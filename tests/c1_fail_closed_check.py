#!/usr/bin/env python3
"""Standalone browser check for the C1 fix (fail closed when E2EE is
unavailable in an already-encrypted DM).

Reuses the isolated-server + scratch-DB + Playwright scaffolding from
e2ee_browser_check.py. NOT part of the pytest suite; must run OUTSIDE the
command sandbox (headless Chromium needs Mach-port access):

    python tests/c1_fail_closed_check.py

Scenario:
  1. Alice opens a DM with Bob and sends a message -> the room becomes
     encrypted (envelope stored, _roomEncrypted has the room).
  2. Force E2EE.available = false on Alice's page (simulates crypto init
     failure / exhausted key-upload retries) and try to send again.
     EXPECT: send blocked, "Encryption unavailable" toast, and NO row for
     that message in the DB (in particular no plaintext leak). This is the
     regression the fix closes: pre-fix the message went out as plaintext.
  3. Control: in a never-encrypted (keyless-peer) DM with E2EE.available
     still false, a send must STILL fall back to plaintext -- proving the
     fix blocks only previously-encrypted rooms, not all sends.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2ee_browser_check as h  # noqa: E402  reuse the harness helpers


def _room_has_text_in_db(db_path, room_id, needle):
    rows = h.query_db(
        db_path, "SELECT content FROM messages WHERE room_id = ?", (room_id,)
    )
    return any(needle in r["content"] for r in rows)


def _room_has_plaintext_in_db(db_path, room_id, needle):
    rows = h.query_db(
        db_path, "SELECT content FROM messages WHERE room_id = ?", (room_id,)
    )
    return any(needle in r["content"] and '"e2ee"' not in r["content"] for r in rows)


def main() -> int:
    tmp_dir = tempfile.mkdtemp(prefix="c1_check_")
    db_path = Path(tmp_dir) / "chat.db"
    proc = None
    failures = []

    try:
        import os

        os.environ["CHAT_DB_PATH"] = str(db_path)
        alice, bob, carol, carol_dm_id = h.setup_users(db_path)
        h.log(
            f"scratch users alice={alice['id'][:8]} bob={bob['id'][:8]} "
            f"carol={carol['id'][:8]} (keyless) db={db_path}"
        )

        port = h.get_free_port()
        proc, base_url, _logs = h.start_server(db_path, port)
        h.log(f"server ready at {base_url}")

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx_a = browser.new_context(viewport={"width": 1280, "height": 900})
                ctx_b = browser.new_context(viewport={"width": 1280, "height": 900})
                h.add_session_cookie(ctx_a, base_url, alice["token"])
                h.add_session_cookie(ctx_b, base_url, bob["token"])
                page_a = ctx_a.new_page()
                page_b = ctx_b.new_page()

                for pg, label in ((page_a, "alice"), (page_b, "bob")):
                    pg.goto(base_url + "/chat", timeout=30000)
                    pg.wait_for_selector("#messages", timeout=20000)
                    h.wait_until(
                        lambda pg=pg: pg.evaluate(
                            "() => !!ws && ws.readyState === 1"
                        ),
                        timeout=15.0,
                        desc=f"{label} WS open",
                    )
                # Both devices must have uploaded their E2EE key before the
                # first send, or encryption falls back to plaintext for the
                # wrong reason (keyless peer, not the path under test).
                for pg, u, label in (
                    (page_a, alice, "alice"),
                    (page_b, bob, "bob"),
                ):
                    h.wait_until(
                        lambda uid=u["id"]: bool(
                            h.query_db(
                                db_path,
                                "SELECT 1 FROM e2ee_device_keys WHERE user_id = ?",
                                (uid,),
                            )
                        ),
                        timeout=15.0,
                        desc=f"{label} device key registered",
                    )

                # --- Step 1: establish an encrypted DM ---------------------
                nonce1 = h.secrets.token_hex(4)
                dm_room_id = h.open_dm_and_send(
                    page_a, bob["id"], f"encrypted hello {nonce1}"
                )
                page_a.wait_for_selector(
                    f'.msg-text:has-text("{nonce1}")', timeout=10000
                )
                h.wait_until(
                    lambda: _room_has_text_in_db(db_path, dm_room_id, '"e2ee"'),
                    timeout=10.0,
                    desc="first DM message stored as an E2EE envelope",
                )
                room_encrypted = page_a.evaluate(
                    f"() => _roomEncrypted.has('{dm_room_id}')"
                )
                if not room_encrypted:
                    failures.append(
                        "precondition: _roomEncrypted does not contain the DM room "
                        "after a successful encrypted send"
                    )
                h.log(f"step1 OK: encrypted DM established room={dm_room_id}")

                # --- Step 2: flip E2EE.available=false, attempt send -------
                page_a.evaluate("() => { E2EE.available = false; }")
                probe = f"SHOULD_NOT_LEAK_{h.secrets.token_hex(4)}"
                page_a.fill("#msg-input", probe)
                page_a.click(".input-bar .send")

                # The blocked send surfaces a toast and returns before any
                # optimistic render; give the WS/DB a moment to (not) persist.
                toast_ok = False
                try:
                    h.wait_until(
                        lambda: "encryption unavailable"
                        in (page_a.text_content(".toast") or "").lower(),
                        timeout=6.0,
                        desc="fail-closed toast shown",
                    )
                    toast_ok = True
                except Exception:
                    pass

                # Hard assertion: the probe must never reach the DB, and in
                # particular must never be stored as plaintext.
                h.time.sleep(1.5)
                leaked_plain = _room_has_plaintext_in_db(db_path, dm_room_id, probe)
                present_at_all = _room_has_text_in_db(db_path, dm_room_id, probe)
                in_dom = (
                    page_a.locator(f'.msg-text:has-text("{probe}")').count() > 0
                )

                if leaked_plain:
                    failures.append(
                        "C1 FAIL: blocked-send probe was stored as PLAINTEXT in the "
                        "encrypted DM (the exact regression the fix closes)"
                    )
                if present_at_all:
                    failures.append(
                        "C1 FAIL: blocked-send probe reached the DB at all "
                        "(should have been blocked before send)"
                    )
                if in_dom:
                    failures.append(
                        "C1 FAIL: blocked-send probe rendered as a sent bubble"
                    )
                if not toast_ok:
                    failures.append(
                        "C1 WARN: no 'Encryption unavailable' toast observed "
                        "(block may still have worked; check DB assertions)"
                    )
                if not failures:
                    h.log("step2 OK: send blocked, no plaintext leak, no DB row")

                # --- Step 3: control -- keyless DM still allows plaintext ---
                # E2EE.available is still false on Alice's page. The Carol DM
                # was never encrypted, so the fix must let it fall back to
                # plaintext (else we'd have broken all sends).
                page_a.locator(".tabs button", has_text="DMs").click(timeout=10000)
                page_a.wait_for_selector(
                    f'.member-item[data-room-id="{carol_dm_id}"]', timeout=10000
                )
                page_a.click(f'.member-item[data-room-id="{carol_dm_id}"]')
                page_a.wait_for_selector("#msg-input", timeout=10000)
                ctrl = f"keyless_ok_{h.secrets.token_hex(4)}"
                page_a.fill("#msg-input", ctrl)
                page_a.click(".input-bar .send")
                try:
                    h.wait_until(
                        lambda: _room_has_plaintext_in_db(
                            db_path, carol_dm_id, ctrl
                        ),
                        timeout=8.0,
                        desc="keyless-DM control message stored as plaintext",
                    )
                    h.log("step3 OK: never-encrypted DM still sends plaintext")
                except Exception:
                    failures.append(
                        "CONTROL FAIL: keyless DM no longer sends (fix over-blocks "
                        "never-encrypted rooms)"
                    )
            finally:
                browser.close()
    finally:
        h.stop_server(proc)

    print("\n=== C1 fail-closed check ===")
    if failures:
        for f in failures:
            print("  FAIL " + f)
        print("RESULT: FAIL")
        return 1
    print("  all assertions passed")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
