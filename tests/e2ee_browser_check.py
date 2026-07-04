#!/usr/bin/env python3
"""Standalone Playwright browser verification for E2EE DMs.

Spins up an isolated uvicorn server against a scratch chat.db, drives two
real headless Chromium browser contexts (Alice, Bob) through the actual
chat UI, and verifies the full E2EE-for-DMs flow end to end: key upload,
DM encryption, UI indicators, replies, link-preview suppression, reporting,
key rotation, and group-room plaintext behavior.

Run directly (NOT collected by pytest -- no test_ prefix):

    python tests/e2ee_browser_check.py

Exits 0 on success, 1 with a failure summary otherwise.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = REPO_ROOT / "server"

# Env vars that must never leak into the scratch server process. Nothing in
# the tested path (key upload, DM send/receive, group room, reporting)
# requires any of these -- OPENAI_API_KEY absent means moderation gracefully
# skips (see chat_moderation.check_openai_moderation/check_content_detection),
# VAPID absent means the push code paths short-circuit before touching
# pywebpush (see chat_ws._push_or_defer and api.py's push scheduler).
SENSITIVE_ENV_KEYS = [
    "OPENAI_API_KEY",
    "MAILEROO_API_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "VAPID_PRIVATE_KEY",
    "VAPID_PUBLIC_KEY",
    "VAPID_CLAIMS_EMAIL",
    "CHAT_ADMIN_EMAILS",
    "CHAT_ADMIN_TOKEN",
]

CHECK_DESCS = {
    1: "Pages load, WS connects, main room visible; e2ee_keys row exists for both users",
    2: "Alice opens DM with Bob, sends message, Bob sees plaintext; Bob replies, Alice sees it",
    3: "DM messages stored as E2EE envelopes; no nonce plaintext anywhere in messages table",
    4: "UI indicators: lock icon in DM header + E2EE banner on both pages",
    5: "Reply gesture (double-click): reply quote rebuilt client-side on both pages; server snippet empty",
    6: "Link message: no link preview rendered on receiver; content stored as envelope",
    7: "Report: reporter-provided plaintext snapshot stored, unverified=1",
    8: "Key rotation: Bob re-keys, Alice re-derives, Bob's old messages now fail to decrypt",
    9: "Group room sanity: plaintext message stored and visible, no E2EE wrapper",
    10: "Zero console errors / page errors across both pages",
}


def log(msg: str) -> None:
    print(f"[E2E] {msg}", flush=True)


class Results:
    def __init__(self):
        self.items = []  # list of (n, desc, passed, detail)
        self.seen = set()

    def record(self, n, desc, passed, detail=""):
        status = "PASS" if passed else "FAIL"
        line = f"[E2E] {n:>2}. [{status}] {desc}"
        if detail:
            line += f" -- {detail}"
        print(line, flush=True)
        self.items.append((n, desc, passed, detail))
        self.seen.add(n)

    def fill_gaps(self, reason):
        for n in sorted(CHECK_DESCS):
            if n not in self.seen:
                self.record(n, CHECK_DESCS[n], False, reason)

    def failures(self):
        return [(n, d, det) for n, d, p, det in self.items if not p]


RESULTS = Results()


def run_check(n, fn):
    desc = CHECK_DESCS[n]
    try:
        detail = fn()
        RESULTS.record(n, desc, True, detail or "")
    except Exception as e:  # noqa: BLE001 - want every failure captured, not crash the run
        RESULTS.record(n, desc, False, f"{type(e).__name__}: {e}")


# --- Small polling / waiting helpers -----------------------------------------


def wait_until(predicate, timeout=15.0, interval=0.25, desc=""):
    deadline = time.monotonic() + timeout
    last_exc = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception as e:  # noqa: BLE001
            last_exc = e
        time.sleep(interval)
    if last_exc:
        raise TimeoutError(f"timed out waiting for: {desc} (last error: {last_exc})")
    raise TimeoutError(f"timed out waiting for: {desc}")


def query_db(db_path, sql, params=()):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def find_msg_id_by_text(page, room_id, needle):
    return page.evaluate(
        """([roomId, needle]) => {
            const msgs = messagesByRoom[roomId] || [];
            for (const m of msgs) {
                try {
                    const c = JSON.parse(m.content);
                    if (c && typeof c.text === 'string' && c.text.indexOf(needle) !== -1) return m.id;
                } catch (e) {}
            }
            return null;
        }""",
        [room_id, needle],
    )


def wait_for_msg_id(page, room_id, needle, timeout=10.0):
    # Optimistic messages carry a temporary 'tmp_*' id until message_acked
    # swaps in the server id; keep polling until the confirmed id appears so
    # later DOM lookups by data-msg-id can't go stale mid-check.
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        mid = find_msg_id_by_text(page, room_id, needle)
        if mid and not str(mid).startswith("tmp_"):
            return mid
        last = mid
        time.sleep(0.2)
    raise TimeoutError(
        f"confirmed message containing {needle!r} not found in room {room_id!r} "
        f"(last seen id: {last!r})"
    )


def click_exact_action(page, text, timeout_ms=10000):
    """Click a .action-sheet-btn whose trimmed text is exactly `text` (avoids
    'Report' matching 'Report & Block')."""
    loc = page.locator("#action-sheet .action-sheet-btn").filter(
        has_text=re.compile(rf"^\s*{re.escape(text)}\s*$")
    )
    loc.first.click(timeout=timeout_ms)


# --- Server lifecycle ---------------------------------------------------------


def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def build_server_env(db_path, base_url):
    env = os.environ.copy()
    for k in SENSITIVE_ENV_KEYS:
        env.pop(k, None)
    env["CHAT_DB_PATH"] = str(db_path)
    env["CHAT_BASE_URL"] = base_url
    env["CHAT_EVENT_ID"] = "stone-techno-2026"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def start_server(db_path, port):
    base_url = f"http://127.0.0.1:{port}"
    env = build_server_env(db_path, base_url)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(SERVER_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log_lines = []

    def _reader():
        for line in proc.stdout:
            log_lines.append(line.rstrip())

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    def _ready():
        if proc.poll() is not None:
            raise RuntimeError(
                "server process exited early:\n" + "\n".join(log_lines[-40:])
            )
        try:
            with urllib.request.urlopen(base_url + "/chat/api/config", timeout=1) as r:
                return r.status == 200
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            return False

    try:
        wait_until(
            _ready, timeout=25.0, interval=0.3, desc=f"server ready at {base_url}"
        )
    except TimeoutError:
        tail = "\n".join(log_lines[-60:])
        stop_server(proc)
        raise RuntimeError(
            f"server did not become ready in time. Last log output:\n{tail}"
        )

    return proc, base_url, log_lines


def stop_server(proc):
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


# --- Scratch user setup --------------------------------------------------------


_AVATAR_WEBP_CACHE = None


def make_avatar_webp():
    # Real users always have a WebP blob in the avatars table (the profile
    # setup makes it mandatory), and /dms rewrites any truthy avatar_url to
    # the /chat/api/avatar/{id} endpoint -- so fake users must have a real
    # served blob too, or every DM row render 404s and pollutes check 10.
    global _AVATAR_WEBP_CACHE
    if _AVATAR_WEBP_CACHE is None:
        import pyvips

        _AVATAR_WEBP_CACHE = pyvips.Image.black(8, 8, bands=3).webpsave_buffer()
    return _AVATAR_WEBP_CACHE


def create_fake_user(chat_db, db, provider_id, display_name, username):
    user = chat_db.create_user(db, "e2ee_test", provider_id, display_name)
    # create_user() only sets display_name; the client's profile-complete
    # gate also requires username/country/avatar_url (see chat.html route():
    # "if (!currentUser.username || !currentUser.country || !currentUser.avatar_url)").
    avatar_url = f"/chat/api/avatar/{user['id']}?v=1"
    db.execute(
        "UPDATE users SET username=?, username_lower=?, country=?, avatar_url=? WHERE id=?",
        (username, username.lower(), "US", avatar_url, user["id"]),
    )
    db.execute(
        "INSERT OR REPLACE INTO avatars (user_id, data) VALUES (?, ?)",
        (user["id"], make_avatar_webp()),
    )
    db.commit()
    session = chat_db.create_session(db, user["id"])
    return {"id": user["id"], "token": session["token"], "display_name": display_name}


def setup_users(db_path):
    sys.path.insert(0, str(SERVER_DIR))
    # chat_db resolves CHAT_DB_PATH from the environment at import time; set it
    # here so a caller that forgot can never make us write to the real dev DB.
    os.environ["CHAT_DB_PATH"] = str(db_path)
    import chat_db  # noqa: E402  (path must be set first)

    if str(chat_db.CHAT_DB_PATH) != str(Path(db_path)):
        raise RuntimeError(
            f"chat_db bound to {chat_db.CHAT_DB_PATH}, expected scratch db {db_path} "
            "(chat_db was imported before CHAT_DB_PATH was set)"
        )
    db = chat_db.get_chat_db()
    nonce = secrets.token_hex(4)
    alice = create_fake_user(
        chat_db, db, f"e2ee-alice-{nonce}", "Alice E2E", f"alice_e2e_{nonce}"
    )
    bob = create_fake_user(
        chat_db, db, f"e2ee-bob-{nonce}", "Bob E2E", f"bob_e2e_{nonce}"
    )
    db.close()
    return alice, bob


def add_session_cookie(context, base_url, token):
    context.add_cookies(
        [
            {
                "name": "chat_session",
                "value": token,
                "url": base_url,
                "httpOnly": False,
                "secure": False,
                "sameSite": "Lax",
            }
        ]
    )


def attach_console_collectors(page, label, console_errors, page_errors):
    def on_console(msg):
        if msg.type == "error":
            console_errors.append(f"[{label}] {msg.text}")

    def on_pageerror(exc):
        page_errors.append(f"[{label}] {exc}")

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)


def attach_ws_frame_collector(page, frames):
    def on_websocket(ws):
        def on_frame(payload):
            frames.append(payload)

        ws.on("framereceived", on_frame)

    page.on("websocket", on_websocket)


# --- Individual checks ---------------------------------------------------------


def check1_pages_load(page_a, page_b, alice, bob, db_path):
    page_a.wait_for_selector("#messages", timeout=20000)
    page_b.wait_for_selector("#messages", timeout=20000)

    for label, page in (("alice", page_a), ("bob", page_b)):
        wait_until(
            lambda page=page: page.evaluate("() => !!ws && ws.readyState === 1"),
            timeout=15.0,
            desc=f"{label} WS open",
        )
        wait_until(
            lambda page=page: (
                (page.text_content(".header .title") or "").strip()
                == "Stone Techno 2026"
            ),
            timeout=10.0,
            desc=f"{label} main room header visible",
        )

    wait_until(
        lambda: (
            len(
                query_db(
                    db_path,
                    "SELECT user_id FROM e2ee_keys WHERE user_id IN (?, ?)",
                    (alice["id"], bob["id"]),
                )
            )
            == 2
        ),
        timeout=15.0,
        desc="e2ee_keys rows for both users",
    )
    return "both pages loaded, WS open, main room visible, both e2ee_keys rows present"


def check2_dm_flow(page_a, page_b, alice, bob, ctx, nonce1, nonce2):
    msg1_text = f"hello from alice {nonce1}"
    msg2_text = f"hello from bob {nonce2}"

    page_a.locator(f'.member-item[data-user-id="{bob["id"]}"]').click(timeout=15000)
    page_a.wait_for_selector("#action-sheet", timeout=10000)
    click_exact_action(page_a, "Send Message")

    # openRoom('pending-dm') re-renders the input bar; typing before that
    # render completes lands in an input element that gets replaced, so the
    # send submits an empty message and the DM is never created. Wait for the
    # pending-DM view to be fully open before touching #msg-input.
    wait_until(
        lambda: page_a.evaluate(
            "() => currentRoom === 'pending-dm' && currentRoomType === 'dm'"
            " && !!document.getElementById('msg-input')"
            " && !document.querySelector('#action-sheet')"
        ),
        timeout=10.0,
        desc="pending DM view open",
    )
    page_a.fill("#msg-input", msg1_text)
    page_a.click(".input-bar .send")

    wait_until(
        lambda: page_a.evaluate("() => currentRoom") not in (None, "pending-dm"),
        timeout=10.0,
        desc="DM room created client-side",
    )
    dm_room_id = page_a.evaluate("() => currentRoom")
    ctx["dm_room_id"] = dm_room_id

    page_a.wait_for_selector(f'.msg-text:has-text("{nonce1}")', timeout=10000)
    msg1_id = wait_for_msg_id(page_a, dm_room_id, nonce1)
    ctx["msg1_id"] = msg1_id
    ctx["msg1_text"] = msg1_text

    # Bob: navigate to the DM via the real sidebar UI (DMs tab -> room item).
    page_b.locator(".tabs button", has_text="DMs").click(timeout=10000)
    page_b.wait_for_selector(
        f'.member-item[data-room-id="{dm_room_id}"]', timeout=10000
    )
    page_b.click(f'.member-item[data-room-id="{dm_room_id}"]')

    page_b.wait_for_selector(f'.msg-text:has-text("{nonce1}")', timeout=10000)
    bob_msg1_id = wait_for_msg_id(page_b, dm_room_id, nonce1)
    if bob_msg1_id != msg1_id:
        raise AssertionError(
            f"message id mismatch between senders: {msg1_id} vs {bob_msg1_id}"
        )

    page_b.fill("#msg-input", msg2_text)
    page_b.click(".input-bar .send")

    page_a.wait_for_selector(f'.msg-text:has-text("{nonce2}")', timeout=10000)
    msg2_id = wait_for_msg_id(page_a, dm_room_id, nonce2)
    ctx["msg2_id"] = msg2_id

    return f"dm_room_id={dm_room_id} msg1_id={msg1_id} msg2_id={msg2_id}"


def check3_db_envelope(db_path, ctx, nonce1, nonce2):
    dm_room_id = ctx.get("dm_room_id")
    if not dm_room_id:
        raise AssertionError("dm_room_id missing (check 2 must have failed)")

    rows = query_db(
        db_path, "SELECT id, content FROM messages WHERE room_id = ?", (dm_room_id,)
    )
    if len(rows) < 2:
        raise AssertionError(f"expected >=2 messages in DM room, found {len(rows)}")
    for r in rows:
        content = json.loads(r["content"])
        if content.get("e2ee") is not True or "ct" not in content:
            raise AssertionError(
                f"message {r['id']} is not an E2EE envelope: {r['content']!r}"
            )

    all_rows = query_db(db_path, "SELECT content FROM messages")
    for r in all_rows:
        if nonce1 in r["content"] or nonce2 in r["content"]:
            raise AssertionError(
                f"plaintext nonce leaked into messages table content: {r['content']!r}"
            )
    return f"{len(rows)} DM messages verified as envelopes, no plaintext nonce leakage anywhere"


def check4_ui_indicators(page_a, page_b, ctx):
    expected_banner = "Messages are end-to-end encrypted. Only you and the other person can read them."
    for label, page in (("alice", page_a), ("bob", page_b)):
        page.wait_for_selector(".header .title .icon-lock", timeout=10000)
        banner_text = page.text_content(".dm-e2ee-banner", timeout=10000)
        if not banner_text or banner_text.strip() != expected_banner:
            raise AssertionError(f"{label} banner mismatch: {banner_text!r}")
    # Sidebar lock (Phase 4.3): Bob's sidebar is on the DMs tab after check 2;
    # the DM row must carry the lock icon. Guards against sidebar-markup
    # rewrites silently dropping the indicator.
    dm_room_id = ctx.get("dm_room_id")
    if dm_room_id:
        page_b.wait_for_selector(
            f'.member-item[data-room-id="{dm_room_id}"] .icon-lock', timeout=10000
        )
    return "lock icon (header + sidebar row) + encrypted-variant banner present"


def check5_reply_gesture(page_a, page_b, ctx, nonce1, nonce_reply):
    dm_room_id = ctx.get("dm_room_id")
    msg1_id = ctx.get("msg1_id")
    if not dm_room_id or not msg1_id:
        raise AssertionError(
            "dm_room_id/msg1_id missing (earlier check must have failed)"
        )

    reply_text = f"bob reply {nonce_reply}"

    page_b.locator(f'.msg[data-msg-id="{msg1_id}"] .msg-other-bubble').dblclick(
        timeout=10000
    )
    page_b.wait_for_selector("#reply-preview.visible", timeout=5000)
    reply_name = (page_b.text_content("#reply-name") or "").strip()
    reply_preview_text = page_b.text_content("#reply-text") or ""
    if reply_name != "Alice E2E":
        raise AssertionError(f"reply preview name mismatch: {reply_name!r}")
    if nonce1 not in reply_preview_text:
        raise AssertionError(
            f"reply preview text missing original nonce: {reply_preview_text!r}"
        )

    page_b.fill("#msg-input", reply_text)
    page_b.click(".input-bar .send")

    page_b.wait_for_selector(f'.msg-text:has-text("{nonce_reply}")', timeout=10000)
    reply_msg_id = wait_for_msg_id(page_b, dm_room_id, nonce_reply)
    ctx["reply_msg_id"] = reply_msg_id

    bob_quote = page_b.text_content(
        f'.msg[data-msg-id="{reply_msg_id}"] .reply-quote-text'
    )
    if not bob_quote or nonce1 not in bob_quote:
        raise AssertionError(
            f"bob's own reply quote missing original text: {bob_quote!r}"
        )

    page_a.wait_for_selector(f'.msg-text:has-text("{nonce_reply}")', timeout=10000)
    alice_quote = page_a.text_content(
        f'.msg[data-msg-id="{reply_msg_id}"] .reply-quote-text'
    )
    if not alice_quote or nonce1 not in alice_quote:
        raise AssertionError(
            f"alice's view of reply quote missing original text: {alice_quote!r}"
        )

    # Best-effort: confirm the server-side reply snippet stayed empty in the
    # raw WS payload Alice received (it cannot read ciphertext, so the quote
    # she sees was rebuilt client-side from her own decrypted history).
    server_snippet_note = "not observed (non-fatal)"
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        for raw in list(ctx.get("ws_frames_a", [])):
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if data.get("event") == "message" and data.get("id") == reply_msg_id:
                reply_to = data.get("reply_to")
                if reply_to is not None:
                    if reply_to.get("text") != "":
                        raise AssertionError(
                            f"server-provided reply_to.text was not empty: {reply_to!r}"
                        )
                    server_snippet_note = "confirmed empty in raw WS payload"
                break
        else:
            time.sleep(0.2)
            continue
        break

    return f"reply_msg_id={reply_msg_id}; server snippet: {server_snippet_note}"


def check6_link_preview_suppressed(page_a, page_b, db_path, ctx, nonce3):
    dm_room_id = ctx.get("dm_room_id")
    if not dm_room_id:
        raise AssertionError("dm_room_id missing (earlier check must have failed)")

    link_text = f"check https://example.com {nonce3}"
    page_a.fill("#msg-input", link_text)
    page_a.click(".input-bar .send")

    page_b.wait_for_selector(f'.msg-text:has-text("{nonce3}")', timeout=10000)
    link_msg_id = wait_for_msg_id(page_b, dm_room_id, nonce3)
    ctx["link_msg_id"] = link_msg_id

    preview_count = page_b.locator(
        f'.msg[data-msg-id="{link_msg_id}"] .msg-link-preview'
    ).count()
    if preview_count != 0:
        raise AssertionError(
            f"unexpected link preview rendered for E2EE message ({preview_count})"
        )

    rows = query_db(
        db_path, "SELECT content FROM messages WHERE id = ?", (link_msg_id,)
    )
    if not rows:
        raise AssertionError(f"message {link_msg_id} not found in DB")
    content = json.loads(rows[0]["content"])
    if content.get("e2ee") is not True:
        raise AssertionError(
            f"link message not stored as envelope: {rows[0]['content']!r}"
        )

    return f"link_msg_id={link_msg_id}, no preview rendered, content is envelope"


def check7_report(page_b, db_path, alice, bob, ctx, nonce3):
    link_msg_id = ctx.get("link_msg_id")
    if not link_msg_id:
        raise AssertionError("link_msg_id missing (check 6 must have failed)")

    page_b.locator(f'.msg[data-msg-id="{link_msg_id}"] .msg-other-bubble').click(
        button="right", timeout=10000
    )
    page_b.wait_for_selector("#action-sheet", timeout=10000)
    click_exact_action(page_b, "Report")
    page_b.wait_for_selector(
        '.action-sheet-title:has-text("Report this message?")', timeout=10000
    )
    click_exact_action(page_b, "Report")

    def _report_row():
        rows = query_db(
            db_path,
            "SELECT * FROM reports WHERE reported_user_id = ? ORDER BY created_at DESC LIMIT 1",
            (alice["id"],),
        )
        return rows[0] if rows else None

    wait_until(
        lambda: _report_row() is not None, timeout=10.0, desc="report row created"
    )
    row = _report_row()

    if row["reporter_id"] != bob["id"]:
        raise AssertionError(f"reporter_id mismatch: {row['reporter_id']}")
    if not row["unverified"]:
        raise AssertionError(f"expected unverified=1, got {row['unverified']}")
    if nonce3 not in row["message_snapshot"]:
        raise AssertionError(
            f"snapshot missing reported plaintext: {row['message_snapshot']!r}"
        )

    return f"report id={row['id']} unverified=1, snapshot contains reporter-provided plaintext"


def check8_rekey(page_a, page_b, db_path, bob, ctx, nonce4):
    dm_room_id = ctx.get("dm_room_id")
    msg1_id = ctx.get("msg1_id")
    if not dm_room_id or not msg1_id:
        raise AssertionError(
            "dm_room_id/msg1_id missing (earlier check must have failed)"
        )

    old_key_rows = query_db(
        db_path, "SELECT public_key FROM e2ee_keys WHERE user_id = ?", (bob["id"],)
    )
    old_key = old_key_rows[0]["public_key"] if old_key_rows else None

    page_b.evaluate("() => localStorage.removeItem('e2ee_keypair')")
    page_b.reload(timeout=30000)
    page_b.wait_for_selector("#messages", timeout=20000)

    def _key_changed():
        rows = query_db(
            db_path, "SELECT public_key FROM e2ee_keys WHERE user_id = ?", (bob["id"],)
        )
        return bool(rows) and rows[0]["public_key"] != old_key

    wait_until(_key_changed, timeout=15.0, desc="bob's e2ee_keys row to change")

    # Small, explicitly-justified settle: the server fires key_rotated via an
    # asyncio.create_task during the PUT /keys request that stored the new
    # key; give it a moment to reach Alice's already-open WS connection.
    time.sleep(1.0)

    post_rekey_text = f"post-rekey {nonce4}"
    page_a.fill("#msg-input", post_rekey_text)
    page_a.click(".input-bar .send")

    # Bob re-opens the DM (fresh page state after reload) via the real UI.
    page_b.locator(".tabs button", has_text="DMs").click(timeout=10000)
    page_b.wait_for_selector(
        f'.member-item[data-room-id="{dm_room_id}"]', timeout=10000
    )
    page_b.click(f'.member-item[data-room-id="{dm_room_id}"]')

    page_b.wait_for_selector(f'.msg-text:has-text("{nonce4}")', timeout=10000)
    new_msg_id = wait_for_msg_id(page_b, dm_room_id, nonce4)
    failed_new = page_b.locator(
        f'.msg[data-msg-id="{new_msg_id}"] .msg-text.msg-decrypt-failed'
    ).count()
    if failed_new != 0:
        raise AssertionError("post-rekey message failed to decrypt for bob")

    wait_until(
        lambda: (
            page_b.locator(
                f'.msg[data-msg-id="{msg1_id}"] .msg-text.msg-decrypt-failed'
            ).count()
            == 1
        ),
        timeout=10.0,
        desc="bob's view of msg1 to show decrypt-failed styling",
    )
    old_text = page_b.text_content(f'.msg[data-msg-id="{msg1_id}"] .msg-text')
    if not old_text or "[Encrypted message]" not in old_text:
        raise AssertionError(
            f"old message did not render decrypt-failure sentinel: {old_text!r}"
        )

    return "bob re-keyed, alice re-derived, post-rekey message decrypts, old message now fails"


def check9_group_room(page_a, page_b, db_path, nonce5):
    group_text = f"group plaintext {nonce5}"

    page_a.locator(".tabs button", has_text="Rooms").click(timeout=10000)
    page_a.wait_for_selector('.room-item[data-room-id="general"]', timeout=10000)
    page_a.click('.room-item[data-room-id="general"]')
    page_a.wait_for_selector("#msg-input", timeout=10000)

    page_a.fill("#msg-input", group_text)
    page_a.click(".input-bar .send")
    page_a.wait_for_selector(f'.msg-text:has-text("{nonce5}")', timeout=10000)

    def _group_row():
        rows = query_db(
            db_path,
            "SELECT content FROM messages WHERE room_id = 'general' ORDER BY created_at DESC LIMIT 5",
        )
        for r in rows:
            if nonce5 in r["content"]:
                return r
        return None

    wait_until(
        lambda: _group_row() is not None, timeout=10.0, desc="group message row in DB"
    )
    row = _group_row()
    content = json.loads(row["content"])
    if content.get("e2ee"):
        raise AssertionError(
            f"group room message unexpectedly wrapped as E2EE: {row['content']!r}"
        )

    page_b.locator(".tabs button", has_text="Rooms").click(timeout=10000)
    page_b.wait_for_selector('.room-item[data-room-id="general"]', timeout=10000)
    page_b.click('.room-item[data-room-id="general"]')
    page_b.wait_for_selector(f'.msg-text:has-text("{nonce5}")', timeout=10000)

    return "group message stored as plaintext, visible to bob"


def check10_console_sweep(
    console_errors_a, console_errors_b, page_errors_a, page_errors_b, allowlist
):
    log(
        "console allowlist: "
        + (", ".join(p for p, _ in allowlist) if allowlist else "(none)")
    )
    for pattern, reason in allowlist:
        log(f"  allowlisted: {pattern!r} -- {reason}")

    def _filter(entries):
        if not allowlist:
            return entries
        out = []
        for e in entries:
            if any(re.search(p, e) for p, _ in allowlist):
                continue
            out.append(e)
        return out

    remaining = (
        _filter(console_errors_a)
        + _filter(console_errors_b)
        + _filter(page_errors_a)
        + _filter(page_errors_b)
    )
    if remaining:
        raise AssertionError("; ".join(remaining[:5]))
    return (
        f"0 console errors / page errors "
        f"(raw counts: console_a={len(console_errors_a)} console_b={len(console_errors_b)} "
        f"pageerror_a={len(page_errors_a)} pageerror_b={len(page_errors_b)})"
    )


# --- Main ------------------------------------------------------------------


def main():
    tmp_dir = tempfile.mkdtemp(prefix="e2ee_check_")
    db_path = Path(tmp_dir) / "chat.db"
    proc = None

    try:
        os.environ["CHAT_DB_PATH"] = str(db_path)
        alice, bob = setup_users(db_path)
        log(
            f"created scratch users alice={alice['id'][:8]} bob={bob['id'][:8]} db={db_path}"
        )

        port = get_free_port()
        proc, base_url, _server_logs = start_server(db_path, port)
        log(f"server ready at {base_url}")

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            RESULTS.fill_gaps(f"playwright not importable: {e}")
            return

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx_a = browser.new_context(viewport={"width": 1280, "height": 900})
                ctx_b = browser.new_context(viewport={"width": 1280, "height": 900})
                add_session_cookie(ctx_a, base_url, alice["token"])
                add_session_cookie(ctx_b, base_url, bob["token"])
                page_a = ctx_a.new_page()
                page_b = ctx_b.new_page()

                console_errors_a, console_errors_b = [], []
                page_errors_a, page_errors_b = [], []
                ws_frames_a = []
                attach_console_collectors(
                    page_a, "alice", console_errors_a, page_errors_a
                )
                attach_console_collectors(
                    page_b, "bob", console_errors_b, page_errors_b
                )
                attach_ws_frame_collector(page_a, ws_frames_a)

                page_a.goto(base_url + "/chat", timeout=30000)
                page_b.goto(base_url + "/chat", timeout=30000)

                ctx = {"ws_frames_a": ws_frames_a}
                nonce1 = secrets.token_hex(4)
                nonce2 = secrets.token_hex(4)
                nonce_reply = secrets.token_hex(4)
                nonce3 = secrets.token_hex(4)
                nonce4 = secrets.token_hex(4)
                nonce5 = secrets.token_hex(4)

                # No allowlist entries: all static assets referenced by
                # chat.html (favicon.svg, manifest.json, shared.css/js,
                # sw.js) are served by explicit routes in api.py from files
                # that already exist in server/static/, the avatar is a
                # data: URI (no network fetch), and push permission is never
                # auto-requested (route() only checks an existing
                # subscription, never calls Notification.requestPermission).
                # verify()/console.error is the app's own built-in assertion
                # helper (server/static/shared.js) -- any genuine internal
                # invariant violation during the run must surface as a real
                # failure here, not be suppressed.
                console_allowlist = []

                run_check(
                    1, lambda: check1_pages_load(page_a, page_b, alice, bob, db_path)
                )
                run_check(
                    2,
                    lambda: check2_dm_flow(
                        page_a, page_b, alice, bob, ctx, nonce1, nonce2
                    ),
                )
                run_check(3, lambda: check3_db_envelope(db_path, ctx, nonce1, nonce2))
                run_check(4, lambda: check4_ui_indicators(page_a, page_b, ctx))
                run_check(
                    5,
                    lambda: check5_reply_gesture(
                        page_a, page_b, ctx, nonce1, nonce_reply
                    ),
                )
                run_check(
                    6,
                    lambda: check6_link_preview_suppressed(
                        page_a, page_b, db_path, ctx, nonce3
                    ),
                )
                run_check(
                    7, lambda: check7_report(page_b, db_path, alice, bob, ctx, nonce3)
                )
                run_check(
                    8, lambda: check8_rekey(page_a, page_b, db_path, bob, ctx, nonce4)
                )
                run_check(9, lambda: check9_group_room(page_a, page_b, db_path, nonce5))
                run_check(
                    10,
                    lambda: check10_console_sweep(
                        console_errors_a,
                        console_errors_b,
                        page_errors_a,
                        page_errors_b,
                        console_allowlist,
                    ),
                )
            finally:
                browser.close()
    finally:
        stop_server(proc)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    RESULTS.fill_gaps("not run: earlier fatal error aborted the run")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - guarantee a clear summary + exit code either way
        log(f"FATAL: {type(e).__name__}: {e}")
        RESULTS.fill_gaps(f"fatal error during setup/execution: {e}")

    failures = RESULTS.failures()
    if failures:
        print("\n[E2E] FAILURES:", flush=True)
        for n, d, det in failures:
            print(f"[E2E]   {n}. {d}: {det}", flush=True)
        sys.exit(1)
    else:
        print("\n[E2E] All checks passed.", flush=True)
        sys.exit(0)
