"""Stage-1 deterministic chat-push scenarios (Layer 1: server -> wire, no browser/CDP).

Each scenario is an async function with signature
    async def scenario(server: NotifServer, fps: list[FakePushService], recorder: SignalRecorder) -> list[str]
returning a list of failure strings (empty list = pass). Every scenario is self-contained:
it creates its own fresh room, users, sessions, and subscriptions so scenarios stay
independent even when they share one NotifServer process (see run.py) -- only the
FakePushService instance(s) and the room/users are fresh per scenario, matching the
build contract ("runs each scenario with a FRESH FakePushService and fresh users/subs").

A scenario that cannot be made deterministic with the available foundation API must
raise ScenarioSkip(reason) instead of silently passing or faking a result. None of the
seven Stage-1 scenarios below need this escape hatch (see pending_not_pushed's docstring
for why it does not need to skip), but run.py handles it for future scenarios.

No emojis anywhere, per project convention.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone

from harness import NotifServer, WSClient, post_idle_beacon
from recorder import SignalRecorder


class ScenarioSkip(Exception):
    """Raised by a scenario that cannot be made deterministic with the current
    foundation API. Carries the reason to print; run.py reports this as SKIP,
    never as a pass."""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _new_room(server: NotifServer, name: str, is_moderated: bool = False) -> str:
    """Insert a fresh, isolated room directly into the scratch chat.db.

    Each scenario gets its own room rather than reusing server.main_room_id():
    _get_room_notification_targets (services/companion/chat_ws.py) pulls ALL members of a
    room, so a room shared across scenarios would accumulate users from every
    earlier scenario and inflate push_targets / the "[PUSH] targets=" log line
    with stale recipients -- silently breaking the exact-count assertions
    below. This is the same direct-DB-write fixture pattern harness.py itself
    uses for subscriptions and sessions; no server code changes needed.
    """
    room_id = f"scn-{uuid.uuid4().hex}"
    conn = sqlite3.connect(server.chat_db_path)
    try:
        conn.execute(
            "INSERT INTO rooms (id, event_id, type, name, description, is_main, "
            "is_moderated, is_read_only, auto_join, allows_media, ttl_minutes, "
            "position, created_at) VALUES (?, ?, ?, ?, '', 0, ?, 0, 0, 1, 1440, 0, ?)",
            (
                room_id,
                "stone-techno-2026",
                "general",
                name,
                int(is_moderated),
                _iso_now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return room_id


def _insert_pending_message(
    server: NotifServer, room_id: str, user_id: str, text: str
) -> str:
    """Insert a message row directly with moderation_status='pending'.

    This simulates a message whose moderation task never completed. There is
    no foundation hook to hold a *real* is_moderated room's message pending
    for a deterministic window: the isolated NotifServer strips
    OPENAI_API_KEY (see harness.SENSITIVE_ENV_KEYS), so a genuinely moderated
    room's AI layers silently pass everything and the word filter alone
    resolves 'pending' -> 'approved' almost instantly. A direct insert is the
    only deterministic way to hold a row pending, and it is the same
    direct-DB-write fixture pattern harness.py itself uses.
    """
    msg_id = f"pending-{uuid.uuid4().hex}"
    now = _iso_now()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=1440)).isoformat()
    content = json.dumps({"text": text})
    conn = sqlite3.connect(server.chat_db_path)
    try:
        conn.execute(
            "INSERT INTO messages (id, room_id, user_id, type, content, expires_at, "
            "created_at, moderation_status) VALUES (?, ?, ?, 'text', ?, ?, ?, 'pending')",
            (msg_id, room_id, user_id, content, expires, now),
        )
        conn.commit()
    finally:
        conn.close()
    return msg_id


async def _poll_until(predicate, timeout: float = 5.0, interval: float = 0.1) -> bool:
    """Poll predicate() until truthy or timeout elapses; returns final truthiness."""
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(interval)


def _log_offset(server: NotifServer) -> int:
    """Current length of server.log_lines -- capture before an action so a
    later _has_push_targets_log check only looks at lines that action itself
    produced, not a stale match left over from an earlier scenario sharing
    this same server process."""
    return len(server.log_lines)


def _has_push_targets_log(server: NotifServer, n: int, since: int = 0) -> bool:
    """True if the server logged a "[PUSH] targets=<n> " line at or after the
    `since` log-line offset (services/companion/chat_ws.py's handle_chat_ws logs
    "[PUSH] targets=%d all=%d connected=%d sender=%s")."""
    needle = f"[PUSH] targets={n} "
    return any(needle in line for line in server.log_lines[since:])


def _setup_sender_recipient(
    server: NotifServer, room_name: str
) -> tuple[str, str, str]:
    """Create a fresh room + sender + recipient, both room members. Returns
    (room_id, sender_id, recipient_id)."""
    room_id = _new_room(server, room_name)
    sender_id = server.create_user("Sender", username=_short_uid("snd"))
    recipient_id = server.create_user("Recipient", username=_short_uid("rcp"))
    server.ensure_membership(sender_id, room_id)
    server.ensure_membership(recipient_id, room_id)
    return room_id, sender_id, recipient_id


# ---------------------------------------------------------------------------
# 1. offline_recipient_push
# ---------------------------------------------------------------------------


async def offline_recipient_push(
    server: NotifServer, fps: list, recorder: SignalRecorder
) -> list[str]:
    """An offline room member gets exactly one push for a new message.

    Asserts the decrypted payload carries the message text in `body`,
    `count == 1`, `url == /chat/msg/<id>`, and a `push_id`; the wire headers
    show `TTL == 300` and a VAPID `aud` equal to this FPS's own origin and a
    `sub` claim; and the server logs "[PUSH] targets=1" for the send.
    """
    fails: list[str] = []
    push_fps = fps[0]
    room_id, sender_id, recipient_id = _setup_sender_recipient(
        server, "offline-recipient-push"
    )
    sub = server.inject_chat_subscription(recipient_id, push_fps)
    recorder.record("harness", "sub_injected", {"sub_id": sub.sub_id})

    sender = WSClient(server.ws_base, server.create_session(sender_id))
    try:
        await sender.connect()
        await sender.join_room(room_id)
        await asyncio.sleep(0.3)

        log_offset = _log_offset(server)
        text = "offline push test message"
        await sender.send_message(room_id, text)
        recorder.record("ws:sender", "send_message", {"text": text})
        acked = await sender.recv_until("message_acked", timeout=5.0)
        msg_id = acked.get("id")

        pushes = await push_fps.wait_for(sub.sub_id, count=1, timeout=8.0)
        push = pushes[0]
        recorder.record(
            "fps", "push_captured", {"sub_id": sub.sub_id}, t=push.received_at
        )

        if push.decrypt_error:
            fails.append(f"decrypt_error: {push.decrypt_error}")
        payload = push.payload or {}
        if not payload:
            fails.append("payload did not decrypt")
        if text not in (payload.get("body") or ""):
            fails.append(f"body did not carry message text: {payload.get('body')!r}")
        if payload.get("count") != 1:
            fails.append(f"count expected 1, got {payload.get('count')!r}")
        expected_url = f"/chat/msg/{msg_id}"
        if payload.get("url") != expected_url:
            fails.append(f"url expected {expected_url!r}, got {payload.get('url')!r}")
        if not payload.get("push_id"):
            fails.append("missing push_id in payload")
        if push.ttl != 300:
            fails.append(f"TTL expected 300, got {push.ttl}")
        if push.vapid.get("aud") != push_fps.origin:
            fails.append(
                f"VAPID aud expected {push_fps.origin}, got {push.vapid.get('aud')}"
            )
        if not push.vapid.get("sub"):
            fails.append("VAPID sub missing")
        if not _has_push_targets_log(server, 1, since=log_offset):
            fails.append("expected a '[PUSH] targets=1 ' log line")

        print(
            f"[offline_recipient_push] asserted payload={payload} ttl={push.ttl} "
            f"aud={push.vapid.get('aud')}"
        )
    finally:
        await sender.close()
    return fails


# ---------------------------------------------------------------------------
# 2. active_recipient_no_push
# ---------------------------------------------------------------------------


async def active_recipient_no_push(
    server: NotifServer, fps: list, recorder: SignalRecorder
) -> list[str]:
    """A recipient who is connected AND recently active (<30s) must NOT
    receive a push. Confirms the active-viewer suppression in the
    push_targets filter: services/companion/chat_ws.py excludes a uid from push_targets
    when it is in connected_uids and `now - _last_ws_activity[uid] <= 30`.
    Connecting itself stamps fresh activity (ConnectionManager.connect), so
    the recipient is "active" for the next 30s with no extra event needed.
    """
    fails: list[str] = []
    push_fps = fps[0]
    room_id, sender_id, recipient_id = _setup_sender_recipient(
        server, "active-recipient-no-push"
    )
    sub = server.inject_chat_subscription(recipient_id, push_fps)

    sender = WSClient(server.ws_base, server.create_session(sender_id))
    recipient = WSClient(server.ws_base, server.create_session(recipient_id))
    try:
        await recipient.connect()
        await recipient.join_room(room_id)
        recorder.record("ws:recipient", "connected", {})
        await asyncio.sleep(0.2)

        await sender.connect()
        await sender.join_room(room_id)
        await asyncio.sleep(0.3)

        log_offset = _log_offset(server)
        text = "active viewer should not get a push"
        await sender.send_message(room_id, text)
        recorder.record("ws:sender", "send_message", {"text": text})

        try:
            await push_fps.wait_for(sub.sub_id, count=1, timeout=1.5)
            fails.append(
                "a push was captured for an actively-connected recipient "
                "(active-viewer suppression did not hold)"
            )
        except TimeoutError:
            recorder.record("fps", "no_push_confirmed", {"sub_id": sub.sub_id})

        if not _has_push_targets_log(server, 0, since=log_offset):
            fails.append("expected a '[PUSH] targets=0 ' log line")

        print(
            f"[active_recipient_no_push] asserted no push reached sub_id={sub.sub_id} "
            f"within 1.5s of send"
        )
    finally:
        await sender.close()
        await recipient.close()
    return fails


# ---------------------------------------------------------------------------
# 3. idle_recipient_push
# ---------------------------------------------------------------------------


async def idle_recipient_push(
    server: NotifServer, fps: list, recorder: SignalRecorder
) -> list[str]:
    """A recipient connected but idle IS a push target.

    Idleness is forced deterministically via `POST /chat/api/push/idle`
    (harness.post_idle_beacon), which zeroes `manager._last_ws_activity` for
    the user server-side -- the same signal the real client's sendBeacon
    sends on tab-hide, per services/companion/chat_api.py's chat_push_idle. Avoids a real
    30-second sleep while still exercising the exact idle-detection code
    path (the push_targets filter in handle_chat_ws).
    """
    fails: list[str] = []
    push_fps = fps[0]
    room_id, sender_id, recipient_id = _setup_sender_recipient(
        server, "idle-recipient-push"
    )
    sub = server.inject_chat_subscription(recipient_id, push_fps)
    recipient_token = server.create_session(recipient_id)

    sender = WSClient(server.ws_base, server.create_session(sender_id))
    recipient = WSClient(server.ws_base, recipient_token)
    try:
        await recipient.connect()
        await recipient.join_room(room_id)
        await asyncio.sleep(0.2)

        await post_idle_beacon(server.base_url, recipient_token)
        recorder.record("harness", "idle_beacon_posted", {})

        await sender.connect()
        await sender.join_room(room_id)
        await asyncio.sleep(0.3)

        log_offset = _log_offset(server)
        text = "idle recipient should get a push"
        await sender.send_message(room_id, text)
        recorder.record("ws:sender", "send_message", {"text": text})

        pushes = await push_fps.wait_for(sub.sub_id, count=1, timeout=8.0)
        push = pushes[0]
        recorder.record(
            "fps", "push_captured", {"sub_id": sub.sub_id}, t=push.received_at
        )

        if push.decrypt_error:
            fails.append(f"decrypt_error: {push.decrypt_error}")
        payload = push.payload or {}
        if text not in (payload.get("body") or ""):
            fails.append(f"body did not carry message text: {payload.get('body')!r}")
        if not _has_push_targets_log(server, 1, since=log_offset):
            fails.append("expected a '[PUSH] targets=1 ' log line")

        try:
            recorder.assert_sequence(
                ["idle_beacon_posted", "send_message", "push_captured"]
            )
            recorder.assert_within(
                "idle_beacon_posted", "push_captured", max_seconds=8.0
            )
        except AssertionError as e:
            fails.append(str(e))

        print(
            f"[idle_recipient_push] asserted push after idle beacon, payload={payload}"
        )
    finally:
        await sender.close()
        await recipient.close()
    return fails


# ---------------------------------------------------------------------------
# 4. debounce_silent_escalation
# ---------------------------------------------------------------------------


async def debounce_silent_escalation(
    server: NotifServer, fps: list, recorder: SignalRecorder
) -> list[str]:
    """Two rapid sends to the same offline recipient: the first push is loud
    (silent=False); the rapid follow-up is coalesced by _push_or_defer's
    debounce and delivered later, by its own background flush task, with
    silent=True.

    Per services/companion/chat_ws.py's _push_or_defer: the debounce window is 10s only
    until a push has actually gone out for this (user, room) key; once
    `_push_sent[key]` is set (right after the first real send), the window
    for any later call within 1800s widens to 60s. So the second send here
    is deferred and its own flush task does not fire until ~60s after the
    first push. This is inherent to the production window and cannot be
    shortened without editing server code, so this scenario genuinely takes
    close to a minute of wall-clock time -- there is no faster deterministic
    way to observe the real escalation path.
    """
    fails: list[str] = []
    push_fps = fps[0]
    room_id, sender_id, recipient_id = _setup_sender_recipient(
        server, "debounce-silent-escalation"
    )
    sub = server.inject_chat_subscription(recipient_id, push_fps)

    sender = WSClient(server.ws_base, server.create_session(sender_id))
    try:
        await sender.connect()
        await sender.join_room(room_id)
        await asyncio.sleep(0.3)

        await sender.send_message(room_id, "debounce message one")
        recorder.record("ws:sender", "send_message_1", {})
        await asyncio.sleep(0.5)
        await sender.send_message(room_id, "debounce message two follow-up")
        recorder.record("ws:sender", "send_message_2", {})

        pushes = await push_fps.wait_for(sub.sub_id, count=2, timeout=75.0)
        for p in pushes:
            recorder.record(
                "fps", "push_captured", {"sub_id": sub.sub_id}, t=p.received_at
            )

        if len(pushes) < 2:
            fails.append(f"expected 2 pushes, got {len(pushes)}")
        else:
            p0, p1 = pushes[0], pushes[1]
            if p0.decrypt_error:
                fails.append(f"first push decrypt_error: {p0.decrypt_error}")
            if p1.decrypt_error:
                fails.append(f"second push decrypt_error: {p1.decrypt_error}")
            payload0 = p0.payload or {}
            payload1 = p1.payload or {}
            if payload0.get("silent") is not False:
                fails.append(
                    f"first push expected silent=False, got {payload0.get('silent')!r}"
                )
            if payload1.get("silent") is not True:
                fails.append(
                    f"second (escalated) push expected silent=True, got "
                    f"{payload1.get('silent')!r}"
                )
            print(
                f"[debounce_silent_escalation] push1.silent={payload0.get('silent')!r} "
                f"push2.silent={payload1.get('silent')!r}"
            )
    finally:
        await sender.close()
    return fails


# ---------------------------------------------------------------------------
# 5. dead_endpoint_pruned
# ---------------------------------------------------------------------------


async def dead_endpoint_pruned(
    server: NotifServer, fps: list, recorder: SignalRecorder
) -> list[str]:
    """A subscription whose push endpoint returns 410 Gone must be pruned
    from chat_push_subscriptions.

    Confirms services/companion/chat_ws.py's _do_send_push WebPushException handler
    (status_code in (404, 410) -> delete_push_subscription_by_endpoint).
    """
    fails: list[str] = []
    push_fps = fps[0]
    room_id, sender_id, recipient_id = _setup_sender_recipient(
        server, "dead-endpoint-pruned"
    )
    sub = server.inject_chat_subscription(recipient_id, push_fps)
    push_fps.set_dead(sub.sub_id, 410)
    recorder.record("harness", "sub_marked_dead", {"sub_id": sub.sub_id})

    if server.chat_sub_count(recipient_id) != 1:
        fails.append(
            f"fixture setup: expected exactly 1 subscription before send, "
            f"got {server.chat_sub_count(recipient_id)}"
        )

    sender = WSClient(server.ws_base, server.create_session(sender_id))
    try:
        await sender.connect()
        await sender.join_room(room_id)
        await asyncio.sleep(0.3)

        await sender.send_message(room_id, "dead endpoint pruning test")
        recorder.record("ws:sender", "send_message", {})

        await push_fps.wait_for(sub.sub_id, count=1, timeout=8.0)
        recorder.record("fps", "push_attempted_410", {"sub_id": sub.sub_id})

        pruned = await _poll_until(
            lambda: server.chat_sub_count(recipient_id) == 0, timeout=8.0
        )
        if pruned:
            recorder.record("harness", "sub_pruned", {})
        else:
            fails.append(
                f"subscription row not pruned after 410; chat_sub_count="
                f"{server.chat_sub_count(recipient_id)}"
            )

        try:
            recorder.assert_sequence(
                ["sub_marked_dead", "push_attempted_410", "sub_pruned"]
            )
        except AssertionError as e:
            fails.append(str(e))

        print(
            f"[dead_endpoint_pruned] asserted chat_sub_count(recipient)="
            f"{server.chat_sub_count(recipient_id)} after 410"
        )
    finally:
        await sender.close()
    return fails


# ---------------------------------------------------------------------------
# 6. vapid_isolation
# ---------------------------------------------------------------------------


async def vapid_isolation(
    server: NotifServer, fps: list, recorder: SignalRecorder
) -> list[str]:
    """One recipient with THREE subscriptions on THREE distinct FPS origins
    (standing in for FCM/Apple/Mozilla). One send must produce three pushes,
    each carrying a VAPID `aud` equal to its OWN origin, and the three auds
    must be pairwise distinct.

    This is the anti-poisoning invariant documented in services/companion/chat_ws.py's
    _do_send_push: pywebpush mutates the vapid_claims dict it is given,
    stamping the first endpoint's origin as `aud`, so a shared dict across
    the subscription loop would poison every later push with the first
    service's aud. The fix is `vapid_claims=dict(vapid_claims)` per call.
    """
    fails: list[str] = []
    if len(fps) < 3:
        fails.append(
            f"vapid_isolation requires 3 FakePushService instances, got {len(fps)}"
        )
        return fails

    room_id, sender_id, recipient_id = _setup_sender_recipient(
        server, "vapid-isolation"
    )
    subs = [server.inject_chat_subscription(recipient_id, f) for f in fps]
    origins = [f.origin for f in fps]
    if len(set(origins)) != 3:
        fails.append(f"expected 3 distinct FPS origins, got {origins}")
    recorder.record("harness", "subs_injected", {"origins": origins})

    sender = WSClient(server.ws_base, server.create_session(sender_id))
    try:
        await sender.connect()
        await sender.join_room(room_id)
        await asyncio.sleep(0.3)

        await sender.send_message(room_id, "vapid isolation test")
        recorder.record("ws:sender", "send_message", {})

        auds: list[str | None] = []
        for f, sub in zip(fps, subs):
            pushes = await f.wait_for(sub.sub_id, count=1, timeout=8.0)
            push = pushes[0]
            recorder.record(
                "fps",
                "push_captured",
                {"sub_id": sub.sub_id, "origin": f.origin},
                t=push.received_at,
            )
            if push.decrypt_error:
                fails.append(f"{f.origin}: decrypt_error: {push.decrypt_error}")
            aud = push.vapid.get("aud")
            auds.append(aud)
            if aud != f.origin:
                fails.append(f"{f.origin}: VAPID aud expected {f.origin}, got {aud!r}")

        if len(set(auds)) != 3:
            fails.append(
                f"expected 3 distinct auds across the three pushes, got {auds}"
            )

        print(f"[vapid_isolation] asserted per-origin auds={auds}")
    finally:
        await sender.close()
    return fails


# ---------------------------------------------------------------------------
# 7. pending_not_pushed
# ---------------------------------------------------------------------------


async def pending_not_pushed(
    server: NotifServer, fps: list, recorder: SignalRecorder
) -> list[str]:
    """A message stuck at moderation_status='pending' must never appear in a
    push body, and must never inflate `count` or `total_unread`.

    Implemented (not skipped): a real is_moderated room cannot be held
    pending deterministically here (the isolated server has no
    OPENAI_API_KEY, see harness.SENSITIVE_ENV_KEYS, so the AI moderation
    layers silently pass and a real message resolves 'pending' -> 'approved'
    almost instantly). Instead this inserts a message row directly with
    moderation_status='pending' -- the same direct-DB-write fixture pattern
    harness.py itself uses for subscriptions/sessions -- and then sends one
    real, immediately-approved message. It asserts the pending row's text
    never leaks into the resulting push and never inflates the counts that
    services/companion/chat_ws.py's _do_send_push and chat_db.get_unread_counts compute
    (both filter "moderation_status != 'pending'").
    """
    fails: list[str] = []
    push_fps = fps[0]
    room_id, sender_id, recipient_id = _setup_sender_recipient(
        server, "pending-not-pushed"
    )
    sub = server.inject_chat_subscription(recipient_id, push_fps)

    pending_text = "PENDING SHOULD NOT APPEAR"
    _insert_pending_message(server, room_id, sender_id, pending_text)
    recorder.record("harness", "pending_message_inserted", {})

    sender = WSClient(server.ws_base, server.create_session(sender_id))
    try:
        await sender.connect()
        await sender.join_room(room_id)
        await asyncio.sleep(0.3)

        visible_text = "visible approved message"
        await sender.send_message(room_id, visible_text)
        recorder.record("ws:sender", "send_message", {"text": visible_text})

        pushes = await push_fps.wait_for(sub.sub_id, count=1, timeout=8.0)
        push = pushes[0]
        recorder.record(
            "fps", "push_captured", {"sub_id": sub.sub_id}, t=push.received_at
        )

        if push.decrypt_error:
            fails.append(f"decrypt_error: {push.decrypt_error}")
        payload = push.payload or {}
        body = payload.get("body") or ""
        if pending_text in body:
            fails.append(f"pending message text leaked into push body: {body!r}")
        if visible_text not in body:
            fails.append(f"push body did not carry the approved message text: {body!r}")
        if payload.get("count") != 1:
            fails.append(
                f"count expected 1 (pending message must not count), got "
                f"{payload.get('count')!r}"
            )
        if payload.get("total_unread") != 1:
            fails.append(
                f"total_unread expected 1 (pending message must not count), got "
                f"{payload.get('total_unread')!r}"
            )

        print(f"[pending_not_pushed] asserted pending text excluded, payload={payload}")
    finally:
        await sender.close()
    return fails


# ---------------------------------------------------------------------------
# Registry consumed by run.py
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, dict] = {
    "offline_recipient_push": {"fn": offline_recipient_push, "fps_count": 1},
    "active_recipient_no_push": {"fn": active_recipient_no_push, "fps_count": 1},
    "idle_recipient_push": {"fn": idle_recipient_push, "fps_count": 1},
    "debounce_silent_escalation": {"fn": debounce_silent_escalation, "fps_count": 1},
    "dead_endpoint_pruned": {"fn": dead_endpoint_pruned, "fps_count": 1},
    "vapid_isolation": {"fn": vapid_isolation, "fps_count": 3},
    "pending_not_pushed": {"fn": pending_not_pushed, "fps_count": 1},
}
