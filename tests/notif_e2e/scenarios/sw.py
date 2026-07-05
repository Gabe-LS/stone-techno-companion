"""Stage-3 service-worker handler-behavior scenarios (real Chromium, synchronous).

Each scenario is a SYNC function `fn(swlab, server, recorder) -> list[str]`
returning failure strings (empty = pass). They drive the REAL server/static/
sw.js source running in sw_harness.SWHarness's mock service-worker
environment, asserting on push/notificationclick/notificationclose/
pushsubscriptionchange side effects: shown notifications, tag collapse,
silent flag, cache writes, postMessage navigation, openWindow, and
swlog/ack fetches.

Synchronous because Playwright's sync API cannot run inside an asyncio loop
(matching scenarios/client.py's Stage-2 suite).
"""

from __future__ import annotations

from scenarios.emission import ScenarioSkip  # noqa: F401 -- re-exported escape hatch


def _swlog_steps(rec: dict) -> list:
    return [
        f["body"].get("step") for f in rec["fetches"] if f["url"] == "/chat/api/swlog"
    ]


def _ack_actions(rec: dict) -> list:
    return [
        f["body"].get("action")
        for f in rec["fetches"]
        if f["url"] == "/chat/api/push/ack"
    ]


def push_shows_notification(swlab, server, recorder) -> list[str]:
    """One push -> exactly one notification with the room-name title, body,
    tag == stc-<room_id>-<push_id>, data {url, roomId, count}; a swlog
    push-received fetch carrying the payload url; an ack delivered fetch;
    setAppBadge(total_unread)."""
    fails: list[str] = []
    h = swlab.new_harness(recorder=recorder)
    try:
        payload = {
            "title": "#general",
            "body": "A: hello",
            "url": "/chat/msg/m1",
            "room_id": "general",
            "push_id": "p1",
            "total_unread": 3,
            "count": 1,
        }
        rec = h.push(payload)

        if len(rec["shown"]) != 1:
            fails.append(
                f"expected 1 notification shown, got {len(rec['shown'])}: {rec['shown']}"
            )
        else:
            n = rec["shown"][0]
            if n["title"] != "#general":
                fails.append(f"title != '#general': {n['title']!r}")
            if n["body"] != "A: hello":
                fails.append(f"body != 'A: hello': {n['body']!r}")
            if n["tag"] != "stc-general-p1":
                fails.append(f"tag != 'stc-general-p1': {n['tag']!r}")
            expected_data = {"url": "/chat/msg/m1", "roomId": "general", "count": 1}
            if n["data"] != expected_data:
                fails.append(f"data mismatch: {n['data']!r} != {expected_data!r}")

        steps = _swlog_steps(rec)
        if "push-received" not in steps:
            fails.append(f"no swlog 'push-received': {steps}")
        recv_urls = [
            f["body"].get("detail")
            for f in rec["fetches"]
            if f["url"] == "/chat/api/swlog"
            and f["body"].get("step") == "push-received"
        ]
        if payload["url"] not in recv_urls:
            fails.append(f"swlog push-received detail missing payload url: {recv_urls}")

        acks = _ack_actions(rec)
        if "delivered" not in acks:
            fails.append(f"no ack 'delivered': {acks}")

        if 3 not in rec["badge"]:
            fails.append(f"setAppBadge(3) not called: {rec['badge']}")

        if not fails:
            print("[push_shows_notification] shown/tag/swlog/ack/badge all matched")
    finally:
        h.close()
    return fails


def tag_uniqueness_and_collapse(swlab, server, recorder) -> list[str]:
    """Push room 'general' push_id p1, then room 'general' push_id p2: the
    first is closed (rec.closed contains its tag), exactly one notification
    remains, and the two tags differ. Then push room 'other' push_id p3:
    two notifications coexist (different rooms are not collapsed)."""
    fails: list[str] = []
    h = swlab.new_harness(recorder=recorder)
    try:
        rec1 = h.push(
            {
                "title": "#general",
                "body": "1",
                "url": "/chat/msg/m1",
                "room_id": "general",
                "push_id": "p1",
                "total_unread": 1,
            }
        )
        if not rec1["shown"]:
            fails.append("first push did not show a notification")
            return fails
        tag1 = rec1["shown"][0]["tag"]

        rec2 = h.push(
            {
                "title": "#general",
                "body": "2",
                "url": "/chat/msg/m2",
                "room_id": "general",
                "push_id": "p2",
                "total_unread": 2,
            }
        )
        if tag1 not in rec2["closed"]:
            fails.append(
                f"first tag {tag1!r} not closed after second push in same room: closed={rec2['closed']}"
            )

        notifs = h.notifications()
        if len(notifs) != 1:
            fails.append(
                f"expected 1 notification remaining after collapse, got {len(notifs)}: {notifs}"
            )

        tag2 = rec2["shown"][-1]["tag"] if rec2["shown"] else None
        if tag1 == tag2:
            fails.append(
                f"tags did not differ across same-room pushes: {tag1} == {tag2}"
            )

        h.push(
            {
                "title": "#other",
                "body": "3",
                "url": "/chat/msg/m3",
                "room_id": "other",
                "push_id": "p3",
                "total_unread": 3,
            }
        )
        notifs3 = h.notifications()
        if len(notifs3) != 2:
            fails.append(
                f"expected 2 notifications to coexist across rooms, got {len(notifs3)}: {notifs3}"
            )

        if not fails:
            print(
                "[tag_uniqueness_and_collapse] same-room collapse + cross-room coexistence OK"
            )
    finally:
        h.close()
    return fails


def silent_followup(swlab, server, recorder) -> list[str]:
    """A push with silent:true -> the shown notification has silent === true;
    a push with silent absent -> silent is falsy."""
    fails: list[str] = []
    h = swlab.new_harness(recorder=recorder)
    try:
        h.push(
            {
                "title": "#silent",
                "body": "x",
                "url": "/chat/msg/s1",
                "room_id": "silentroom",
                "push_id": "sp1",
                "total_unread": 1,
                "silent": True,
            }
        )
        notifs = h.notifications()
        if not notifs or notifs[-1]["silent"] is not True:
            fails.append(f"silent push did not set silent=true: {notifs}")

        h.push(
            {
                "title": "#loud",
                "body": "y",
                "url": "/chat/msg/l1",
                "room_id": "loudroom",
                "push_id": "lp1",
                "total_unread": 2,
            }
        )
        notifs2 = h.notifications()
        if not notifs2 or notifs2[-1]["silent"]:
            fails.append(f"non-silent push had truthy silent: {notifs2}")

        if not fails:
            print("[silent_followup] silent true/falsy propagated correctly")
    finally:
        h.close()
    return fails


def click_existing_client(swlab, server, recorder) -> list[str]:
    """new_harness(match_client set) -> push -> click_last -> cachePuts has
    /_push_navigate, a postMessage {type: navigate, url: <full url>} was
    sent to the client, the client was focused, an ack 'clicked' fetch, a
    swlog 'click-done' fetch, and NO openWindow call."""
    fails: list[str] = []
    client_url = server.base_url + "/chat"
    h = swlab.new_harness(match_client=client_url, recorder=recorder)
    try:
        h.push(
            {
                "title": "#general",
                "body": "hi",
                "url": "/chat/msg/m1",
                "room_id": "general",
                "push_id": "p1",
                "total_unread": 1,
            }
        )
        rec = h.click_last()

        if "/_push_navigate" not in rec["cachePuts"]:
            fails.append(f"click did not cache /_push_navigate: {rec['cachePuts']}")

        nav = [m for m in rec["postMessages"] if m.get("type") == "navigate"]
        expected_url = server.base_url + "/chat/msg/m1"
        if not nav:
            fails.append(f"click did not postMessage navigate: {rec['postMessages']}")
        elif nav[-1].get("url") != expected_url:
            fails.append(
                f"postMessage navigate url mismatch: {nav[-1]} != {expected_url!r}"
            )

        states = h.client_states()
        if not states or not states[0].get("focused"):
            fails.append(f"existing client was not focused: {states}")

        acks = _ack_actions(rec)
        if "clicked" not in acks:
            fails.append(f"no ack 'clicked': {acks}")

        steps = _swlog_steps(rec)
        if "click-done" not in steps:
            fails.append(f"no swlog 'click-done': {steps}")

        if rec["openWindows"]:
            fails.append(
                f"openWindow called unexpectedly with an existing client: {rec['openWindows']}"
            )

        if not fails:
            print(
                "[click_existing_client] postMessage navigate + focus + acks OK, no openWindow"
            )
    finally:
        h.close()
    return fails


def click_opens_window(swlab, server, recorder) -> list[str]:
    """new_harness(match_client=None) -> push -> click_last -> openWindow
    was called with the full url, ack 'clicked', and NO postMessage."""
    fails: list[str] = []
    h = swlab.new_harness(match_client=None, recorder=recorder)
    try:
        h.push(
            {
                "title": "#general",
                "body": "hi",
                "url": "/chat/msg/m2",
                "room_id": "general",
                "push_id": "p2",
                "total_unread": 1,
            }
        )
        rec = h.click_last()

        expected_url = server.base_url + "/chat/msg/m2"
        if expected_url not in rec["openWindows"]:
            fails.append(
                f"openWindow not called with {expected_url!r}: {rec['openWindows']}"
            )

        acks = _ack_actions(rec)
        if "clicked" not in acks:
            fails.append(f"no ack 'clicked': {acks}")

        if rec["postMessages"]:
            fails.append(
                f"postMessage sent unexpectedly with no existing client: {rec['postMessages']}"
            )

        if not fails:
            print("[click_opens_window] openWindow called, ack clicked, no postMessage")
    finally:
        h.close()
    return fails


def close_acks_dismissed(swlab, server, recorder) -> list[str]:
    """push -> close_last -> an ack fetch with action 'dismissed'."""
    fails: list[str] = []
    h = swlab.new_harness(recorder=recorder)
    try:
        h.push(
            {
                "title": "#general",
                "body": "hi",
                "url": "/chat/msg/m3",
                "room_id": "general",
                "push_id": "p3",
                "total_unread": 1,
            }
        )
        rec = h.close_last()

        acks = _ack_actions(rec)
        if "dismissed" not in acks:
            fails.append(f"no ack 'dismissed': {acks}")

        if not fails:
            print("[close_acks_dismissed] ack 'dismissed' observed")
    finally:
        h.close()
    return fails


def subscriptionchange_resubscribes(swlab, server, recorder) -> list[str]:
    """subscription_change(old_options) -> a POST /chat/api/push/subscribe
    fetch whose body has the rotated endpoint and keys {p256dh, auth}."""
    fails: list[str] = []
    h = swlab.new_harness(recorder=recorder)
    try:
        old_options = {"userVisibleOnly": True, "applicationServerKey": [9]}
        rec = h.subscription_change(old_options)

        subs = [f for f in rec["fetches"] if f["url"] == "/chat/api/push/subscribe"]
        if not subs:
            fails.append(
                f"no POST /chat/api/push/subscribe observed: "
                f"{[(f['url'], f['body']) for f in rec['fetches']]}"
            )
        else:
            body = subs[-1]["body"] or {}
            if body.get("endpoint") != "https://fcm.googleapis.com/fcm/send/rotated":
                fails.append(
                    f"subscribe endpoint not the rotated one: {body.get('endpoint')!r}"
                )
            keys = body.get("keys") or {}
            if "p256dh" not in keys or "auth" not in keys:
                fails.append(f"subscribe body missing keys.p256dh/auth: {keys}")

        if not fails:
            print(
                "[subscriptionchange_resubscribes] resubscribed with rotated endpoint + keys"
            )
    finally:
        h.close()
    return fails


SCENARIOS = {
    "push_shows_notification": {"fn": push_shows_notification},
    "tag_uniqueness_and_collapse": {"fn": tag_uniqueness_and_collapse},
    "silent_followup": {"fn": silent_followup},
    "click_existing_client": {"fn": click_existing_client},
    "click_opens_window": {"fn": click_opens_window},
    "close_acks_dismissed": {"fn": close_acks_dismissed},
    "subscriptionchange_resubscribes": {"fn": subscriptionchange_resubscribes},
}
