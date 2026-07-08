#!/usr/bin/env python3
"""Standalone browser check for the relationship-scoped anti-downgrade fix
(P2): a DM that was encrypted in a PRIOR session must not silently downgrade
to plaintext after a reload once its history has TTL-purged and the peer looks
keyless.

Reuses e2ee_browser_check.py scaffolding. NOT part of pytest; run OUTSIDE the
command sandbox:

    python tests/p2_downgrade_check.py

Scenario (isolates the persisted per-peer flag from the in-memory
_roomEncrypted set):
  1. Alice opens a DM with Bob and sends an encrypted message. This persists
     a per-peer "has had E2EE" bit in Alice's localStorage.
  2. Simulate the passage of time: delete the message (TTL purge) AND delete
     Bob's device keys (peer now looks keyless). Reload Alice's page so the
     in-memory _roomEncrypted resets to empty while localStorage survives.
  3. Alice reopens the DM (empty history, so nothing re-populates
     _roomEncrypted) and tries to send. EXPECT: fail closed (blocked, no
     plaintext row), because the persisted flag still marks the relationship
     as encrypted. Pre-fix, this reopened the downgrade window and sent
     plaintext.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2ee_browser_check as h  # noqa: E402


def _row_has_text(db_path, room_id, needle):
    rows = h.query_db(
        db_path, "SELECT content FROM messages WHERE room_id = ?", (room_id,)
    )
    return any(needle in r["content"] for r in rows)


def _row_has_plaintext(db_path, room_id, needle):
    rows = h.query_db(
        db_path, "SELECT content FROM messages WHERE room_id = ?", (room_id,)
    )
    return any(needle in r["content"] and '"e2ee"' not in r["content"] for r in rows)


def main() -> int:
    tmp_dir = tempfile.mkdtemp(prefix="p2_check_")
    db_path = Path(tmp_dir) / "chat.db"
    proc = None
    failures = []

    try:
        os.environ["CHAT_DB_PATH"] = str(db_path)
        alice, bob, _carol, _carol_dm = h.setup_users(db_path)
        h.log(f"scratch users alice={alice['id'][:8]} bob={bob['id'][:8]} db={db_path}")

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
                        lambda pg=pg: pg.evaluate("() => !!ws && ws.readyState === 1"),
                        timeout=15.0,
                        desc=f"{label} WS open",
                    )
                for u, label in ((alice, "alice"), (bob, "bob")):
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

                # --- Step 1: establish encryption (persists the peer flag) ---
                nonce1 = h.secrets.token_hex(4)
                dm_room_id = h.open_dm_and_send(
                    page_a, bob["id"], f"encrypted hello {nonce1}"
                )
                page_a.wait_for_selector(f'.msg-text:has-text("{nonce1}")', timeout=10000)
                h.wait_until(
                    lambda: _row_has_text(db_path, dm_room_id, '"e2ee"'),
                    timeout=10.0,
                    desc="first message stored as an E2EE envelope",
                )
                # typeof-guarded so the check degrades to False (not a thrown
                # ReferenceError) when run against pre-fix code that lacks the
                # helper -- lets the negative validation reach step 3 and show
                # the actual plaintext downgrade rather than crashing here.
                if not page_a.evaluate(
                    f"() => typeof _hasPeerBeenEncrypted === 'function'"
                    f" && _hasPeerBeenEncrypted('{bob['id']}')"
                ):
                    failures.append(
                        "precondition: per-peer flag not persisted after encrypted send"
                    )
                h.log("step1 OK: encrypted DM + persisted per-peer flag")

                # --- Step 2: simulate TTL purge + peer going keyless ---------
                h.exec_db(
                    db_path, "DELETE FROM messages WHERE room_id = ?", (dm_room_id,)
                )
                h.exec_db(
                    db_path,
                    "DELETE FROM e2ee_device_keys WHERE user_id = ?",
                    (bob["id"],),
                )
                page_a.reload(timeout=30000)
                page_a.wait_for_selector("#messages", timeout=20000)
                h.wait_until(
                    lambda: page_a.evaluate("() => !!ws && ws.readyState === 1"),
                    timeout=15.0,
                    desc="alice WS reopen after reload",
                )
                h.open_existing_dm(page_a, dm_room_id)
                # After reload with no history, _roomEncrypted must be empty:
                # this is what isolates the persisted flag as the sole guard.
                if page_a.evaluate(f"() => _roomEncrypted.has('{dm_room_id}')"):
                    failures.append(
                        "precondition: _roomEncrypted unexpectedly set after reload "
                        "(test would not isolate the persisted flag)"
                    )
                if not page_a.evaluate(
                    f"() => typeof _hasPeerBeenEncrypted === 'function'"
                    f" && _hasPeerBeenEncrypted('{bob['id']}')"
                ):
                    failures.append(
                        "precondition: per-peer flag lost across reload (localStorage)"
                    )
                h.log("step2 OK: history purged, peer keyless, reloaded, flag survives")

                # --- Step 3: attempt send -> must fail closed ----------------
                probe = f"SHOULD_NOT_LEAK_{h.secrets.token_hex(4)}"
                page_a.fill("#msg-input", probe)
                page_a.click(".input-bar .send")
                toast_ok = False
                try:
                    h.wait_until(
                        lambda: "encryption unavailable"
                        in (page_a.text_content(".toast") or "").lower(),
                        timeout=6.0,
                        desc="fail-closed toast",
                    )
                    toast_ok = True
                except Exception:
                    pass

                h.time.sleep(1.5)
                if _row_has_plaintext(db_path, dm_room_id, probe):
                    failures.append(
                        "P2 FAIL: reopened DM downgraded to PLAINTEXT after reload "
                        "(the exact window the persisted flag should close)"
                    )
                if _row_has_text(db_path, dm_room_id, probe):
                    failures.append("P2 FAIL: probe reached the DB (send not blocked)")
                if page_a.locator(f'.msg-text:has-text("{probe}")').count() > 0:
                    failures.append("P2 FAIL: probe rendered as a sent bubble")
                if not toast_ok:
                    failures.append(
                        "P2 WARN: no 'Encryption unavailable' toast (check DB assertions)"
                    )
                if not failures:
                    h.log("step3 OK: send blocked, no plaintext leak across reload")
            finally:
                browser.close()
    finally:
        h.stop_server(proc)

    print("\n=== P2 anti-downgrade check ===")
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
