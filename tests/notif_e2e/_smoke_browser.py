"""Smoke test for the Stage-2 browser layer.

Launches real headless Chromium, logs in a created user, loads /chat, drives
the real _enableAllNotifications() flow (with the subscribe-success override),
and asserts the client POSTed a subscription the server stored, set the
push_enabled flag, and that the spies + WS capture work. Run unsandboxed:
    python tests/notif_e2e/_smoke_browser.py
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402

from browser import BrowserLab  # noqa: E402
from harness import NotifServer  # noqa: E402
from recorder import SignalRecorder  # noqa: E402


def main() -> int:
    server = NotifServer()
    fails: list[str] = []
    browser = None
    pw = None
    try:
        server.start()
        room_id = server.main_room_id()
        uid = server.create_user("Alice", username="alice")
        server.ensure_membership(uid, room_id)

        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        lab = BrowserLab(server, browser)
        rec = SignalRecorder()
        sess = lab.new_session(uid, recorder=rec)
        print(f"[bsmoke] loaded /chat, title={sess.title()!r}")

        # WS should be connected and frames captured (join/badge_counts etc.)
        time.sleep(0.5)
        print(f"[bsmoke] ws_sent kinds: {[f.get('event') for f in sess.ws_sent()][:6]}")
        print(
            f"[bsmoke] ws_recv kinds: {[f.get('event') for f in sess.ws_received()][:6]}"
        )

        # Drive the real enable flow (page.evaluate awaits the returned promise).
        sess.call("() => _enableAllNotifications()")
        time.sleep(0.5)

        push_enabled = sess.ls_get("push_enabled")
        fetches = sess.fetches()
        sub_posts = [u for u in fetches if "/push/subscribe" in u]
        vapid_fetches = [u for u in fetches if "/push/vapid-key" in u]
        server_count = server.chat_sub_count(uid)
        push_calls = sess.call("() => window.__pushCalls || []")

        print(
            f"[bsmoke] push_enabled={push_enabled!r} vapid_fetches={len(vapid_fetches)} "
            f"subscribe_posts={len(sub_posts)} server_sub_count={server_count}"
        )
        print(f"[bsmoke] __pushCalls order: {push_calls}")

        if push_enabled != "1":
            fails.append(f"push_enabled expected '1', got {push_enabled!r}")
        if not vapid_fetches:
            fails.append("no /push/vapid-key fetch observed")
        if not sub_posts:
            fails.append("no POST /push/subscribe observed")
        if server_count != 1:
            fails.append(f"server chat_sub_count expected 1, got {server_count}")
        # shared-endpoint invariant: no unsubscribe before subscribe
        if "subscribe" in push_calls:
            sidx = push_calls.index("subscribe")
            if "unsubscribe" in push_calls[:sidx]:
                fails.append(f"unsubscribe called before subscribe: {push_calls}")
        else:
            fails.append(f"subscribe was never called: {push_calls}")

        # spies present
        if sess.call("() => typeof navigator.setAppBadge") != "function":
            fails.append("setAppBadge spy missing")
        if sess.call("() => document.hasFocus()") is not True:
            fails.append("hasFocus override not returning forced value")

        sess.close()
    except Exception:
        fails.append("exception: " + traceback.format_exc())
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass
        try:
            server.stop()
        except Exception:
            pass

    if fails:
        print("\n[bsmoke] FAIL")
        for f in fails:
            print("  -", f)
        print("\n[bsmoke] --- server log tail ---")
        for line in server.log_lines[-25:]:
            print("   ", line)
        return 1
    print("\n[bsmoke] PASS - browser layer core loop works")
    return 0


if __name__ == "__main__":
    sys.exit(main())
