"""Tests for chat WebSocket server: rooms, messaging, presence, moderation integration."""

import asyncio
import json
import sqlite3
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from chat_db import (
    init_chat_db,
    create_user,
    create_session,
    create_room,
    get_room,
    get_room_messages,
    get_strike_count,
    is_banned,
    create_message,
    purge_expired_messages,
    find_or_create_dm,
    get_pending_reports,
    save_push_subscription,
)
from chat_moderation import moderate_message
import chat_ws
from chat_ws import ConnectionManager, handle_chat_ws, manager as global_manager


class _UnclosableConnection:
    """Wraps a test sqlite connection so chat_ws's db.close() calls are no-ops."""

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


async def _run_ws(ws, token, event_id, db):
    """Drive handle_chat_ws end-to-end against a patched in-memory db, then
    drain any background tasks it scheduled (moderation, push) before returning."""
    with patch("chat_ws.get_chat_db", return_value=_UnclosableConnection(db)):
        await handle_chat_ws(ws, token, event_id)
        for _ in range(5):
            pending = [
                t
                for t in asyncio.all_tasks()
                if t is not asyncio.current_task() and not t.done()
            ]
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_chat_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def event_id():
    return "test-event"


@pytest.fixture
def user1(db):
    return create_user(db, "google", "g-1", "Alice", "fp-1")


@pytest.fixture
def user2(db):
    return create_user(db, "apple", "a-2", "Bob", "fp-2")


@pytest.fixture
def session1(db, user1):
    return create_session(db, user1["id"])


@pytest.fixture
def session2(db, user2):
    return create_session(db, user2["id"])


@pytest.fixture
def stage_room(db, event_id):
    return create_room(db, "grand-hall", event_id, "stage", "Grand Hall")


@pytest.fixture
def mgr():
    return ConnectionManager()


class FakeWebSocket:
    def __init__(self):
        self.sent: list[str] = []
        self.to_receive: list[str] = []
        self.accepted = False
        self.closed = False
        self.close_code = None
        self._recv_index = 0

    async def accept(self):
        self.accepted = True

    async def send_text(self, data: str):
        self.sent.append(data)

    async def receive_text(self) -> str:
        if self._recv_index < len(self.to_receive):
            msg = self.to_receive[self._recv_index]
            self._recv_index += 1
            return msg
        raise Exception("WebSocketDisconnect")

    async def close(self, code: int = 1000, reason: str = ""):
        self.closed = True
        self.close_code = code

    def get_events(self) -> list[dict]:
        return [json.loads(s) for s in self.sent]

    def get_events_by_type(self, event_type: str) -> list[dict]:
        return [e for e in self.get_events() if e.get("event") == event_type]


class BlockingWS(FakeWebSocket):
    """A FakeWebSocket that stays connected until a sentinel is queued, so a
    test can run two overlapping live connections (e.g. a recipient who must
    remain connected while a DM is created and a message is sent)."""

    def __init__(self):
        super().__init__()
        self.queue = asyncio.Queue()

    async def receive_text(self) -> str:
        item = await self.queue.get()
        if item is None:
            raise Exception("WebSocketDisconnect")
        return item


# --- ConnectionManager ---


class TestConnectionManager:
    @pytest.mark.asyncio
    async def test_connect_and_join(self, mgr):
        ws = FakeWebSocket()
        await mgr.connect(ws, "user-1", "c1")
        await mgr.join_room("room-1", "user-1", "c1", "Alice")
        online = mgr.get_online_users("room-1")
        assert len(online) == 1
        assert online[0]["display_name"] == "Alice"

    @pytest.mark.asyncio
    async def test_disconnect_leaves_rooms(self, mgr):
        ws = FakeWebSocket()
        await mgr.connect(ws, "user-1", "c1")
        await mgr.join_room("room-1", "user-1", "c1", "Alice")
        _, left = mgr.disconnect("c1")
        assert "room-1" in left
        assert mgr.get_online_users("room-1") == []

    @pytest.mark.asyncio
    async def test_broadcast_to_room(self, mgr):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect(ws1, "u1", "c1")
        await mgr.connect(ws2, "u2", "c2")
        await mgr.join_room("r1", "u1", "c1", "A")
        await mgr.join_room("r1", "u2", "c2", "B")
        await mgr.broadcast_to_room("r1", {"event": "test", "data": "hello"})
        assert len(ws1.sent) >= 1
        assert len(ws2.sent) >= 1

    @pytest.mark.asyncio
    async def test_broadcast_excludes_sender(self, mgr):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect(ws1, "u1", "c1")
        await mgr.connect(ws2, "u2", "c2")
        await mgr.join_room("r1", "u1", "c1", "A")
        await mgr.join_room("r1", "u2", "c2", "B")
        presence_count = len(ws1.sent)
        await mgr.broadcast_to_room("r1", {"event": "test"}, exclude_conn="c1")
        assert len(ws1.sent) == presence_count
        assert len(ws2.get_events_by_type("test")) == 1

    @pytest.mark.asyncio
    async def test_send_to_user(self, mgr):
        ws = FakeWebSocket()
        await mgr.connect(ws, "u1", "c1")
        await mgr.send_to_user("u1", {"event": "hello"})
        assert ws.get_events_by_type("hello")

    @pytest.mark.asyncio
    async def test_rate_limit(self, mgr):
        for _ in range(5):
            assert mgr.check_rate_limit("u1", max_msgs=5, window_secs=10)
        assert not mgr.check_rate_limit("u1", max_msgs=5, window_secs=10)

    @pytest.mark.asyncio
    async def test_leave_room(self, mgr):
        ws = FakeWebSocket()
        await mgr.connect(ws, "u1", "c1")
        await mgr.join_room("r1", "u1", "c1", "A")
        await mgr.leave_room("r1", "c1")
        assert mgr.get_online_users("r1") == []

    @pytest.mark.asyncio
    async def test_multiple_rooms(self, mgr):
        ws = FakeWebSocket()
        await mgr.connect(ws, "u1", "c1")
        await mgr.join_room("r1", "u1", "c1", "A")
        await mgr.join_room("r2", "u1", "c1", "A")
        assert len(mgr.get_online_users("r1")) == 1
        assert len(mgr.get_online_users("r2")) == 1
        _, left = mgr.disconnect("c1")
        assert "r1" in left and "r2" in left


# --- Message Flow ---


class TestMessageFlow:
    @pytest.mark.asyncio
    async def test_send_and_receive(
        self, db, user1, user2, session1, session2, stage_room, event_id
    ):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()

        mgr = ConnectionManager()
        await mgr.connect(ws1, user1["id"], "c1")
        await mgr.connect(ws2, user2["id"], "c2")
        await mgr.join_room("grand-hall", user1["id"], "c1", "Alice")
        await mgr.join_room("grand-hall", user2["id"], "c2", "Bob")

        content = json.dumps({"text": "hello everyone"})
        msg = create_message(db, "grand-hall", user1["id"], "text", content)

        await mgr.broadcast_to_room(
            "grand-hall",
            {
                "event": "message",
                "id": msg["id"],
                "room_id": "grand-hall",
                "user_id": user1["id"],
                "display_name": "Alice",
                "type": "text",
                "content": content,
                "created_at": msg["created_at"],
            },
        )

        msgs1 = ws1.get_events_by_type("message")
        msgs2 = ws2.get_events_by_type("message")
        assert len(msgs1) == 1
        assert len(msgs2) == 1
        assert msgs2[0]["display_name"] == "Alice"
        assert json.loads(msgs2[0]["content"])["text"] == "hello everyone"

    @pytest.mark.asyncio
    async def test_message_stored_in_db(self, db, user1, stage_room):
        content = json.dumps({"text": "persisted"})
        create_message(db, "grand-hall", user1["id"], "text", content)
        messages = get_room_messages(db, "grand-hall")
        assert len(messages) == 1
        assert messages[0]["content"] == content

    @pytest.mark.asyncio
    async def test_typing_indicator(self, db):
        mgr = ConnectionManager()
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect(ws1, "u1", "c1")
        await mgr.connect(ws2, "u2", "c2")
        await mgr.join_room("r1", "u1", "c1", "A")
        await mgr.join_room("r1", "u2", "c2", "B")

        await mgr.broadcast_to_room(
            "r1",
            {
                "event": "typing",
                "room_id": "r1",
                "user_id": "u1",
                "active": True,
            },
            exclude_conn="c1",
        )

        typing_events = ws2.get_events_by_type("typing")
        assert len(typing_events) == 1
        assert typing_events[0]["active"] is True
        assert not ws1.get_events_by_type("typing")


# --- Presence ---


class TestPresence:
    @pytest.mark.asyncio
    async def test_join_broadcasts_presence(self, db):
        mgr = ConnectionManager()
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect(ws1, "u1", "c1")
        await mgr.connect(ws2, "u2", "c2")
        await mgr.join_room("r1", "u1", "c1", "A")
        await mgr.join_room("r1", "u2", "c2", "B")

        presence = ws1.get_events_by_type("presence")
        assert len(presence) == 1
        assert presence[0]["user_id"] == "u2"
        assert presence[0]["online"] is True

    @pytest.mark.asyncio
    async def test_leave_broadcasts_offline(self, db):
        mgr = ConnectionManager()
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect(ws1, "u1", "c1")
        await mgr.connect(ws2, "u2", "c2")
        await mgr.join_room("r1", "u1", "c1", "A")
        await mgr.join_room("r1", "u2", "c2", "B")

        await mgr.leave_room("r1", "c2")

        offline = [e for e in ws1.get_events_by_type("presence") if not e["online"]]
        assert len(offline) == 1
        assert offline[0]["user_id"] == "u2"


# --- Rate Limiting ---


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_rate_limit_resets(self):
        mgr = ConnectionManager()
        for _ in range(5):
            mgr.check_rate_limit("u1", max_msgs=5, window_secs=0.01)
        await asyncio.sleep(0.02)
        assert mgr.check_rate_limit("u1", max_msgs=5, window_secs=0.01)


# --- Moderation in flow ---


class TestModerationInFlow:
    @pytest.mark.asyncio
    async def test_blocked_message_not_broadcast(self, db, user1, stage_room):
        mgr = ConnectionManager()
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect(ws1, user1["id"], "c1")
        u2 = create_user(db, "apple", "a-2", "Bob", "fp-2")
        await mgr.connect(ws2, u2["id"], "c2")
        await mgr.join_room("grand-hall", user1["id"], "c1", "Alice")
        await mgr.join_room("grand-hall", u2["id"], "c2", "Bob")

        with patch(
            "chat_moderation.check_openai_moderation",
            new_callable=AsyncMock,
            return_value=None,
        ):
            mod = await moderate_message(db, user1["id"], "got molly?")
            assert not mod["allowed"]

        assert get_room_messages(db, "grand-hall") == []

    @pytest.mark.asyncio
    async def test_clean_message_stored_and_broadcast(self, db, user1, stage_room):
        mgr = ConnectionManager()
        ws = FakeWebSocket()
        await mgr.connect(ws, user1["id"], "c1")
        await mgr.join_room("grand-hall", user1["id"], "c1", "Alice")

        with patch(
            "chat_moderation.check_openai_moderation",
            new_callable=AsyncMock,
            return_value=None,
        ):
            mod = await moderate_message(db, user1["id"], "great set!")
            assert mod["allowed"]

        content = json.dumps({"text": "great set!"})
        msg = create_message(db, "grand-hall", user1["id"], "text", content)
        await mgr.broadcast_to_room(
            "grand-hall",
            {
                "event": "message",
                "id": msg["id"],
                "room_id": "grand-hall",
                "user_id": user1["id"],
                "display_name": "Alice",
                "type": "text",
                "content": content,
                "created_at": msg["created_at"],
            },
        )

        assert len(get_room_messages(db, "grand-hall")) == 1
        assert ws.get_events_by_type("message")


# --- Purge Notifications ---


class TestPurgeNotifications:
    @pytest.mark.asyncio
    async def test_expired_messages_notified(self, db, user1, stage_room):
        mgr = ConnectionManager()
        ws = FakeWebSocket()
        await mgr.connect(ws, user1["id"], "c1")
        await mgr.join_room("grand-hall", user1["id"], "c1", "Alice")

        msg = create_message(
            db, "grand-hall", user1["id"], "text", '{"text":"bye"}', ttl_minutes=0
        )
        expired = purge_expired_messages(db)

        for batch in expired:
            await mgr.broadcast_to_room(
                batch["room_id"],
                {
                    "event": "messages_expired",
                    "room_id": batch["room_id"],
                    "message_ids": batch["message_ids"],
                },
            )

        expire_events = ws.get_events_by_type("messages_expired")
        assert len(expire_events) == 1
        assert msg["id"] in expire_events[0]["message_ids"]


# --- E2EE ---


class TestIsE2eeContent:
    def test_valid_envelope(self):
        content = json.dumps({"e2ee": True, "v": 1, "ct": "abc"})
        assert chat_ws._is_e2ee_content(content) is True

    def test_valid_v2_envelope(self):
        content = json.dumps(
            {"e2ee": True, "v": 2, "sd": "a" * 32, "ct": "abc", "keys": {"a" * 32: "x"}}
        )
        assert chat_ws._is_e2ee_content(content) is True

    def test_plain_text_json(self):
        content = json.dumps({"text": "hello everyone"})
        assert chat_ws._is_e2ee_content(content) is False

    def test_invalid_json(self):
        assert chat_ws._is_e2ee_content("not json{{{") is False

    def test_empty_string(self):
        assert chat_ws._is_e2ee_content("") is False


class TestE2eeSendMessage:
    @pytest.mark.asyncio
    async def test_e2ee_rejected_outside_dm(
        self, db, user1, session1, stage_room, event_id
    ):
        envelope = json.dumps({"e2ee": True, "v": 1, "ct": "ciphertext-blob"})
        ws = FakeWebSocket()
        ws.to_receive = [
            json.dumps(
                {
                    "event": "send_message",
                    "room_id": "grand-hall",
                    "type": "text",
                    "content": envelope,
                    "temp_id": "t1",
                }
            )
        ]
        await _run_ws(ws, session1["token"], event_id, db)

        rejected = ws.get_events_by_type("message_rejected")
        assert len(rejected) == 1
        assert (
            rejected[0]["reason"]
            == "Encrypted messages are only supported in direct messages"
        )
        assert rejected[0]["temp_id"] == "t1"
        assert get_room_messages(db, "grand-hall") == []

    @pytest.mark.asyncio
    async def test_e2ee_v2_rejected_outside_dm(
        self, db, user1, session1, stage_room, event_id
    ):
        envelope = json.dumps(
            {
                "e2ee": True,
                "v": 2,
                "sd": "a" * 32,
                "ct": "ciphertext-blob",
                "keys": {"a" * 32: "x"},
            }
        )
        ws = FakeWebSocket()
        ws.to_receive = [
            json.dumps(
                {
                    "event": "send_message",
                    "room_id": "grand-hall",
                    "type": "text",
                    "content": envelope,
                    "temp_id": "t1v2",
                }
            )
        ]
        await _run_ws(ws, session1["token"], event_id, db)

        rejected = ws.get_events_by_type("message_rejected")
        assert len(rejected) == 1
        assert (
            rejected[0]["reason"]
            == "Encrypted messages are only supported in direct messages"
        )
        assert get_room_messages(db, "grand-hall") == []

    @pytest.mark.asyncio
    async def test_e2ee_length_allowance_in_dm(
        self, db, user1, user2, session1, event_id
    ):
        room_id = find_or_create_dm(db, event_id, user1["id"], user2["id"])
        envelope = json.dumps({"e2ee": True, "v": 1, "ct": "A" * 3500})
        assert 1020 < len(envelope) < 6000

        ws = FakeWebSocket()
        ws.to_receive = [
            json.dumps(
                {
                    "event": "send_message",
                    "room_id": room_id,
                    "type": "text",
                    "content": envelope,
                    "temp_id": "t2",
                }
            )
        ]
        await _run_ws(ws, session1["token"], event_id, db)

        assert ws.get_events_by_type("message_rejected") == []
        assert len(ws.get_events_by_type("message_acked")) == 1
        stored = get_room_messages(db, room_id)
        assert len(stored) == 1
        assert stored[0]["content"] == envelope

    @pytest.mark.asyncio
    async def test_e2ee_v2_envelope_accepted_in_dm(
        self, db, user1, user2, session1, event_id
    ):
        # 12 device slots (~118 chars each, matching the size math in
        # docs/e2ee-multidevice.md -- only ~1.4 KB, well under any limit) plus
        # a padded ciphertext to deliberately land between the old v1 text
        # ceiling (4000) and the new v2 ceiling (6000).
        keys = {f"{i:032x}": "B" * 80 for i in range(12)}
        envelope = json.dumps(
            {"e2ee": True, "v": 2, "sd": "a" * 32, "ct": "A" * 3600, "keys": keys}
        )
        assert 4000 < len(envelope) < 6000
        room_id = find_or_create_dm(db, event_id, user1["id"], user2["id"])

        ws = FakeWebSocket()
        ws.to_receive = [
            json.dumps(
                {
                    "event": "send_message",
                    "room_id": room_id,
                    "type": "text",
                    "content": envelope,
                    "temp_id": "t2v2",
                }
            )
        ]
        await _run_ws(ws, session1["token"], event_id, db)

        assert ws.get_events_by_type("message_rejected") == []
        assert len(ws.get_events_by_type("message_acked")) == 1
        stored = get_room_messages(db, room_id)
        assert len(stored) == 1
        assert stored[0]["content"] == envelope

    @pytest.mark.asyncio
    async def test_e2ee_length_still_bounded(
        self, db, user1, user2, session1, event_id
    ):
        room_id = find_or_create_dm(db, event_id, user1["id"], user2["id"])
        # v2's +2000 headroom raised the text ceiling to 6000 -- this must
        # exceed that, not the old v1 ceiling of 4000.
        envelope = json.dumps({"e2ee": True, "v": 1, "ct": "A" * 6500})
        assert len(envelope) > 6000

        ws = FakeWebSocket()
        ws.to_receive = [
            json.dumps(
                {
                    "event": "send_message",
                    "room_id": room_id,
                    "type": "text",
                    "content": envelope,
                    "temp_id": "t3",
                }
            )
        ]
        await _run_ws(ws, session1["token"], event_id, db)

        rejected = ws.get_events_by_type("message_rejected")
        assert len(rejected) == 1
        assert rejected[0]["reason"] == "Message too long."
        assert get_room_messages(db, room_id) == []

    @pytest.mark.asyncio
    async def test_media_url_check_skipped_for_e2ee_dm_image(
        self, db, user1, user2, session1, event_id
    ):
        room_id = find_or_create_dm(db, event_id, user1["id"], user2["id"])
        envelope = json.dumps({"e2ee": True, "v": 1, "ct": "image-ciphertext-blob"})

        ws = FakeWebSocket()
        ws.to_receive = [
            json.dumps(
                {
                    "event": "send_message",
                    "room_id": room_id,
                    "type": "image",
                    "content": envelope,
                    "temp_id": "t4",
                }
            )
        ]
        await _run_ws(ws, session1["token"], event_id, db)

        rejected = ws.get_events_by_type("message_rejected")
        assert not any(r["reason"] == "Invalid media URL." for r in rejected)
        assert len(get_room_messages(db, room_id)) == 1

    @pytest.mark.asyncio
    async def test_e2ee_non_text_length_allowance_increased(
        self, db, user1, user2, session1, event_id
    ):
        # Before v2, non-text E2EE envelopes got no extra allowance (2000
        # flat) -- a multi-device image envelope this size would have been
        # rejected. v2 extends the +2000 headroom to every message type.
        room_id = find_or_create_dm(db, event_id, user1["id"], user2["id"])
        keys = {f"{i:032x}": "B" * 80 for i in range(10)}
        envelope = json.dumps(
            {"e2ee": True, "v": 2, "sd": "a" * 32, "ct": "A" * 900, "keys": keys}
        )
        assert 2000 < len(envelope) < 4000

        ws = FakeWebSocket()
        ws.to_receive = [
            json.dumps(
                {
                    "event": "send_message",
                    "room_id": room_id,
                    "type": "image",
                    "content": envelope,
                    "temp_id": "t5",
                }
            )
        ]
        await _run_ws(ws, session1["token"], event_id, db)

        assert ws.get_events_by_type("message_rejected") == []
        assert len(get_room_messages(db, room_id)) == 1

    @pytest.mark.asyncio
    async def test_e2ee_non_text_length_still_bounded(
        self, db, user1, user2, session1, event_id
    ):
        room_id = find_or_create_dm(db, event_id, user1["id"], user2["id"])
        envelope = json.dumps({"e2ee": True, "v": 1, "ct": "A" * 4200})
        assert len(envelope) > 4000

        ws = FakeWebSocket()
        ws.to_receive = [
            json.dumps(
                {
                    "event": "send_message",
                    "room_id": room_id,
                    "type": "image",
                    "content": envelope,
                    "temp_id": "t6",
                }
            )
        ]
        await _run_ws(ws, session1["token"], event_id, db)

        rejected = ws.get_events_by_type("message_rejected")
        assert len(rejected) == 1
        assert rejected[0]["reason"] == "Message too long."
        assert get_room_messages(db, room_id) == []

    @pytest.mark.asyncio
    async def test_media_url_check_enforced_for_plaintext_image(
        self, db, user1, user2, session1, event_id
    ):
        room_id = find_or_create_dm(db, event_id, user1["id"], user2["id"])
        bad_content = json.dumps({"url": "https://evil.example.com/x.webp"})

        ws = FakeWebSocket()
        ws.to_receive = [
            json.dumps(
                {
                    "event": "send_message",
                    "room_id": room_id,
                    "type": "image",
                    "content": bad_content,
                    "temp_id": "t5",
                }
            )
        ]
        await _run_ws(ws, session1["token"], event_id, db)

        rejected = ws.get_events_by_type("message_rejected")
        assert len(rejected) == 1
        assert rejected[0]["reason"] == "Invalid media URL."
        assert get_room_messages(db, room_id) == []


class TestDmModerationSkipped:
    @pytest.mark.asyncio
    async def test_moderation_skipped_for_dm(self, db, user1, user2, event_id):
        room_id = find_or_create_dm(db, event_id, user1["id"], user2["id"])
        room = get_room(db, room_id)
        assert room["is_moderated"] == 0

        mgr = ConnectionManager()
        ws = FakeWebSocket()
        await mgr.connect(ws, user1["id"], "c1")

        text = "got molly?"
        content = json.dumps({"text": text})
        msg = create_message(db, room_id, user1["id"], "text", content)

        with patch("chat_ws.get_chat_db", return_value=_UnclosableConnection(db)):
            await chat_ws._moderate_and_broadcast(
                mgr,
                room_id,
                user1["id"],
                "c1",
                "Alice",
                "alice",
                0,
                "",
                msg,
                "text",
                content,
                text,
                None,
                None,
                ws,
                is_moderated=bool(room["is_moderated"]),
            )
            for _ in range(5):
                pending = [
                    t
                    for t in asyncio.all_tasks()
                    if t is not asyncio.current_task() and not t.done()
                ]
                if not pending:
                    break
                await asyncio.gather(*pending, return_exceptions=True)

        assert len(get_room_messages(db, room_id)) == 1
        assert ws.get_events_by_type("message_removed") == []


class TestDmBadgeRegistration:
    """A DM created mid-session must badge-register both participants, even
    one who connected before the room existed and never sent a message.

    These drive handle_chat_ws directly (not via the _run_ws helper) because
    two connections need to stay alive concurrently, and _run_ws's
    all-tasks drain loop would otherwise deadlock against the outer task
    awaiting a create_task-wrapped _run_ws call."""

    @pytest.mark.asyncio
    async def test_recipient_connected_before_dm_creation_gets_badge_update(
        self, db, user1, user2, session1, session2, event_id
    ):
        with patch("chat_ws.get_chat_db", return_value=_UnclosableConnection(db)):
            ws_b = BlockingWS()
            b_task = asyncio.create_task(
                handle_chat_ws(ws_b, session2["token"], event_id)
            )
            await asyncio.sleep(0.2)

            room_id = find_or_create_dm(db, event_id, user1["id"], user2["id"])
            # Mirrors the badge registration POST /dms now performs (see
            # chat_api.create_dm) for both participants at room-creation time.
            global_manager.user_badge_rooms.setdefault(user1["id"], set()).add(room_id)
            global_manager.user_badge_rooms.setdefault(user2["id"], set()).add(room_id)

            ws_a = BlockingWS()
            a_task = asyncio.create_task(
                handle_chat_ws(ws_a, session1["token"], event_id)
            )
            await asyncio.sleep(0.2)

            envelope = json.dumps({"e2ee": True, "v": 1, "ct": "Zm9vYmFyYmF6cXV4"})
            await ws_a.queue.put(json.dumps({"event": "join_room", "room_id": room_id}))
            await ws_a.queue.put(
                json.dumps(
                    {
                        "event": "send_message",
                        "room_id": room_id,
                        "type": "text",
                        "content": envelope,
                        "temp_id": "tmp_x",
                    }
                )
            )
            await asyncio.sleep(0.3)

            badge_events = ws_b.get_events_by_type("badge_update")
            assert len(badge_events) == 1
            assert badge_events[0]["room_id"] == room_id
            assert badge_events[0]["type"] == "dm"

            await ws_a.queue.put(None)
            await ws_b.queue.put(None)
            await asyncio.gather(a_task, b_task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_join_room_registers_dm_badge_for_participant_who_never_sent(
        self, db, user1, user2, session2, event_id
    ):
        with patch("chat_ws.get_chat_db", return_value=_UnclosableConnection(db)):
            ws = BlockingWS()
            task = asyncio.create_task(handle_chat_ws(ws, session2["token"], event_id))
            await asyncio.sleep(0.2)

            room_id = find_or_create_dm(db, event_id, user1["id"], user2["id"])
            assert room_id not in global_manager.user_badge_rooms.get(
                user2["id"], set()
            )

            await ws.queue.put(json.dumps({"event": "join_room", "room_id": room_id}))
            await asyncio.sleep(0.2)

            assert room_id in global_manager.user_badge_rooms.get(user2["id"], set())

            await ws.queue.put(None)
            await task

    @pytest.mark.asyncio
    async def test_join_room_registers_meetup_badge_for_attendee_who_never_sent(
        self, db, user1, user2, session1, event_id
    ):
        from chat_db import create_meetup, join_meetup

        meetup = create_meetup(
            db, user2["id"], event_id, None, "Sunrise set", "2026-07-11T06:00:00"
        )
        join_meetup(db, meetup["id"], user1["id"])
        assert meetup["id"] not in global_manager.user_badge_rooms.get(
            user1["id"], set()
        )

        with patch("chat_ws.get_chat_db", return_value=_UnclosableConnection(db)):
            ws = BlockingWS()
            task = asyncio.create_task(handle_chat_ws(ws, session1["token"], event_id))
            await asyncio.sleep(0.2)

            await ws.queue.put(
                json.dumps({"event": "join_room", "room_id": meetup["id"]})
            )
            await asyncio.sleep(0.2)

            assert meetup["id"] in global_manager.user_badge_rooms.get(
                user1["id"], set()
            )

            await ws.queue.put(None)
            await task


class TestE2eeReplySnippet:
    def test_reply_snippet_empty_for_e2ee_message(self, db, user1, stage_room):
        envelope = json.dumps({"e2ee": True, "v": 1, "ct": "xyz"})
        orig = create_message(db, "grand-hall", user1["id"], "text", envelope)
        snippet = chat_ws._build_reply_snippet(db, orig["id"])
        assert snippet is not None
        assert snippet["text"] == ""

    def test_reply_snippet_normal_for_plaintext_message(self, db, user1, stage_room):
        content = json.dumps({"text": "hello there"})
        orig = create_message(db, "grand-hall", user1["id"], "text", content)
        snippet = chat_ws._build_reply_snippet(db, orig["id"])
        assert snippet["text"] == "hello there"


class TestE2eeReport:
    @pytest.mark.asyncio
    async def test_report_uses_client_content_for_e2ee_and_marks_unverified(
        self, db, user1, user2, session1, event_id
    ):
        room_id = find_or_create_dm(db, event_id, user1["id"], user2["id"])
        envelope = json.dumps({"e2ee": True, "v": 1, "ct": "gibberish"})
        msg = create_message(db, room_id, user2["id"], "text", envelope)

        ws = FakeWebSocket()
        ws.to_receive = [
            json.dumps(
                {
                    "event": "report_message",
                    "message_id": msg["id"],
                    "reason": "harassment",
                    "message_content": '{"text":"actual decrypted abuse"}',
                }
            )
        ]
        await _run_ws(ws, session1["token"], event_id, db)

        assert ws.get_events_by_type("report_confirmed")
        reports = get_pending_reports(db)
        assert len(reports) == 1
        assert "actual decrypted abuse" in reports[0]["message_snapshot"]
        assert reports[0]["unverified"] == 1

    @pytest.mark.asyncio
    async def test_report_ignores_client_content_for_plaintext(
        self, db, user1, user2, session1, event_id
    ):
        room_id = find_or_create_dm(db, event_id, user1["id"], user2["id"])
        content = json.dumps({"text": "real message"})
        msg = create_message(db, room_id, user2["id"], "text", content)

        ws = FakeWebSocket()
        ws.to_receive = [
            json.dumps(
                {
                    "event": "report_message",
                    "message_id": msg["id"],
                    "reason": "harassment",
                    "message_content": '{"text":"forged content"}',
                }
            )
        ]
        await _run_ws(ws, session1["token"], event_id, db)

        reports = get_pending_reports(db)
        assert len(reports) == 1
        assert "real message" in reports[0]["message_snapshot"]
        assert "forged content" not in reports[0]["message_snapshot"]
        assert reports[0]["unverified"] == 0

    @pytest.mark.asyncio
    async def test_report_e2ee_without_client_content_uses_placeholder(
        self, db, user1, user2, session1, event_id
    ):
        room_id = find_or_create_dm(db, event_id, user1["id"], user2["id"])
        envelope = json.dumps({"e2ee": True, "v": 1, "ct": "gibberish"})
        msg = create_message(db, room_id, user2["id"], "text", envelope)

        ws = FakeWebSocket()
        ws.to_receive = [
            json.dumps(
                {
                    "event": "report_message",
                    "message_id": msg["id"],
                    "reason": "harassment",
                }
            )
        ]
        await _run_ws(ws, session1["token"], event_id, db)

        reports = get_pending_reports(db)
        assert len(reports) == 1
        assert (
            "[encrypted message - no content provided]"
            in reports[0]["message_snapshot"]
        )
        assert reports[0]["unverified"] == 0


class TestDmPushPreview:
    """The debounced-push DB-refetch path must never leak the E2EE envelope."""

    def _setup_push(self, db, monkeypatch, recipient_id):
        save_push_subscription(
            db, recipient_id, "https://push.example/ep1", "p256", "auth"
        )
        monkeypatch.setenv("VAPID_PRIVATE_KEY", "BEGIN test key")

    @pytest.mark.asyncio
    async def test_dm_refetch_preview_is_generic(
        self, db, user1, user2, event_id, monkeypatch
    ):
        room_id = find_or_create_dm(db, event_id, user1["id"], user2["id"])
        envelope = json.dumps({"e2ee": True, "v": 1, "ct": "Zm9vYmFy"})
        msg = create_message(db, room_id, user1["id"], "text", envelope)
        self._setup_push(db, monkeypatch, user2["id"])

        with patch("chat_ws.get_chat_db", return_value=_UnclosableConnection(db)):
            with patch("pywebpush.webpush") as wp:
                await chat_ws._do_send_push(
                    user2["id"],
                    room_id,
                    "dm",
                    "DM",
                    "",
                    "",
                    msg["id"],
                    silent=False,
                    push_index=1,
                )

        assert wp.called
        payload = wp.call_args.kwargs["data"]
        parsed = json.loads(payload)
        assert parsed["title"] == "Alice"
        assert parsed["body"] == "Sent you a message"
        assert "e2ee" not in payload

    @pytest.mark.asyncio
    async def test_group_refetch_preview_keeps_content(
        self, db, user1, user2, event_id, stage_room, monkeypatch
    ):
        from chat_db import join_room_membership

        room_id = stage_room["id"]
        join_room_membership(db, user2["id"], room_id)
        msg = create_message(
            db, room_id, user1["id"], "text", json.dumps({"text": "hello there"})
        )
        self._setup_push(db, monkeypatch, user2["id"])

        with patch("chat_ws.get_chat_db", return_value=_UnclosableConnection(db)):
            with patch("pywebpush.webpush") as wp:
                await chat_ws._do_send_push(
                    user2["id"],
                    room_id,
                    "stage",
                    "Grand Hall",
                    "",
                    "",
                    msg["id"],
                    silent=False,
                    push_index=1,
                )

        assert wp.called
        parsed = json.loads(wp.call_args.kwargs["data"])
        assert parsed["title"] == "#Grand Hall"
        assert "hello there" in parsed["body"]
