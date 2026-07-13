#!/usr/bin/env python3
"""Standalone Playwright browser verification for notification and badge fixes.

Spins up an isolated uvicorn server against a scratch chat.db (env
CHAT_DB_PATH), creates fake users, and drives headless Chromium browser
contexts to verify four fixes applied on branch chat-prototype:

  Fix 1: loadRooms() merges instead of replacing roomTypeLookup -- DM unread
          state set by badge_counts on WS connect is preserved.
  Fix 2: refreshAllBadges() selector now includes .member-item[data-room-id]
          (DM rows in the DMs tab sidebar).
  Fix 3: push payload includes push_id (unique per push); SW_VERSION = 'v10';
          sw.js tag prefers data.push_id.
  Fix 4: _subscribePush() returns true only on real success; failure shows
          truthful toast; push_enabled flag gates the repair path.

Run directly (NOT collected by pytest -- no test_ prefix):

    python tests/notif_badge_browser_check.py

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
SERVER_DIR = REPO_ROOT / "services" / "companion"

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

# Notification spy: headless Chromium reports permission='denied' regardless
# of context.grant_permissions; the spy overrides Notification so the app's
# Notification.permission check always sees 'granted' and constructor calls
# are recorded instead of silently dropped.
NOTIFICATION_SPY_SCRIPT = """
(() => {
  window.__notifications = [];
  function FakeNotification(title, options) {
    window.__notifications.push({ title, body: options && options.body });
    this.onclick = null;
  }
  FakeNotification.prototype.close = function () {};
  Object.defineProperty(FakeNotification, 'permission', {
    get: () => 'granted',
  });
  FakeNotification.requestPermission = () => Promise.resolve('granted');
  window.Notification = FakeNotification;
})();
"""

# Force PushManager.subscribe to reject immediately so _subscribePush()
# fails even if the vapid-key fetch somehow succeeded.
PUSH_SUBSCRIBE_REJECT_SCRIPT = """
(() => {
  if (typeof PushManager !== 'undefined') {
    PushManager.prototype.subscribe = function () {
      return Promise.reject(new Error('push service unavailable (test override)'));
    };
  }
})();
"""

# Records every fetch() call's URL into window.__fetchRequests.
# Added to a page via page.add_init_script() before a reload so it persists
# across navigations without affecting other contexts.
FETCH_TRACKER_SCRIPT = """
(() => {
  window.__fetchRequests = [];
  var _origFetch = window.fetch;
  window.fetch = function (input, init) {
    try {
      var u = typeof input === 'string' ? input
            : (input && typeof input === 'object' && 'url' in input) ? input.url
            : String(input);
      window.__fetchRequests.push(u);
    } catch (e) {}
    return _origFetch.apply(this, arguments);
  };
})();
"""

CHECK_DESCS = {
    1: "Cold-start DM badge survives loadRooms: unreadByRoom[dmId]>=1 and roomTypeLookup[dmId].type==='dm'",
    2: "DMs tab dot and DM row .unread-badge visible after cold start",
    3: "refreshAllBadges repairs DM rows: .member-item[data-room-id] badge text updates",
    4: "push_id in chat_ws.py payload; sw.js SW_VERSION='v10' and uses data.push_id",
    5: "enable-notifications failure: toast contains 'Push registration failed'; push_enabled unset; _pushSubscribed=false",
    6: "Repair gated: no vapid-key fetch without push_enabled; vapid-key fetched when push_enabled='1'",
    7: "Zero page errors across all contexts",
}


def log(msg: str) -> None:
    print(f"[NBC] {msg}", flush=True)


class Results:
    def __init__(self):
        self.items = []
        self.seen = set()

    def record(self, n, desc, passed, detail=""):
        status = "PASS" if passed else "FAIL"
        line = f"[NBC] {n:>2}. [{status}] {desc}"
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
    except Exception as e:  # noqa: BLE001
        RESULTS.record(n, desc, False, f"{type(e).__name__}: {e}")


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


_AVATAR_WEBP_CACHE = None


def make_avatar_webp():
    global _AVATAR_WEBP_CACHE
    if _AVATAR_WEBP_CACHE is None:
        import pyvips

        _AVATAR_WEBP_CACHE = pyvips.Image.black(8, 8, bands=3).webpsave_buffer()
    return _AVATAR_WEBP_CACHE


def create_fake_user(chat_db, db, provider_id, display_name, username):
    user = chat_db.create_user(db, "nbc_test", provider_id, display_name)
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
    os.environ["CHAT_DB_PATH"] = str(db_path)
    import chat_db  # noqa: E402

    if str(chat_db.CHAT_DB_PATH) != str(Path(db_path)):
        raise RuntimeError(
            f"chat_db bound to {chat_db.CHAT_DB_PATH}, expected {db_path}"
        )
    db = chat_db.get_chat_db()
    nonce = secrets.token_hex(4)
    alice = create_fake_user(
        chat_db, db, f"nbc-alice-{nonce}", "Alice NBC", f"alice_nbc_{nonce}"
    )
    bob = create_fake_user(
        chat_db, db, f"nbc-bob-{nonce}", "Bob NBC", f"bob_nbc_{nonce}"
    )
    # Create DM room directly (Bob never online, simulating cold start):
    # find_or_create_dm adds both users to dm_participants; get_unread_counts
    # union-queries dm_participants so Bob's session will receive badge_counts
    # with the DM room when he connects via WS.
    dm_room_id = chat_db.find_or_create_dm(
        db, "stone-techno-2026", alice["id"], bob["id"]
    )
    chat_db.create_message(
        db,
        dm_room_id,
        alice["id"],
        "text",
        json.dumps({"text": "hello bob from alice nbc"}),
    )
    db.close()
    log(
        f"users: alice={alice['id'][:8]} bob={bob['id'][:8]} "
        f"dm_room_id={dm_room_id} db={db_path}"
    )
    return alice, bob, dm_room_id


def add_session_cookie(context, base_url, token):
    # Onboarded-user harness: suppress the first-run notification banner so it
    # never overlaps the flows under test (this harness verifies E2EE/badges,
    # not onboarding). Mirrors how it strips push/VAPID concerns.
    context.add_init_script(
        "() => { try { localStorage.setItem('notif_prompt_done', '1'); } catch (e) {} }"
    )
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
            loc = ""
            try:
                loc = (msg.location or {}).get("url", "")
            except Exception:  # noqa: BLE001
                pass
            console_errors.append(
                f"[{label}] {msg.text}" + (f" ({loc})" if loc else "")
            )

    def on_pageerror(exc):
        page_errors.append(f"[{label}] {exc}")

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check1_cold_start_dm_badge(page, dm_room_id):
    """Fix 1: badge_counts sets unreadByRoom[dm] and roomTypeLookup[dm].type='dm';
    loadRooms() must not wipe these entries."""
    # Wait for routing: the main room opens and #messages appears (desktop viewport,
    # route() auto-opens the main room when rooms.length > 0).
    page.wait_for_selector("#messages", timeout=20000)
    # Ensure WS is open (badge_counts has been received by now).
    wait_until(
        lambda: page.evaluate("() => !!ws && ws.readyState === 1"),
        timeout=15.0,
        desc="bob cold-start WS open",
    )
    # Wait until rooms are loaded (guarantees loadRooms() has returned).
    wait_until(
        lambda: page.evaluate(
            "() => typeof rooms !== 'undefined' && Array.isArray(rooms) && rooms.length > 0"
        ),
        timeout=10.0,
        desc="loadRooms returned with rooms",
    )
    state = page.evaluate(
        """(dmId) => ({
          unread: unreadByRoom[dmId],
          typeEntry: roomTypeLookup[dmId],
        })""",
        dm_room_id,
    )
    unread = state.get("unread")
    type_entry = state.get("typeEntry")
    if not unread or unread < 1:
        raise AssertionError(
            f"unreadByRoom[{dm_room_id!r}] = {unread!r} (expected >= 1 after cold start)"
        )
    if not type_entry or type_entry.get("type") != "dm":
        raise AssertionError(
            f"roomTypeLookup[{dm_room_id!r}] = {type_entry!r} (expected type='dm')"
        )
    return f"unreadByRoom[dm]={unread} roomTypeLookup[dm].type='dm' -- both survived loadRooms"


def check2_tab_and_row_badge(page, dm_room_id):
    """Fix 1+2: DMs tab dot is active; DM row renders an .unread-badge."""
    # The tab dot reflects unread state from updateTabBadges (called by refreshAllBadges
    # and by badge_counts handler).  Wait for it to appear.
    wait_until(
        lambda: page.locator(".tabs button:nth-child(3) .tab-dot.active").count() == 1,
        timeout=10.0,
        desc="DMs tab dot active",
    )
    # Open the DMs tab to render DM rows.
    page.locator(".tabs button", has_text="DMs").click(timeout=10000)
    # Wait for the DM row to render.
    page.wait_for_selector(f'.member-item[data-room-id="{dm_room_id}"]', timeout=10000)
    # Assert the row has an unread badge.
    badge_count = page.locator(
        f'.member-item[data-room-id="{dm_room_id}"] .unread-badge'
    ).count()
    if badge_count != 1:
        raise AssertionError(
            f"DM row has {badge_count} .unread-badge element(s), expected 1"
        )
    badge_text = page.locator(
        f'.member-item[data-room-id="{dm_room_id}"] .unread-badge'
    ).text_content()
    return f"DMs tab dot active; DM row .unread-badge visible (text={badge_text!r})"


def check3_refresh_all_badges_dm_rows(page, dm_room_id):
    """Fix 2: refreshAllBadges() selector includes .member-item[data-room-id],
    so mutating unreadByRoom and calling refreshAllBadges() updates DM row badges."""
    # DMs tab is already open from check 2.
    # Read current badge text.
    current_text = page.locator(
        f'.member-item[data-room-id="{dm_room_id}"] .unread-badge'
    ).text_content()
    try:
        current_count = int(current_text)
    except (ValueError, TypeError):
        current_count = 1
    new_count = current_count + 5
    # Mutate unreadByRoom and call refreshAllBadges().
    page.evaluate(
        """([dmId, n]) => {
          unreadByRoom[dmId] = n;
          refreshAllBadges();
        }""",
        [dm_room_id, new_count],
    )
    # Assert the DOM badge reflects the new count.
    wait_until(
        lambda: (
            page.locator(
                f'.member-item[data-room-id="{dm_room_id}"] .unread-badge'
            ).text_content()
            == str(new_count)
        ),
        timeout=5.0,
        desc=f"DM row badge updated to {new_count}",
    )
    return (
        f"refreshAllBadges() updated .member-item .unread-badge "
        f"from {current_count} to {new_count}"
    )


def check4_push_id_static(base_url, chat_ws_path):
    """Fix 3: chat_ws.py payload includes push_id; sw.js uses data.push_id and SW_VERSION='v10'."""
    # Assert sw.js source -- fetch from the scratch server.
    try:
        with urllib.request.urlopen(base_url + "/sw.js", timeout=5) as r:
            sw_content = r.read().decode("utf-8")
    except Exception as e:
        raise AssertionError(f"failed to fetch /sw.js: {e}")

    if "SW_VERSION = 'v10'" not in sw_content:
        raise AssertionError(
            "sw.js does not contain SW_VERSION = 'v10' -- found: "
            + (
                [l for l in sw_content.splitlines() if "SW_VERSION" in l]
                or ["(not found)"]
            )[0]
        )
    if "data.push_id" not in sw_content:
        raise AssertionError(
            "sw.js does not reference data.push_id -- tag uniqueness fix missing"
        )

    # Assert chat_ws.py source -- grep the file directly.
    ws_source = Path(chat_ws_path).read_text(encoding="utf-8")
    if '"push_id": secrets.token_hex(8)' not in ws_source:
        raise AssertionError(
            'chat_ws.py does not contain "push_id": secrets.token_hex(8)'
        )

    return (
        "sw.js: SW_VERSION='v10' and data.push_id present; "
        'chat_ws.py: "push_id": secrets.token_hex(8) found'
    )


def check5_enable_notif_failure(page):
    """Fix 4: when _subscribePush() returns false, the toast says
    'Push registration failed' and push_enabled is NOT set."""
    # The Notification spy + PushManager.subscribe override are installed as
    # context init scripts -- they run before any page JS.
    # In the scratch server, /push/vapid-key returns 501 (no VAPID keys),
    # which causes api() to throw inside _subscribePush(), returning false.
    # The PushManager override is belt-and-suspenders for the case where
    # vapid-key somehow succeeded.

    # Patch showToast to capture the message.
    page.evaluate("""() => {
      window._lastToast = '';
      var orig = window.showToast;
      window.showToast = function (msg) {
        window._lastToast = String(msg);
        if (orig) orig.apply(this, arguments);
      };
    }""")

    # Call _enableAllNotifications() -- Playwright awaits the Promise.
    page.evaluate("() => _enableAllNotifications()")

    last_toast = page.evaluate("() => window._lastToast")
    push_enabled = page.evaluate("() => localStorage.getItem('push_enabled')")
    push_subscribed = page.evaluate("() => _pushSubscribed")

    if "Push registration failed" not in (last_toast or ""):
        raise AssertionError(
            f"expected toast to contain 'Push registration failed', got: {last_toast!r}"
        )
    if push_enabled == "1":
        raise AssertionError(
            "push_enabled was set to '1' even though subscription failed"
        )
    if push_subscribed:
        raise AssertionError(
            f"_pushSubscribed is {push_subscribed!r} (expected false/falsy) after failed subscribe"
        )

    return f"toast={last_toast[:60]!r}; push_enabled={push_enabled!r}; _pushSubscribed={push_subscribed!r}"


def check6_repair_gated(page):
    """Fix 4: _repairPushSubscription runs only when push_enabled==='1';
    reload without flag -> no vapid-key fetch; reload with flag -> vapid-key fetched."""
    # push_enabled is unset (check 5 verified this). Install fetch tracker on
    # the page so it survives the reload and records all fetch() calls.
    page.add_init_script(FETCH_TRACKER_SCRIPT)

    # --- Reload 1: no push_enabled ---
    page.reload(timeout=30000)
    page.wait_for_selector("#messages", timeout=20000)
    wait_until(
        lambda: page.evaluate("() => !_routing"),
        timeout=15.0,
        desc="routing complete (reload 1)",
    )
    # Give _repairPushSubscription a moment to run (it awaits _checkPushStatus first).
    time.sleep(1.5)

    reqs_1 = page.evaluate("() => window.__fetchRequests || []")
    vapid_reqs_1 = [u for u in reqs_1 if "/push/vapid-key" in u]
    if vapid_reqs_1:
        raise AssertionError(
            f"repair ran without push_enabled -- vapid-key fetched: {vapid_reqs_1}"
        )

    # --- Set push_enabled and reload ---
    page.evaluate("() => localStorage.setItem('push_enabled', '1')")

    # Reload 2: push_enabled='1', Notification.permission='granted' (spy), no subscription.
    # _repairPushSubscription should call _subscribePush() -> fetch vapid-key -> 501 -> fail.
    page.reload(timeout=30000)
    page.wait_for_selector("#messages", timeout=20000)
    wait_until(
        lambda: page.evaluate("() => !_routing"),
        timeout=15.0,
        desc="routing complete (reload 2)",
    )
    # Allow time for async repair to attempt the vapid-key fetch.
    time.sleep(2.0)

    reqs_2 = page.evaluate("() => window.__fetchRequests || []")
    vapid_reqs_2 = [u for u in reqs_2 if "/push/vapid-key" in u]
    if not vapid_reqs_2:
        raise AssertionError(
            f"repair did not attempt vapid-key fetch even with push_enabled='1'. "
            f"All fetches: {reqs_2}"
        )

    # Page must not crash (page error collector for this context will catch it).
    return (
        f"reload-1 (no flag): no vapid-key fetch ({len(reqs_1)} total); "
        f"reload-2 (flag set): vapid-key fetched ({vapid_reqs_2[0]!r}), page survived"
    )


def check7_zero_errors(
    console_errors_cold,
    page_errors_cold,
    console_errors_notif,
    page_errors_notif,
    allowlist,
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
        _filter(console_errors_cold)
        + _filter(console_errors_notif)
        + _filter(page_errors_cold)
        + _filter(page_errors_notif)
    )
    if remaining:
        raise AssertionError("; ".join(remaining[:5]))
    return (
        f"0 errors "
        f"(console_cold={len(console_errors_cold)} console_notif={len(console_errors_notif)} "
        f"pageerr_cold={len(page_errors_cold)} pageerr_notif={len(page_errors_notif)})"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    tmp_dir = tempfile.mkdtemp(prefix="nbc_check_")
    db_path = Path(tmp_dir) / "chat.db"
    proc = None

    try:
        os.environ["CHAT_DB_PATH"] = str(db_path)
        alice, bob, dm_room_id = setup_users(db_path)

        port = get_free_port()
        proc, base_url, _server_logs = start_server(db_path, port)
        log(f"server ready at {base_url}")

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            RESULTS.fill_gaps(f"playwright not importable: {e}")
            return

        chat_ws_path = str(SERVER_DIR / "chat_ws.py")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                # --- Context 1: Bob cold-start (checks 1-3) ---
                log("launching bob cold-start context")
                ctx_cold = browser.new_context(viewport={"width": 1280, "height": 900})
                # Notification spy prevents any browser-level permission dialogs.
                ctx_cold.add_init_script(NOTIFICATION_SPY_SCRIPT)
                add_session_cookie(ctx_cold, base_url, bob["token"])
                page_cold = ctx_cold.new_page()

                console_errors_cold, page_errors_cold = [], []
                attach_console_collectors(
                    page_cold, "bob_cold", console_errors_cold, page_errors_cold
                )

                page_cold.goto(base_url + "/chat", timeout=30000)

                run_check(1, lambda: check1_cold_start_dm_badge(page_cold, dm_room_id))
                run_check(2, lambda: check2_tab_and_row_badge(page_cold, dm_room_id))
                run_check(
                    3, lambda: check3_refresh_all_badges_dm_rows(page_cold, dm_room_id)
                )

                page_cold.close()
                ctx_cold.close()
                log("cold-start context closed")

                # --- Check 4: static file assertions (no browser needed) ---
                run_check(4, lambda: check4_push_id_static(base_url, chat_ws_path))

                # --- Context 2: Bob notification-failure context (checks 5-6) ---
                log("launching bob notification-failure context")
                ctx_notif = browser.new_context(viewport={"width": 1280, "height": 900})
                # Notification spy: requestPermission always resolves 'granted'.
                ctx_notif.add_init_script(NOTIFICATION_SPY_SCRIPT)
                # PushManager.subscribe rejects: belt-and-suspenders in case vapid-key
                # fetch somehow succeeded (scratch server has no VAPID keys -> 501).
                ctx_notif.add_init_script(PUSH_SUBSCRIBE_REJECT_SCRIPT)
                add_session_cookie(ctx_notif, base_url, bob["token"])
                page_notif = ctx_notif.new_page()

                console_errors_notif, page_errors_notif = [], []
                attach_console_collectors(
                    page_notif, "bob_notif", console_errors_notif, page_errors_notif
                )

                page_notif.goto(base_url + "/chat", timeout=30000)
                page_notif.wait_for_selector("#messages", timeout=20000)
                wait_until(
                    lambda: page_notif.evaluate("() => !_routing"),
                    timeout=15.0,
                    desc="notif context routing complete",
                )

                run_check(5, lambda: check5_enable_notif_failure(page_notif))
                run_check(6, lambda: check6_repair_gated(page_notif))

                page_notif.close()
                ctx_notif.close()
                log("notification-failure context closed")

            finally:
                browser.close()

        # Console allowlist:
        # - /chat/api/keys/ 404: E2EE keyless-peer lookup design (no E2EE in this run,
        #   but E2EE.init() runs and may probe for peer keys).
        # - /chat/api/push/vapid-key 501: expected in check 6's second reload (scratch
        #   server has no VAPID key; repair was MEANT to attempt this).
        console_allowlist = [
            (
                r"the server responded with a status of 404 .*/chat/api/keys/",
                "keyless-peer E2EE key lookup 404s by design",
            ),
            (
                r"the server responded with a status of 501 .*/chat/api/push/vapid-key",
                "scratch server has no VAPID key; 501 is the expected repair-failure signal",
            ),
        ]

        run_check(
            7,
            lambda: check7_zero_errors(
                console_errors_cold,
                page_errors_cold,
                console_errors_notif,
                page_errors_notif,
                console_allowlist,
            ),
        )

    finally:
        stop_server(proc)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    RESULTS.fill_gaps("not run: earlier fatal error aborted the run")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log(f"FATAL: {type(e).__name__}: {e}")
        RESULTS.fill_gaps(f"fatal error during setup/execution: {e}")

    failures = RESULTS.failures()
    if failures:
        print("\n[NBC] FAILURES:", flush=True)
        for n, d, det in failures:
            print(f"[NBC]   {n}. {d}: {det}", flush=True)
        sys.exit(1)
    else:
        print("\n[NBC] All checks passed.", flush=True)
        sys.exit(0)
