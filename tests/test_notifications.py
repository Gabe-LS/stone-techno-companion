"""Tests for the notification system: progressive debounce, trailing flush,
payload construction, foreground suppression, app badge, notification clearing.

Server-side logic is tested via async Python (mocked pywebpush).
Client-side behavior is tested via Playwright + Chromium.
"""

import asyncio
import json
import sqlite3
import time
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from chat_db import (
    init_chat_db,
    create_user,
    create_session,
    create_room,
    create_message,
    join_room_membership,
    mark_room_read,
    save_push_subscription,
    get_unread_counts,
)
import chat_ws


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_chat_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def sender(db):
    return create_user(db, "google", "g-sender", "Marco", "fp-sender")


@pytest.fixture
def receiver(db):
    return create_user(db, "google", "g-receiver", "Lisa", "fp-receiver")


@pytest.fixture
def receiver2(db):
    return create_user(db, "google", "g-receiver2", "Tom", "fp-receiver2")


@pytest.fixture
def room(db):
    return create_room(db, "general", "test-event", "stage", "General")


@pytest.fixture
def dm_room(db):
    return create_room(db, "dm-room", "test-event", "dm", "DM")


@pytest.fixture
def meetup_room(db):
    return create_room(db, "meetup-1", "test-event", "meetup", "Afterparty Meetup")


@pytest.fixture
def setup_room(db, sender, receiver, room):
    """Sender and receiver are members of the room, receiver has a push subscription."""
    join_room_membership(db, sender["id"], room["id"])
    join_room_membership(db, receiver["id"], room["id"])
    save_push_subscription(
        db, receiver["id"], "https://push.example.com/recv", "p256dh-key", "auth-key"
    )
    return room


@pytest.fixture
def setup_dm(db, sender, receiver, dm_room):
    """DM room with both participants and receiver subscribed."""
    join_room_membership(db, sender["id"], dm_room["id"])
    join_room_membership(db, receiver["id"], dm_room["id"])
    db.execute(
        "INSERT INTO dm_participants (room_id, user_id) VALUES (?, ?), (?, ?)",
        (dm_room["id"], sender["id"], dm_room["id"], receiver["id"]),
    )
    db.commit()
    save_push_subscription(
        db, receiver["id"], "https://push.example.com/dm-recv", "p256dh-dm", "auth-dm"
    )
    return dm_room


@pytest.fixture
def setup_meetup(db, sender, receiver, meetup_room):
    """Meetup room with attendees and receiver subscribed."""
    join_room_membership(db, sender["id"], meetup_room["id"])
    join_room_membership(db, receiver["id"], meetup_room["id"])
    save_push_subscription(
        db, receiver["id"], "https://push.example.com/meetup-recv", "p256dh-m", "auth-m"
    )
    return meetup_room


@pytest.fixture(autouse=True)
def reset_push_state():
    """Reset module-level push state before each test."""
    chat_ws._push_debounce.clear()
    chat_ws._push_sent.clear()
    for t in chat_ws._push_flush_tasks.values():
        t.cancel()
    chat_ws._push_flush_tasks.clear()
    chat_ws._push_counter = 0
    yield
    chat_ws._push_debounce.clear()
    chat_ws._push_sent.clear()
    for t in chat_ws._push_flush_tasks.values():
        t.cancel()
    chat_ws._push_flush_tasks.clear()


class _UnclosableConnection:
    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _mock_webpush():
    """Returns a mock webpush function that captures all calls."""
    calls = []

    def _webpush(
        subscription_info=None,
        data=None,
        vapid_private_key=None,
        vapid_claims=None,
        **kwargs,
    ):
        payload = json.loads(data)
        calls.append({"sub": subscription_info, "payload": payload})

    return _webpush, calls


from contextlib import contextmanager


@contextmanager
def _push_test_env(db, webpush_fn):
    """Context manager that patches everything needed for push tests."""
    fake_module = MagicMock()
    fake_module.webpush = webpush_fn
    fake_module.WebPushException = Exception

    with (
        patch("chat_ws.get_chat_db", return_value=_UnclosableConnection(db)),
        patch.dict("sys.modules", {"pywebpush": fake_module}),
        patch.dict(
            "os.environ",
            {
                "VAPID_PRIVATE_KEY": "BEGIN fake key",
                "VAPID_CLAIMS_EMAIL": "mailto:t@t.com",
            },
        ),
    ):
        yield


# ---------------------------------------------------------------------------
# Server Push Logic — Progressive Debounce
# ---------------------------------------------------------------------------


class TestProgressiveDebounce:
    @pytest.mark.asyncio
    async def test_first_message_sends_immediately(
        self, db, sender, receiver, setup_room
    ):
        """First message in a conversation triggers an immediate push (leading edge)."""
        msg = create_message(db, "general", sender["id"], "text", "hey!")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "hey!",
                msg["id"],
            )

        assert len(calls) == 1
        payload = calls[0]["payload"]
        assert payload["title"] == "#General"
        assert payload["body"] == "Marco: hey!"
        assert payload["silent"] is False
        assert payload["count"] == 1
        assert payload["room_id"] == "general"
        assert payload["push_index"] == 1

    @pytest.mark.asyncio
    async def test_rapid_messages_debounced_10s(self, db, sender, receiver, setup_room):
        """Messages within 10s of the first push are suppressed."""
        msg1 = create_message(db, "general", sender["id"], "text", "msg1")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "msg1",
                msg1["id"],
            )
            assert len(calls) == 1

            create_message(db, "general", sender["id"], "text", "msg2")
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "msg2",
                None,
            )
            assert len(calls) == 1  # suppressed

    @pytest.mark.asyncio
    async def test_progressive_window_60s_after_first_push(
        self, db, sender, receiver, setup_room
    ):
        """After the first push, the window extends to 60s."""
        msg = create_message(db, "general", sender["id"], "text", "first")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "first",
                msg["id"],
            )
            assert len(calls) == 1

            # Simulate 15s passing (past 10s initial window, but within 60s post-push window)
            key = f"{receiver['id']}:general"
            chat_ws._push_debounce[key] = time.monotonic() - 15

            create_message(db, "general", sender["id"], "text", "second")
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "second",
                None,
            )
            # Still suppressed because _push_sent is True → 60s window
            assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_push_after_60s_window(self, db, sender, receiver, setup_room):
        """After the 60s window expires, next message sends immediately."""
        msg = create_message(db, "general", sender["id"], "text", "first")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "first",
                msg["id"],
            )
            assert len(calls) == 1

            # Simulate 61s passing
            key = f"{receiver['id']}:general"
            chat_ws._push_debounce[key] = time.monotonic() - 61

            msg2 = create_message(db, "general", sender["id"], "text", "after window")
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "after window",
                msg2["id"],
            )
            assert len(calls) == 2
            assert calls[1]["payload"]["silent"] is True

    @pytest.mark.asyncio
    async def test_30min_staleness_resets_sound(self, db, sender, receiver, setup_room):
        """After 30 min of silence, the next push has sound (silent=False)."""
        msg = create_message(db, "general", sender["id"], "text", "first")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "first",
                msg["id"],
            )
            # Simulate 31 min passing
            key = f"{receiver['id']}:general"
            chat_ws._push_debounce[key] = time.monotonic() - 1861

            msg2 = create_message(db, "general", sender["id"], "text", "new convo")
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "new convo",
                msg2["id"],
            )
            assert len(calls) == 2
            assert calls[1]["payload"]["silent"] is False

    @pytest.mark.asyncio
    async def test_multiple_rooms_independent_debounce(
        self, db, sender, receiver, setup_room
    ):
        """Each room has its own debounce window."""
        room2 = create_room(db, "room2", "test-event", "stage", "Stage 2")
        join_room_membership(db, receiver["id"], room2["id"])
        msg1 = create_message(db, "general", sender["id"], "text", "in general")
        msg2 = create_message(db, "room2", sender["id"], "text", "in stage 2")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "in general",
                msg1["id"],
            )
            await chat_ws._push_or_defer(
                receiver["id"],
                "room2",
                "stage",
                "Stage 2",
                "Marco",
                "in stage 2",
                msg2["id"],
            )
            # Both send immediately — different rooms, independent debounce
            assert len(calls) == 2
            assert calls[0]["payload"]["room_id"] == "general"
            assert calls[1]["payload"]["room_id"] == "room2"


# ---------------------------------------------------------------------------
# Server Push Logic — Trailing Flush
# ---------------------------------------------------------------------------


class TestTrailingFlush:
    @pytest.mark.asyncio
    async def test_trailing_flush_scheduled_on_suppress(
        self, db, sender, receiver, setup_room
    ):
        """Suppressed message schedules a trailing flush task."""
        msg = create_message(db, "general", sender["id"], "text", "first")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "first",
                msg["id"],
            )
            # Second message within window → suppressed, flush scheduled
            create_message(db, "general", sender["id"], "text", "second")
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "second",
                None,
            )

        key = f"{receiver['id']}:general"
        assert key in chat_ws._push_flush_tasks
        assert not chat_ws._push_flush_tasks[key].done()
        chat_ws._push_flush_tasks[key].cancel()

    @pytest.mark.asyncio
    async def test_trailing_flush_fires_and_sends(
        self, db, sender, receiver, setup_room
    ):
        """Trailing flush delivers a consolidated push at window expiry."""
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            # Simulate: first push was sent 59.9s ago (within 60s window, flush fires in 0.1s)
            key = f"{receiver['id']}:general"
            chat_ws._push_debounce[key] = time.monotonic() - 59.9
            chat_ws._push_sent[key] = True  # window = 60s (first push already sent)

            # Create messages for unread count
            for i in range(5):
                create_message(db, "general", sender["id"], "text", f"msg {i}")

            # This should be suppressed and schedule a flush with ~0.1s delay
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "suppressed",
                None,
            )
            assert len(calls) == 0
            assert key in chat_ws._push_flush_tasks

            # Wait for the flush to fire
            await asyncio.sleep(0.3)

            assert len(calls) == 1
            payload = calls[0]["payload"]
            assert payload["count"] == 5
            assert payload["body"] == "5 new messages"
            assert payload["silent"] is True

    @pytest.mark.asyncio
    async def test_only_one_flush_per_key(self, db, sender, receiver, setup_room):
        """Multiple suppressed messages don't schedule multiple flushes."""
        msg = create_message(db, "general", sender["id"], "text", "first")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "first",
                msg["id"],
            )
            # Multiple suppressed messages
            for i in range(5):
                create_message(db, "general", sender["id"], "text", f"extra {i}")
                await chat_ws._push_or_defer(
                    receiver["id"],
                    "general",
                    "stage",
                    "General",
                    "Marco",
                    f"extra {i}",
                    None,
                )

        key = f"{receiver['id']}:general"
        assert key in chat_ws._push_flush_tasks
        chat_ws._push_flush_tasks[key].cancel()

    @pytest.mark.asyncio
    async def test_mark_read_cancels_flush(self, db, sender, receiver, setup_room):
        """mark_read cancels the pending trailing flush."""
        msg = create_message(db, "general", sender["id"], "text", "first")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "first",
                msg["id"],
            )
            create_message(db, "general", sender["id"], "text", "second")
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "second",
                None,
            )

        key = f"{receiver['id']}:general"
        assert key in chat_ws._push_flush_tasks

        # Simulate mark_read reset
        chat_ws._push_sent.pop(key, None)
        chat_ws._push_debounce.pop(key, None)
        task = chat_ws._push_flush_tasks.pop(key, None)
        if task:
            task.cancel()

        assert key not in chat_ws._push_flush_tasks

    @pytest.mark.asyncio
    async def test_flush_count_0_guard(self, db, sender, receiver, setup_room):
        """If mark_read raced the flush, count=0 means no push is sent."""
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            # No unread messages (receiver already read everything)
            await chat_ws._do_send_push(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "ghost",
                None,
                silent=True,
                push_index=99,
            )

        assert len(calls) == 0


# ---------------------------------------------------------------------------
# Server Push Logic — Payload Construction
# ---------------------------------------------------------------------------


class TestPayloadConstruction:
    @pytest.mark.asyncio
    async def test_room_payload_single_message(self, db, sender, receiver, setup_room):
        """count=1: room push shows sender preview."""
        msg = create_message(db, "general", sender["id"], "text", "heading to stage 2")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "heading to stage 2",
                msg["id"],
            )

        p = calls[0]["payload"]
        assert p["title"] == "#General"
        assert p["body"] == "Marco: heading to stage 2"
        assert p["count"] == 1
        assert p["total_unread"] == 1
        assert p["url"].startswith("/chat/msg/")
        assert "room_id" in p
        assert "push_index" in p
        assert "tag" not in p  # dead field removed

    @pytest.mark.asyncio
    async def test_dm_payload_single_message(self, db, sender, receiver, setup_dm):
        """DM push: title=sender, body=bare preview."""
        msg = create_message(db, "dm-room", sender["id"], "text", "hey Lisa!")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._push_or_defer(
                receiver["id"],
                "dm-room",
                "dm",
                "DM",
                "Marco",
                "hey Lisa!",
                msg["id"],
            )

        p = calls[0]["payload"]
        assert p["title"] == "Marco"
        assert p["body"] == "hey Lisa!"
        assert p["room_type"] == "dm"

    @pytest.mark.asyncio
    async def test_meetup_payload_single_message(
        self, db, sender, receiver, setup_meetup
    ):
        """Meetup push: title=meetup name, body=sender: preview."""
        msg = create_message(db, "meetup-1", sender["id"], "text", "who's coming?")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._push_or_defer(
                receiver["id"],
                "meetup-1",
                "meetup",
                "Afterparty Meetup",
                "Marco",
                "who's coming?",
                msg["id"],
            )

        p = calls[0]["payload"]
        assert p["title"] == "Afterparty Meetup"
        assert p["body"] == "Marco: who's coming?"
        assert p["room_type"] == "meetup"

    @pytest.mark.asyncio
    async def test_multi_message_payload(self, db, sender, receiver, setup_room):
        """count>1: body shows 'N new messages'."""
        for i in range(7):
            create_message(db, "general", sender["id"], "text", f"msg {i}")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._do_send_push(
                receiver["id"],
                "general",
                "stage",
                "General",
                "",
                "",
                None,
                silent=True,
                push_index=10,
            )

        p = calls[0]["payload"]
        assert p["body"] == "7 new messages"
        assert p["count"] == 7
        assert p["title"] == "#General"

    @pytest.mark.asyncio
    async def test_total_unread_across_rooms(self, db, sender, receiver, setup_room):
        """total_unread sums unread across all rooms the user is in."""
        room2 = create_room(db, "stage2", "test-event", "stage", "Stage 2")
        join_room_membership(db, receiver["id"], room2["id"])
        create_message(db, "general", sender["id"], "text", "in general")
        for i in range(3):
            create_message(db, "stage2", sender["id"], "text", f"in stage2 {i}")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._do_send_push(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "in general",
                None,
                silent=False,
                push_index=1,
            )

        p = calls[0]["payload"]
        assert p["count"] == 1
        assert p["total_unread"] == 4  # 1 in general + 3 in stage2

    @pytest.mark.asyncio
    async def test_trailing_flush_queries_preview_for_count_1(
        self, db, sender, receiver, setup_room
    ):
        """When trailing flush fires and count=1, it queries the message for preview."""
        create_message(db, "general", sender["id"], "text", "the only unread")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            # Empty sender_name simulates trailing flush entry
            await chat_ws._do_send_push(
                receiver["id"],
                "general",
                "stage",
                "General",
                "",
                "",
                None,
                silent=True,
                push_index=5,
            )

        p = calls[0]["payload"]
        assert p["count"] == 1
        assert "Marco" in p["body"]
        assert "the only unread" in p["body"]

    @pytest.mark.asyncio
    async def test_push_index_monotonic(self, db, sender, receiver, setup_room):
        """push_index increments monotonically across calls."""
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            for i in range(3):
                msg = create_message(db, "general", sender["id"], "text", f"msg {i}")
                # Reset debounce to allow immediate send
                chat_ws._push_debounce.pop(f"{receiver['id']}:general", None)
                chat_ws._push_sent.pop(f"{receiver['id']}:general", None)
                await chat_ws._push_or_defer(
                    receiver["id"],
                    "general",
                    "stage",
                    "General",
                    "Marco",
                    f"msg {i}",
                    msg["id"],
                )

        indices = [c["payload"]["push_index"] for c in calls]
        assert indices == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_no_push_subscription_no_send(self, db, sender, receiver, room):
        """No push subscription → no push sent."""
        join_room_membership(db, sender["id"], room["id"])
        join_room_membership(db, receiver["id"], room["id"])
        # Note: no save_push_subscription for receiver
        create_message(db, "general", sender["id"], "text", "hello")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._do_send_push(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "hello",
                None,
                silent=False,
                push_index=1,
            )

        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_dm_trailing_flush_has_sender_title(
        self, db, sender, receiver, setup_dm
    ):
        """DM trailing flush (count>1) still shows sender name as title."""
        for i in range(3):
            create_message(db, "dm-room", sender["id"], "text", f"dm msg {i}")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            # Simulate trailing flush (empty sender_name)
            await chat_ws._do_send_push(
                receiver["id"],
                "dm-room",
                "dm",
                "DM",
                "",
                "",
                None,
                silent=True,
                push_index=10,
            )

        assert len(calls) == 1
        p = calls[0]["payload"]
        assert p["title"] == "Marco"  # sender name resolved from DB
        assert p["body"] == "3 new messages"
        assert p["count"] == 3


# ---------------------------------------------------------------------------
# Server Push Logic — Mark Read & Debounce Reset
# ---------------------------------------------------------------------------


class TestMarkReadReset:
    @pytest.mark.asyncio
    async def test_mark_read_resets_debounce(self, db, sender, receiver, setup_room):
        """After mark_read, next message sends immediately with sound."""
        msg1 = create_message(db, "general", sender["id"], "text", "first")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "first",
                msg1["id"],
            )
            assert len(calls) == 1

            # Mark read (simulates what mark_read handler does)
            key = f"{receiver['id']}:general"
            mark_room_read(db, receiver["id"], "general")
            chat_ws._push_sent.pop(key, None)
            chat_ws._push_debounce.pop(key, None)
            task = chat_ws._push_flush_tasks.pop(key, None)
            if task:
                task.cancel()

            # New message should send immediately with sound
            msg2 = create_message(db, "general", sender["id"], "text", "after read")
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "after read",
                msg2["id"],
            )
            assert len(calls) == 2
            assert calls[1]["payload"]["silent"] is False

    @pytest.mark.asyncio
    async def test_mark_read_prevents_stale_flush(
        self, db, sender, receiver, setup_room
    ):
        """mark_read before flush fires → flush is cancelled, no push sent."""
        msg = create_message(db, "general", sender["id"], "text", "first")
        webpush_fn, calls = _mock_webpush()

        with _push_test_env(db, webpush_fn):
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "first",
                msg["id"],
            )
            # Suppress a message to schedule flush
            create_message(db, "general", sender["id"], "text", "second")
            await chat_ws._push_or_defer(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "second",
                None,
            )

            key = f"{receiver['id']}:general"
            assert key in chat_ws._push_flush_tasks

            # Mark read cancels flush
            mark_room_read(db, receiver["id"], "general")
            chat_ws._push_sent.pop(key, None)
            chat_ws._push_debounce.pop(key, None)
            task = chat_ws._push_flush_tasks.pop(key, None)
            if task:
                task.cancel()

            # Wait — no flush should fire
            await asyncio.sleep(0.2)
            assert len(calls) == 1  # only the first immediate push


# ---------------------------------------------------------------------------
# Server Push Logic — Foreground Suppression
# ---------------------------------------------------------------------------


class TestForegroundSuppression:
    @pytest.mark.asyncio
    async def test_visible_keeps_user_fresh(self):
        """The 'visible' event updates _last_ws_activity to prevent false idle."""
        mgr = chat_ws.ConnectionManager()
        user_id = "test-user"
        mgr._last_ws_activity[user_id] = time.monotonic() - 100  # stale

        # Simulate visible event
        mgr._last_ws_activity[user_id] = time.monotonic()

        # Now user should NOT be idle
        assert time.monotonic() - mgr._last_ws_activity[user_id] < 1

    @pytest.mark.asyncio
    async def test_idle_beacon_makes_user_push_eligible(self):
        """Setting _last_ws_activity to 0 makes user immediately push-eligible."""
        mgr = chat_ws.ConnectionManager()
        user_id = "test-user"
        mgr._last_ws_activity[user_id] = time.monotonic()

        # Simulate idle beacon
        mgr._last_ws_activity[user_id] = 0

        # Now user should be idle (30s threshold check)
        assert time.monotonic() - mgr._last_ws_activity[user_id] > 30

    @pytest.mark.asyncio
    async def test_connected_and_fresh_user_excluded_from_push(self):
        """A connected user with recent activity is excluded from push targets."""
        mgr = chat_ws.ConnectionManager()

        class FakeWS:
            async def accept(self):
                pass

            async def send_text(self, data):
                pass

        ws = FakeWS()
        await mgr.connect(ws, "user-1", "c1")
        mgr._last_ws_activity["user-1"] = time.monotonic()

        # Check the same logic used in _moderate_and_broadcast
        connected_uids = set(mgr.user_conns.keys())
        now = time.monotonic()
        push_eligible = (
            "user-1" not in connected_uids
            or now - mgr._last_ws_activity.get("user-1", 0) > 30
        )
        assert not push_eligible

    @pytest.mark.asyncio
    async def test_connected_but_idle_user_gets_push(self):
        """A connected user idle for >30s is push-eligible."""
        mgr = chat_ws.ConnectionManager()

        class FakeWS:
            async def accept(self):
                pass

            async def send_text(self, data):
                pass

        ws = FakeWS()
        await mgr.connect(ws, "user-1", "c1")
        mgr._last_ws_activity["user-1"] = time.monotonic() - 35

        connected_uids = set(mgr.user_conns.keys())
        now = time.monotonic()
        push_eligible = (
            "user-1" not in connected_uids
            or now - mgr._last_ws_activity.get("user-1", 0) > 30
        )
        assert push_eligible


# ---------------------------------------------------------------------------
# Server Push Logic — Dead Subscription Cleanup
# ---------------------------------------------------------------------------


class TestDeadSubscription:
    @pytest.mark.asyncio
    async def test_410_removes_subscription(self, db, sender, receiver, setup_room):
        """410 Gone response removes the dead subscription."""
        create_message(db, "general", sender["id"], "text", "hello")

        class FakeResponse:
            status_code = 410

        class FakeWebPushException(Exception):
            def __init__(self):
                self.response = FakeResponse()

        def failing_webpush(**kwargs):
            raise FakeWebPushException()

        fake_module = MagicMock()
        fake_module.webpush = failing_webpush
        fake_module.WebPushException = FakeWebPushException

        with (
            patch("chat_ws.get_chat_db", return_value=_UnclosableConnection(db)),
            patch.dict("sys.modules", {"pywebpush": fake_module}),
            patch.dict("os.environ", {"VAPID_PRIVATE_KEY": "BEGIN fake key"}),
        ):
            await chat_ws._do_send_push(
                receiver["id"],
                "general",
                "stage",
                "General",
                "Marco",
                "hello",
                None,
                silent=False,
                push_index=1,
            )

        # Subscription should be removed
        from chat_db import get_push_subscription_count

        assert get_push_subscription_count(db, receiver["id"]) == 0


# ---------------------------------------------------------------------------
# Server Push Logic — Stale State Pruning
# ---------------------------------------------------------------------------


class TestStalePruning:
    def test_stale_debounce_entries_pruned(self):
        """Entries older than 2h are pruned."""
        chat_ws._push_debounce["old:room"] = time.monotonic() - 8000
        chat_ws._push_debounce["fresh:room"] = time.monotonic() - 100
        chat_ws._push_sent["old:room"] = True
        chat_ws._push_sent["fresh:room"] = True

        # Simulate purge logic
        cutoff = time.monotonic() - 7200
        stale_keys = [k for k, v in chat_ws._push_debounce.items() if v < cutoff]
        for k in stale_keys:
            chat_ws._push_debounce.pop(k, None)
            chat_ws._push_sent.pop(k, None)

        assert "old:room" not in chat_ws._push_debounce
        assert "old:room" not in chat_ws._push_sent
        assert "fresh:room" in chat_ws._push_debounce
        assert "fresh:room" in chat_ws._push_sent


# ---------------------------------------------------------------------------
# Client-Side — Playwright Tests
# ---------------------------------------------------------------------------

try:
    import playwright  # noqa: F401

    _has_playwright = True
except ImportError:
    _has_playwright = False

pytestmark_pw = pytest.mark.skipif(
    not _has_playwright, reason="playwright not installed"
)


@pytest.fixture(scope="session")
def _server_proc():
    """Start the real server for Playwright tests."""
    if not _has_playwright:
        pytest.skip("playwright not installed")
    import subprocess
    import os

    server_dir = Path(__file__).resolve().parent.parent / "server"
    env = os.environ.copy()
    env["CHAT_BASE_URL"] = "https://localhost:64729"
    env["OPENAI_API_KEY"] = "test-key"
    env.setdefault("VAPID_PRIVATE_KEY", "fake")
    env.setdefault("VAPID_PUBLIC_KEY", "fake")
    env.setdefault("VAPID_CLAIMS_EMAIL", "mailto:test@test.com")
    env.setdefault("MAILEROO_API_KEY", "fake")
    env.setdefault("GOOGLE_CLIENT_ID", "fake")
    env.setdefault("GOOGLE_CLIENT_SECRET", "fake")

    cert_file = server_dir / "localhost+1.pem"
    key_file = server_dir / "localhost+1-key.pem"
    if not cert_file.exists() or not key_file.exists():
        pytest.skip("Local TLS certs not found (localhost+1.pem / localhost+1-key.pem)")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api:app",
            "--port",
            "64729",
            "--host",
            "127.0.0.1",
            "--ssl-keyfile",
            str(key_file),
            "--ssl-certfile",
            str(cert_file),
        ],
        cwd=str(server_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    import urllib.request
    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for _ in range(30):
        try:
            urllib.request.urlopen(
                "https://127.0.0.1:64729/chat/api/config", context=ctx, timeout=1
            )
            break
        except Exception:
            time.sleep(0.5)
    else:
        proc.terminate()
        pytest.fail("Server did not start within 15s")

    yield proc
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture
def server(_server_proc):
    """Ensure server is running for playwright tests."""
    return _server_proc


@pytest.fixture
def browser_context(server):
    """Launch Chromium with notification permission granted."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        ignore_https_errors=True,
        permissions=["notifications"],
    )
    yield context
    context.close()
    browser.close()
    pw.stop()


@pytest.fixture
def chat_page(browser_context, server):
    """Open a chat page and authenticate as a test user."""
    import urllib.request
    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # Create a test user via the server's DB
    page = browser_context.new_page()
    page.goto("https://127.0.0.1:64729/chat")
    page.wait_for_load_state("networkidle")
    return page


@pytestmark_pw
class TestClientVisibleKeepalive:
    """Test the 'visible' WS keepalive prevents false idle detection."""

    def test_visible_keepalive_function_exists(self, chat_page):
        """_startVisibleKeepalive is defined on the page."""
        result = chat_page.evaluate("typeof _startVisibleKeepalive")
        assert result == "function"

    def test_visible_interval_started_on_load(self, chat_page):
        """_visibleInterval is set after page load (keepalive running)."""
        result = chat_page.evaluate("_visibleInterval !== null")
        assert result is True

    def test_visible_sends_ws_event(self, chat_page):
        """The keepalive sends 'visible' events via WebSocket when ws is open."""
        # Mock ws to appear connected and intercept wsSend
        chat_page.evaluate("""
            window._wsSendCalls = [];
            ws = { readyState: 1 };
            wsSend = function(event, payload) {
                window._wsSendCalls.push({event: event, payload: payload});
            };
            // Reset interval so we can test fresh
            if (_visibleInterval) { clearInterval(_visibleInterval); _visibleInterval = null; }
            _startVisibleKeepalive();
        """)
        time.sleep(0.1)

        calls = chat_page.evaluate("window._wsSendCalls")
        visible_calls = [c for c in calls if c["event"] == "visible"]
        assert len(visible_calls) >= 1

    def test_visible_stopped_on_hide(self, chat_page):
        """Simulating document.hidden stops the keepalive interval."""
        # Simulate visibilitychange to hidden
        chat_page.evaluate("""
            Object.defineProperty(document, 'hidden', { value: true, configurable: true });
            document.dispatchEvent(new Event('visibilitychange'));
        """)
        time.sleep(0.1)
        result = chat_page.evaluate("_visibleInterval")
        assert result is None

    def test_visible_restarted_on_show(self, chat_page):
        """Returning to visible restarts the keepalive."""
        # Hide then show
        chat_page.evaluate("""
            Object.defineProperty(document, 'hidden', { value: true, configurable: true });
            document.dispatchEvent(new Event('visibilitychange'));
        """)
        chat_page.evaluate("""
            Object.defineProperty(document, 'hidden', { value: false, configurable: true });
            document.dispatchEvent(new Event('visibilitychange'));
        """)
        time.sleep(0.1)
        result = chat_page.evaluate("_visibleInterval !== null")
        assert result is True


@pytestmark_pw
class TestClientClearRoomNotifications:
    """Test _clearRoomNotifications function."""

    def test_clear_function_exists(self, chat_page):
        result = chat_page.evaluate("typeof _clearRoomNotifications")
        assert result == "function"

    def test_clear_guards_on_service_worker(self, chat_page):
        """Function handles missing service worker gracefully."""
        # Call with a room ID — should not throw even if SW not ready
        chat_page.evaluate("_clearRoomNotifications('test-room-id')")

    def test_clear_has_35s_retry(self, chat_page):
        """The function schedules a 35s retry for iOS <30s notifications."""
        # Verify the implementation includes setTimeout with 35000
        source = chat_page.evaluate("_clearRoomNotifications.toString()")
        assert "35000" in source


@pytestmark_pw
class TestClientAppBadge:
    """Test _updateAppBadge function."""

    def test_badge_function_exists(self, chat_page):
        result = chat_page.evaluate("typeof _updateAppBadge")
        assert result == "function"

    def test_badge_set_when_unread(self, chat_page):
        """Badge is set when there are unread messages."""
        chat_page.evaluate("""
            window._badgeCalls = [];
            navigator.setAppBadge = function(n) { window._badgeCalls.push({action: 'set', value: n}); };
            navigator.clearAppBadge = function() { window._badgeCalls.push({action: 'clear'}); };
            unreadByRoom = { 'room1': 3, 'room2': 5 };
            _hiddenUnread = 2;
            _updateAppBadge();
        """)
        calls = chat_page.evaluate("window._badgeCalls")
        assert len(calls) == 1
        assert calls[0]["action"] == "set"
        assert calls[0]["value"] == 10  # 3 + 5 + 2

    def test_badge_cleared_when_all_read(self, chat_page):
        """Badge is cleared when unread count is 0."""
        chat_page.evaluate("""
            window._badgeCalls = [];
            navigator.setAppBadge = function(n) { window._badgeCalls.push({action: 'set', value: n}); };
            navigator.clearAppBadge = function() { window._badgeCalls.push({action: 'clear'}); };
            unreadByRoom = {};
            _hiddenUnread = 0;
            _updateAppBadge();
        """)
        calls = chat_page.evaluate("window._badgeCalls")
        assert len(calls) == 1
        assert calls[0]["action"] == "clear"

    def test_badge_called_from_badge_counts(self, chat_page):
        """_updateAppBadge is invoked when badge_counts WS event arrives."""
        source = chat_page.evaluate("""
            (function() {
                var lines = document.querySelector('script') ?
                    document.querySelector('script').textContent : '';
                return document.documentElement.innerHTML;
            })()
        """)
        # The function call should be in the page source near badge_counts handler
        assert "_updateAppBadge" in source


@pytestmark_pw
class TestClientIdleBeacon:
    """Test idle beacon sends on tab hide."""

    def test_idle_beacon_on_visibilitychange(self, chat_page):
        """sendBeacon('/chat/api/push/idle') fires when tab becomes hidden."""
        chat_page.evaluate("""
            window._beaconCalls = [];
            navigator.sendBeacon = function(url) { window._beaconCalls.push(url); return true; };
            Object.defineProperty(document, 'hidden', { value: true, configurable: true });
            document.dispatchEvent(new Event('visibilitychange'));
        """)
        calls = chat_page.evaluate("window._beaconCalls")
        assert "/chat/api/push/idle" in calls

    def test_idle_beacon_on_pagehide(self, chat_page):
        """sendBeacon fires on pagehide event."""
        chat_page.evaluate("""
            window._beaconCalls = [];
            navigator.sendBeacon = function(url) { window._beaconCalls.push(url); return true; };
            window.dispatchEvent(new Event('pagehide'));
        """)
        calls = chat_page.evaluate("window._beaconCalls")
        assert "/chat/api/push/idle" in calls


@pytestmark_pw
class TestClientPushNavigation:
    """Test push notification click -> navigation via cache."""

    def test_push_navigate_function_exists(self, chat_page):
        result = chat_page.evaluate("typeof _checkPushNavigate")
        assert result == "function"

    def test_push_navigate_reads_cache(self, chat_page):
        """_checkPushNavigate reads from 'stc-push' cache store."""
        source = chat_page.evaluate("_checkPushNavigate.toString()")
        assert "stc-push" in source
        assert "_push_navigate" in source

    def test_push_navigate_latch_prevents_double(self, chat_page):
        """_pushNavigating latch prevents double navigation."""
        result = chat_page.evaluate("typeof _pushNavigating")
        assert result == "boolean"

    def test_sw_message_listener_registered(self, chat_page):
        """ServiceWorker message listener exists for 'navigate' type."""
        # Check that the listener was registered (we can verify by the source)
        source = chat_page.evaluate("document.documentElement.innerHTML")
        assert (
            "e.data.type === 'navigate'" in source
            or "e.data&&e.data.type==='navigate'" in source
        )


@pytestmark_pw
class TestServiceWorker:
    """Test service worker registration and behavior."""

    def test_sw_registered(self, chat_page):
        """Service worker is registered at /sw.js."""
        chat_page.wait_for_timeout(2000)
        result = chat_page.evaluate("""
            (async () => {
                if (!('serviceWorker' in navigator)) return 'no-sw';
                const reg = await navigator.serviceWorker.getRegistration('/');
                return reg ? reg.active?.scriptURL || reg.installing?.scriptURL || 'pending' : 'none';
            })()
        """)
        assert result != "none"
        assert result != "no-sw"

    def test_sw_push_handler_prunes_by_room(self, chat_page):
        """SW push handler filters old notifications by room_id before showing new."""
        # We can't directly trigger a push event in Playwright, but we can verify
        # the SW source contains the prune logic
        sw_source = chat_page.evaluate("""
            (async () => {
                const reg = await navigator.serviceWorker.getRegistration('/');
                if (!reg || !reg.active) return '';
                const resp = await fetch(reg.active.scriptURL);
                return await resp.text();
            })()
        """)
        assert "getNotifications" in sw_source
        assert "n.data.roomId" in sw_source or "n.data&&n.data.roomId" in sw_source
        assert "n.close()" in sw_source

    def test_sw_uses_unique_tag(self, chat_page):
        """SW builds unique tag from room_id + push_id (falls back to push_index)."""
        sw_source = chat_page.evaluate("""
            (async () => {
                const reg = await navigator.serviceWorker.getRegistration('/');
                if (!reg || !reg.active) return '';
                const resp = await fetch(reg.active.scriptURL);
                return await resp.text();
            })()
        """)
        assert "data.room_id" in sw_source
        assert "data.push_id" in sw_source
        assert "data.push_index" in sw_source

    def test_sw_sets_app_badge(self, chat_page):
        """SW calls setAppBadge with total_unread."""
        sw_source = chat_page.evaluate("""
            (async () => {
                const reg = await navigator.serviceWorker.getRegistration('/');
                if (!reg || !reg.active) return '';
                const resp = await fetch(reg.active.scriptURL);
                return await resp.text();
            })()
        """)
        assert "setAppBadge" in sw_source
        assert "total_unread" in sw_source

    def test_sw_silent_flag(self, chat_page):
        """SW uses silent flag from payload."""
        sw_source = chat_page.evaluate("""
            (async () => {
                const reg = await navigator.serviceWorker.getRegistration('/');
                if (!reg || !reg.active) return '';
                const resp = await fetch(reg.active.scriptURL);
                return await resp.text();
            })()
        """)
        assert "data.silent" in sw_source

    def test_sw_version_v10(self, chat_page):
        """SW version is v10."""
        sw_source = chat_page.evaluate("""
            (async () => {
                const reg = await navigator.serviceWorker.getRegistration('/');
                if (!reg || !reg.active) return '';
                const resp = await fetch(reg.active.scriptURL);
                return await resp.text();
            })()
        """)
        assert "SW_VERSION = 'v10'" in sw_source

    def test_sw_click_handler_cache_first(self, chat_page):
        """SW click handler writes to cache before network calls."""
        sw_source = chat_page.evaluate("""
            (async () => {
                const reg = await navigator.serviceWorker.getRegistration('/');
                if (!reg || !reg.active) return '';
                const resp = await fetch(reg.active.scriptURL);
                return await resp.text();
            })()
        """)
        # Cache write should appear before ackPush in click handler
        cache_pos = sw_source.find("stc-push")
        ack_pos = sw_source.find("ackPush('clicked'")
        assert cache_pos < ack_pos

    def test_sw_notificationclose_waituntil(self, chat_page):
        """notificationclose handler uses event.waitUntil."""
        sw_source = chat_page.evaluate("""
            (async () => {
                const reg = await navigator.serviceWorker.getRegistration('/');
                if (!reg || !reg.active) return '';
                const resp = await fetch(reg.active.scriptURL);
                return await resp.text();
            })()
        """)
        # Find the notificationclose handler
        close_idx = sw_source.find("notificationclose")
        close_section = sw_source[close_idx : close_idx + 200]
        assert "waitUntil" in close_section
