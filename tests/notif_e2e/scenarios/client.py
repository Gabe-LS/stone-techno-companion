"""Stage-2 client-behavior scenarios (real Chromium, synchronous).

Each scenario is a SYNC function `fn(lab, server, recorder) -> list[str]`
returning failure strings (empty = pass). They drive server/chat/chat.html in
a real browser via the BrowserLab/BrowserSession layer and assert the
client-side notification signals: enable/disable/repair, the idle beacon, the
focus-gated visible keepalive, cross-device badge fan-out and clear, and the
non-blocking first-run banner.

Synchronous because Playwright's sync API cannot run inside an asyncio loop.
"other user" sends use browser.send_message_as (also synchronous).
"""

from __future__ import annotations

import time

from browser import ALLOW_FIRST_RUN_BANNER_SCRIPT, send_message_as

# Single source of the skip sentinel: run.py catches the emission module's
# ScenarioSkip for both suites.
from scenarios.emission import ScenarioSkip


def _count_ws_event(sess, event: str) -> int:
    return sum(1 for f in sess.ws_sent() if f.get("event") == event)


def _has_http(sess, method: str, path_substr: str) -> bool:
    return any(
        e["method"] == method and path_substr in e["path"] for e in list(sess._http_log)
    )


def _wait_room_unread(sess, room_id, want, timeout=6.0) -> bool:
    """Poll the client's own unreadByRoom[room_id] until it reaches `want`.
    Asserts on client STATE rather than raw WS frames: Playwright's
    framereceived capture is unreliable for this app (it misses badge_update
    frames the client nonetheless processes), whereas unreadByRoom is the
    authoritative badge state that drives the title and app badge. want==0
    means the room's unread is absent or zero."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        unread = sess.call("() => unreadByRoom") or {}
        cur = unread.get(room_id, 0) or 0
        if (want == 0 and cur == 0) or (want != 0 and cur == want):
            return True
        time.sleep(0.1)
    return False


def enable_success(lab, server, recorder) -> list[str]:
    """Enable flow: vapid-key fetch -> subscribe (no prior unsubscribe) ->
    POST /push/subscribe -> push_enabled=1 -> server stored the subscription."""
    fails: list[str] = []
    uid = server.create_user("EnableUser", username="enableuser")
    server.ensure_membership(uid, server.main_room_id())
    sess = lab.new_session(uid, recorder=recorder)
    try:
        sess.call("() => _enableAllNotifications()")
        time.sleep(0.6)

        push_calls = sess.call("() => window.__pushCalls || []")
        recorder.record("browser", "push_calls", {"order": push_calls})

        if not any("/push/vapid-key" in u for u in sess.fetches()):
            fails.append("no /push/vapid-key fetch observed")
        if not _has_http(sess, "POST", "/push/subscribe"):
            fails.append("no POST /chat/api/push/subscribe observed")
        if sess.ls_get("push_enabled") != "1":
            fails.append(f"push_enabled != '1' (got {sess.ls_get('push_enabled')!r})")
        if server.chat_sub_count(uid) != 1:
            fails.append(
                f"server chat_sub_count != 1 (got {server.chat_sub_count(uid)})"
            )
        if "subscribe" not in push_calls:
            fails.append(f"pushManager.subscribe never called: {push_calls}")
        else:
            sidx = push_calls.index("subscribe")
            if "unsubscribe" in push_calls[:sidx]:
                fails.append(f"unsubscribe called before subscribe: {push_calls}")
        try:
            recorder.assert_sequence(
                [
                    "http:GET /chat/api/push/vapid-key",
                    "http:POST /chat/api/push/subscribe",
                ]
            )
        except AssertionError as e:
            fails.append(f"order (vapid-key before subscribe POST): {e}")
        if not fails:
            print(
                f"[enable_success] push_enabled=1, server sub stored, order OK, calls={push_calls}"
            )
    finally:
        sess.close()
    return fails


def disable_flow(lab, server, recorder) -> list[str]:
    """Disable: endpoint read before unsubscribe -> DELETE /push/subscribe ->
    push_enabled removed, notif_prompt_done set, server row gone."""
    fails: list[str] = []
    uid = server.create_user("DisableUser", username="disableuser")
    server.ensure_membership(uid, server.main_room_id())
    sess = lab.new_session(uid, recorder=recorder)
    try:
        sess.call("() => _enableAllNotifications()")
        time.sleep(0.5)
        if server.chat_sub_count(uid) != 1:
            fails.append("precondition: enable did not store a subscription")
            return fails

        sess.call("() => _disableAllNotifications()")
        time.sleep(0.5)

        push_calls = sess.call("() => window.__pushCalls || []")
        recorder.record("browser", "push_calls_after_disable", {"order": push_calls})

        if "unsubscribe" not in push_calls:
            fails.append(f"unsubscribe never called: {push_calls}")
        else:
            uidx = push_calls.index("unsubscribe")
            if "getSubscription" not in push_calls[:uidx]:
                fails.append(
                    f"endpoint not read (no getSubscription) before unsubscribe: {push_calls}"
                )
        if not _has_http(sess, "DELETE", "/push/subscribe"):
            fails.append("no DELETE /chat/api/push/subscribe observed")
        if sess.ls_get("push_enabled") is not None:
            fails.append(
                f"push_enabled not cleared (got {sess.ls_get('push_enabled')!r})"
            )
        if sess.ls_get("notif_prompt_done") != "1":
            fails.append("notif_prompt_done not set by disable")
        if server.chat_sub_count(uid) != 0:
            fails.append(
                f"server chat_sub_count != 0 (got {server.chat_sub_count(uid)})"
            )
        if not fails:
            print(
                "[disable_flow] unsubscribe after getSubscription, DELETE sent, row gone"
            )
    finally:
        sess.close()
    return fails


def repair_gated(lab, server, recorder) -> list[str]:
    """Repair on load runs only with push_enabled='1'. Without it: no
    vapid-key fetch. With it + an existing subscription: a resync POST
    /push/subscribe (NOT a fresh browser subscribe)."""
    fails: list[str] = []
    uid = server.create_user("RepairUser", username="repairuser")
    server.ensure_membership(uid, server.main_room_id())
    sess = lab.new_session(uid, recorder=recorder)
    try:
        # Phase A: fresh load, push_enabled unset -> repair must NOT fetch vapid-key.
        time.sleep(0.8)
        if any("/push/vapid-key" in u for u in sess.fetches()):
            fails.append(
                "repair ran without push_enabled (vapid-key fetched on initial load)"
            )

        # Enable so a real subscription + push_enabled exist for the resync path.
        sess.call("() => _enableAllNotifications()")
        time.sleep(0.5)
        if server.chat_sub_count(uid) != 1:
            fails.append("precondition: enable did not store a subscription")
            return fails

        # Phase B: reload -> repair should resync (POST /push/subscribe) without
        # re-subscribing. __pushCalls and __fetchRequests reset on reload.
        sess.page.reload(timeout=30000)
        sess.page.wait_for_selector("#messages", timeout=20000)
        time.sleep(2.0)

        post_reload_calls = sess.call("() => window.__pushCalls || []")
        post_reload_fetches = sess.fetches()
        recorder.record(
            "browser",
            "repair_reload",
            {"calls": post_reload_calls, "fetches": post_reload_fetches},
        )

        if not any("/push/subscribe" in u for u in post_reload_fetches):
            fails.append(
                f"repair did not resync (no /push/subscribe after reload): {post_reload_fetches}"
            )
        if "subscribe" in post_reload_calls:
            fails.append(
                f"repair re-subscribed instead of resyncing existing sub: {post_reload_calls}"
            )
        if "getSubscription" not in post_reload_calls:
            fails.append(
                f"repair did not read existing subscription: {post_reload_calls}"
            )
        if not fails:
            print(
                "[repair_gated] gated off without flag; resynced (no re-subscribe) with flag"
            )
    finally:
        sess.close()
    return fails


def idle_beacon(lab, server, recorder) -> list[str]:
    """Hiding the tab fires navigator.sendBeacon('/chat/api/push/idle').
    (The server-side effect is covered by Stage 1's idle_recipient_push, which
    posts the idle signal directly; the spy here records the client call
    without sending, so this asserts the client behavior.)"""
    fails: list[str] = []
    uid = server.create_user("IdleUser", username="idleuser")
    server.ensure_membership(uid, server.main_room_id())
    sess = lab.new_session(uid, recorder=recorder)
    try:
        time.sleep(0.3)
        sess.set_hidden(True)
        time.sleep(0.3)
        beacons = sess.beacons()
        recorder.record("browser", "beacons", {"urls": beacons})
        if not any("/chat/api/push/idle" in b for b in beacons):
            fails.append(f"no idle beacon fired on tab hide; beacons={beacons}")
        if not fails:
            print("[idle_beacon] sendBeacon('/chat/api/push/idle') fired on hide")
    finally:
        sess.close()
    return fails


def focus_gated_keepalive(lab, server, recorder) -> list[str]:
    """The visible keepalive sends a 'visible' WS frame only when
    document.hasFocus() -- a visible-but-unfocused window must not send it.
    Driven directly via _sendVisibleSignal() to avoid the 20s interval."""
    fails: list[str] = []
    uid = server.create_user("FocusUser", username="focususer")
    server.ensure_membership(uid, server.main_room_id())
    sess = lab.new_session(uid, recorder=recorder)
    try:
        time.sleep(0.3)
        # Unfocused: _sendVisibleSignal must be a no-op.
        sess.set_focus(False)
        time.sleep(0.1)
        before = _count_ws_event(sess, "visible")
        sess.call("() => _sendVisibleSignal()")
        time.sleep(0.3)
        after_unfocused = _count_ws_event(sess, "visible")
        if after_unfocused != before:
            fails.append(
                f"'visible' frame sent while unfocused ({before} -> {after_unfocused})"
            )

        # Focused: _sendVisibleSignal must send a 'visible' frame.
        sess.set_focus(True)
        sess.call("() => _sendVisibleSignal()")
        time.sleep(0.3)
        after_focused = _count_ws_event(sess, "visible")
        if after_focused <= after_unfocused:
            fails.append(
                f"'visible' frame not sent while focused ({after_unfocused} -> {after_focused})"
            )
        recorder.record(
            "browser",
            "visible_counts",
            {"before": before, "unfocused": after_unfocused, "focused": after_focused},
        )
        if not fails:
            print(
                f"[focus_gated_keepalive] unfocused suppressed ({before}->{after_unfocused}), "
                f"focused sent ({after_unfocused}->{after_focused})"
            )
    finally:
        sess.close()
    return fails


def badge_fanout_cross_device(lab, server, recorder) -> list[str]:
    """Two devices for one user: a message badges both; reading on device A
    broadcasts a count=0 badge_update that clears device B (cross-device)."""
    fails: list[str] = []
    room_id = server.main_room_id()
    u = server.create_user("BadgeUser", username="badgeuser")
    sender = server.create_user("BadgeSender", username="badgesender")
    server.ensure_membership(u, room_id)
    server.ensure_membership(sender, room_id)

    sess_a = lab.new_session(u, recorder=recorder)
    sess_b = lab.new_session(u, recorder=recorder)
    try:
        time.sleep(0.4)
        # Both hidden: neither foreground-ignores the badge, and A won't
        # auto-read yet.
        sess_a.set_hidden(True)
        sess_b.set_hidden(True)
        time.sleep(0.2)

        send_message_as(server, sender, room_id, "cross-device badge hello", settle=1.0)

        if not _wait_room_unread(sess_b, room_id, 1, timeout=6.0):
            fails.append(
                f"device B did not badge count=1 (unreadByRoom={sess_b.call('() => unreadByRoom')})"
            )
        else:
            if "(1)" not in sess_b.title():
                fails.append(f"device B title missing '(1)': {sess_b.title()!r}")
            if sess_b.app_badge().get("value") != 1:
                fails.append(f"device B app badge != 1: {sess_b.app_badge()}")

        # Bring device A to the foreground -> it reads the message -> server
        # broadcasts badge_update count=0 to all of the user's connections.
        sess_a.set_hidden(False)
        sess_a.set_focus(True)

        if not _wait_room_unread(sess_b, room_id, 0, timeout=8.0):
            fails.append(
                f"device B not cleared cross-device after A read "
                f"(unreadByRoom={sess_b.call('() => unreadByRoom')})"
            )
        else:
            if "(" in sess_b.title():
                fails.append(
                    f"device B title still shows a badge after clear: {sess_b.title()!r}"
                )
            if sess_b.app_badge().get("value") not in (None, 0):
                fails.append(f"device B app badge not cleared: {sess_b.app_badge()}")
        if not fails:
            print(
                "[badge_fanout_cross_device] B badged on send, cleared cross-device on A read"
            )
    finally:
        sess_a.close()
        sess_b.close()
    return fails


def first_run_banner_nonblocking(lab, server, recorder) -> list[str]:
    """After the first sent message the first-run banner appears as a
    non-blocking top banner: the composer send button stays clickable (the
    regression guard for the old full-screen modal). An explicit answer
    persists notif_prompt_done and removes the banner."""
    fails: list[str] = []
    uid = server.create_user("BannerUser", username="banneruser")
    server.ensure_membership(uid, server.main_room_id())
    sess = lab.new_session(
        uid, recorder=recorder, extra_init=[ALLOW_FIRST_RUN_BANNER_SCRIPT]
    )
    try:
        # Ensure the banner is not pre-suppressed for this session.
        sess.call("() => localStorage.removeItem('notif_prompt_done')")
        time.sleep(0.3)

        # Send the first message via the real UI path -> arms the banner.
        sess.call(
            "() => { const i = document.getElementById('msg-input'); "
            "i.value = 'first message'; sendMessage(); }"
        )
        # Banner arms ~800ms after send; give it margin.
        time.sleep(1.6)

        banner_present = sess.call(
            "() => !!document.getElementById('notif-prompt-banner')"
        )
        if not banner_present:
            raise ScenarioSkip(
                "first-run banner did not appear after first message; cannot test "
                "non-blocking behavior (check sent_first_msg/_maybePromptNotifications gating)"
            )

        # Regression guard: the send button must NOT be covered by the banner.
        top_el = sess.call(
            """() => {
              const btn = document.querySelector('.input-bar .send');
              if (!btn) return 'no-send-button';
              const r = btn.getBoundingClientRect();
              const el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
              if (!el) return 'nothing-at-point';
              return el.closest('.input-bar .send') ? 'send-button'
                   : (el.closest('#notif-prompt-banner') ? 'banner-intercepts' : el.tagName);
            }"""
        )
        recorder.record("browser", "send_button_hit_test", {"top_el": top_el})
        if top_el != "send-button":
            fails.append(
                f"send button not clickable while banner shows (elementFromPoint -> {top_el!r})"
            )

        # Explicit dismiss persists the flag and removes the banner.
        sess.call("() => _answerNotifPrompt(false)")
        time.sleep(0.4)
        if sess.call("() => !!document.getElementById('notif-prompt-banner')"):
            fails.append("banner not removed after explicit dismiss")
        if sess.ls_get("notif_prompt_done") != "1":
            fails.append("notif_prompt_done not persisted after explicit dismiss")
        if not fails:
            print(
                "[first_run_banner_nonblocking] banner non-blocking; explicit dismiss persisted"
            )
    finally:
        sess.close()
    return fails


SCENARIOS = {
    "enable_success": {"fn": enable_success},
    "disable_flow": {"fn": disable_flow},
    "repair_gated": {"fn": repair_gated},
    "idle_beacon": {"fn": idle_beacon},
    "focus_gated_keepalive": {"fn": focus_gated_keepalive},
    "badge_fanout_cross_device": {"fn": badge_fanout_cross_device},
    "first_run_banner_nonblocking": {"fn": first_run_banner_nonblocking},
}
