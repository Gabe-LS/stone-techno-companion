"""Tests for chat database schema and core operations."""

import sqlite3
import pytest
from datetime import datetime, timedelta, timezone

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from chat_db import (
    init_chat_db,
    create_user,
    find_user_by_provider,
    get_user,
    update_display_name,
    delete_user,
    is_muted,
    mute_user,
    create_session,
    get_user_by_token,
    ban_user,
    is_banned,
    create_room,
    get_room,
    get_rooms_by_event,
    seed_event_rooms,
    create_message,
    get_room_messages,
    purge_expired_messages,
    create_meetup,
    join_meetup,
    leave_meetup,
    get_meetup_attendees,
    get_active_meetups,
    purge_expired_meetups,
    find_or_create_dm,
    block_user,
    unblock_user,
    is_blocked,
    create_report,
    get_pending_reports,
    resolve_report,
    purge_old_reports,
    add_strike,
    get_strike_count,
    purge_expired_sessions,
    wipe_all_chat_data,
    hash_email,
    upsert_e2ee_device_key,
    get_e2ee_device_keys,
    prune_stale_devices,
    _migrate_chat_db,
)


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_chat_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def user(db):
    return create_user(db, "google", "google-123", "TestUser", "fp-abc")


@pytest.fixture
def user2(db):
    return create_user(db, "apple", "apple-456", "OtherUser", "fp-def")


@pytest.fixture
def event_id():
    return "test-event-2026"


@pytest.fixture
def stage_room(db, event_id):
    return create_room(db, "grand-hall", event_id, "stage", "Grand Hall")


# --- Users ---


class TestUsers:
    def test_create_user(self, db, user):
        assert user["id"]
        assert user["display_name"] == "TestUser"
        assert user["provider"] == "google"

    def test_find_by_provider(self, db, user):
        found = find_user_by_provider(db, "google", "google-123")
        assert found is not None
        assert found["id"] == user["id"]

    def test_find_by_provider_not_found(self, db):
        assert find_user_by_provider(db, "google", "nonexistent") is None

    def test_duplicate_provider_rejected(self, db, user):
        with pytest.raises(sqlite3.IntegrityError):
            create_user(db, "google", "google-123", "Duplicate")

    def test_get_user(self, db, user):
        found = get_user(db, user["id"])
        assert found is not None
        assert found["display_name"] == "TestUser"

    def test_update_display_name(self, db, user):
        update_display_name(db, user["id"], "NewName")
        found = get_user(db, user["id"])
        assert found["display_name"] == "NewName"

    def test_delete_user(self, db, user):
        delete_user(db, user["id"])
        assert get_user(db, user["id"]) is None

    def test_mute_user(self, db, user):
        assert not is_muted(db, user["id"])
        mute_user(db, user["id"], minutes=30)
        assert is_muted(db, user["id"])

    def test_mute_expires(self, db, user):
        mute_user(db, user["id"], minutes=0)
        assert not is_muted(db, user["id"])


# --- Sessions ---


class TestSessions:
    def test_create_and_lookup(self, db, user):
        session = create_session(db, user["id"])
        assert session["token"]
        found = get_user_by_token(db, session["token"])
        assert found is not None
        assert found["id"] == user["id"]

    def test_invalid_token(self, db):
        assert get_user_by_token(db, "bogus-token") is None

    def test_expired_session(self, db, user):
        session = create_session(db, user["id"])
        db.execute(
            "UPDATE sessions SET expires_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", session["id"]),
        )
        db.commit()
        assert get_user_by_token(db, session["token"]) is None

    def test_purge_expired(self, db, user):
        session = create_session(db, user["id"])
        db.execute(
            "UPDATE sessions SET expires_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", session["id"]),
        )
        db.commit()
        purge_expired_sessions(db)
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


# --- Bans ---


class TestBans:
    def test_ban_by_provider(self, db, user):
        ban_user(db, user["id"], "google", "google-123", "drug dealing")
        ban = is_banned(db, "google", "google-123")
        assert ban is not None
        assert ban["reason"] == "drug dealing"

    def test_not_banned(self, db):
        assert is_banned(db, "google", "clean-user") is None

    def test_ban_by_fingerprint(self, db, user):
        ban_user(db, user["id"], "google", "google-123", "spam", "fp-abc")
        ban = is_banned(db, "apple", "different-id", "fp-abc")
        assert ban is not None

    def test_ban_survives_user_deletion(self, db, user):
        ban_user(db, user["id"], "google", "google-123", "bad behavior")
        delete_user(db, user["id"])
        ban = is_banned(db, "google", "google-123")
        assert ban is not None


# --- Rooms ---


class TestRooms:
    def test_create_room(self, db, stage_room):
        assert stage_room["id"] == "grand-hall"
        assert stage_room["type"] == "stage"

    def test_get_room(self, db, stage_room):
        found = get_room(db, "grand-hall")
        assert found is not None
        assert found["name"] == "Grand Hall"

    def test_seed_event_rooms(self, db, event_id):
        seed_event_rooms(db, event_id, "Test Festival 2026")
        rooms = get_rooms_by_event(db, event_id)
        names = {r["name"] for r in rooms}
        assert "Test Festival 2026" in names
        assert "Rideshare" in names
        assert "Lost & Found" in names

    def test_duplicate_room_ignored(self, db, event_id):
        create_room(db, "test", event_id, "stage", "Test")
        create_room(db, "test", event_id, "stage", "Test Again")
        room = get_room(db, "test")
        assert room["name"] == "Test"


# --- Messages ---


class TestMessages:
    def test_create_and_read(self, db, user, stage_room):
        msg = create_message(db, "grand-hall", user["id"], "text", '{"text":"hello"}')
        assert msg["id"]
        assert msg["type"] == "text"

        messages = get_room_messages(db, "grand-hall")
        assert len(messages) == 1
        assert messages[0]["display_name"] == "TestUser"

    def test_message_ordering(self, db, user, stage_room):
        create_message(db, "grand-hall", user["id"], "text", '{"text":"first"}')
        create_message(db, "grand-hall", user["id"], "text", '{"text":"second"}')
        messages = get_room_messages(db, "grand-hall")
        assert len(messages) == 2
        assert messages[0]["content"] == '{"text":"second"}'

    def test_purge_expired(self, db, user, stage_room):
        msg = create_message(
            db, "grand-hall", user["id"], "text", '{"text":"bye"}', ttl_minutes=0
        )
        result = purge_expired_messages(db)
        assert len(result) == 1
        assert msg["id"] in result[0]["message_ids"]
        assert get_room_messages(db, "grand-hall") == []

    def test_unexpired_not_purged(self, db, user, stage_room):
        create_message(
            db, "grand-hall", user["id"], "text", '{"text":"stay"}', ttl_minutes=60
        )
        result = purge_expired_messages(db)
        assert len(result) == 0
        assert len(get_room_messages(db, "grand-hall")) == 1

    def test_cascade_delete_user(self, db, user, stage_room):
        create_message(db, "grand-hall", user["id"], "text", '{"text":"gone"}')
        delete_user(db, user["id"])
        assert get_room_messages(db, "grand-hall") == []


# --- Meetups ---


class TestMeetups:
    def test_create_meetup(self, db, user, event_id, stage_room):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        meetup = create_meetup(
            db,
            user["id"],
            event_id,
            "grand-hall",
            "Bar hangout",
            future,
            location_label="Main bar",
        )
        assert meetup["id"]
        assert meetup["title"] == "Bar hangout"

        room = get_room(db, meetup["id"])
        assert room is not None
        assert room["type"] == "meetup"

        attendees = get_meetup_attendees(db, meetup["id"])
        assert len(attendees) == 1
        assert attendees[0]["id"] == user["id"]

    def test_join_and_leave(self, db, user, user2, event_id, stage_room):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        meetup = create_meetup(db, user["id"], event_id, "grand-hall", "Test", future)

        join_meetup(db, meetup["id"], user2["id"])
        assert len(get_meetup_attendees(db, meetup["id"])) == 2

        leave_meetup(db, meetup["id"], user2["id"])
        assert len(get_meetup_attendees(db, meetup["id"])) == 1

    def test_active_meetups(self, db, user, event_id, stage_room):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        create_meetup(db, user["id"], event_id, "grand-hall", "Active", future)
        active = get_active_meetups(db, event_id)
        assert len(active) == 1
        assert active[0]["attendee_count"] == 1

    def test_purge_expired_meetups(self, db, user, event_id, stage_room):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        meetup = create_meetup(db, user["id"], event_id, "grand-hall", "Old", past)
        create_message(db, meetup["id"], user["id"], "text", '{"text":"in meetup"}')

        expired = purge_expired_meetups(db)
        expired_ids = [mid for mid, _ in expired]
        assert meetup["id"] in expired_ids
        assert get_room(db, meetup["id"]) is None
        assert get_room_messages(db, meetup["id"]) == []


# --- DMs ---


class TestDMs:
    def test_create_dm(self, db, user, user2, event_id):
        room_id = find_or_create_dm(db, event_id, user["id"], user2["id"])
        assert room_id
        room = get_room(db, room_id)
        assert room["type"] == "dm"

    def test_find_existing_dm(self, db, user, user2, event_id):
        room1 = find_or_create_dm(db, event_id, user["id"], user2["id"])
        room2 = find_or_create_dm(db, event_id, user2["id"], user["id"])
        assert room1 == room2

    def test_dm_nonexistent_user(self, db, user, event_id):
        with pytest.raises(ValueError):
            find_or_create_dm(db, event_id, user["id"], "nonexistent")

    def test_dm_room_not_moderated(self, db, user, user2, event_id):
        room_id = find_or_create_dm(db, event_id, user["id"], user2["id"])
        room = get_room(db, room_id)
        assert room["is_moderated"] == 0

    def test_migration_disables_moderation_on_existing_dm_rooms(
        self, db, user, user2, event_id
    ):
        room_id = "legacy-dm-room"
        create_room(db, room_id, event_id, "dm", "DM", is_moderated=True)
        assert get_room(db, room_id)["is_moderated"] == 1
        _migrate_chat_db(db)
        assert get_room(db, room_id)["is_moderated"] == 0


# --- Blocks ---


class TestBlocks:
    def test_block_and_check(self, db, user, user2):
        assert not is_blocked(db, user["id"], user2["id"])
        block_user(db, user["id"], user2["id"])
        assert is_blocked(db, user["id"], user2["id"])
        assert not is_blocked(db, user2["id"], user["id"])

    def test_unblock(self, db, user, user2):
        block_user(db, user["id"], user2["id"])
        unblock_user(db, user["id"], user2["id"])
        assert not is_blocked(db, user["id"], user2["id"])

    def test_double_block_ignored(self, db, user, user2):
        block_user(db, user["id"], user2["id"])
        block_user(db, user["id"], user2["id"])
        assert is_blocked(db, user["id"], user2["id"])


# --- Reports ---


class TestReports:
    def test_create_and_list(self, db, user, user2, stage_room):
        msg = create_message(
            db, "grand-hall", user2["id"], "text", '{"text":"bad stuff"}'
        )
        report_id = create_report(
            db,
            user["id"],
            user2["id"],
            '{"text":"bad stuff"}',
            "grand-hall",
            "harassment",
        )
        assert report_id
        pending = get_pending_reports(db)
        assert len(pending) == 1
        assert pending[0]["reporter_name"] == "TestUser"
        assert pending[0]["reported_name"] == "OtherUser"

    def test_resolve_report(self, db, user, user2, stage_room):
        create_message(db, "grand-hall", user2["id"], "text", '{"text":"bad"}')
        report_id = create_report(
            db, user["id"], user2["id"], '{"text":"bad"}', "grand-hall", "spam"
        )
        resolve_report(db, report_id, "actioned")
        assert len(get_pending_reports(db)) == 0

    def test_purge_old_resolved(self, db, user, user2, stage_room):
        report_id = create_report(
            db, user["id"], user2["id"], "snapshot", "grand-hall", "test"
        )
        resolve_report(db, report_id, "actioned")
        db.execute(
            "UPDATE reports SET reviewed_at = datetime('now', '-31 days') WHERE id = ?",
            (report_id,),
        )
        db.commit()
        purged = purge_old_reports(db)
        assert purged == 1


# --- Strikes ---


class TestStrikes:
    def test_add_strikes(self, db, user):
        assert get_strike_count(db, user["id"]) == 0
        count = add_strike(db, user["id"], "word_filter", "bad word")
        assert count == 1
        count = add_strike(db, user["id"], "ai_moderation", "toxic")
        assert count == 2

    def test_strikes_survive_user_delete(self, db, user):
        # Strikes are FK-less (like bans/reports) so the moderation history
        # survives account deletion and stays in the admin Logs timeline.
        add_strike(db, user["id"], "word_filter")
        delete_user(db, user["id"])
        assert (
            db.execute(
                "SELECT COUNT(*) FROM strikes WHERE user_id = ?", (user["id"],)
            ).fetchone()[0]
            == 1
        )


# --- Wipe ---


class TestWipe:
    def test_wipe_all(self, db, user, user2, event_id, stage_room):
        create_message(db, "grand-hall", user["id"], "text", '{"text":"hello"}')
        block_user(db, user["id"], user2["id"])
        add_strike(db, user["id"], "test")
        wipe_all_chat_data(db)
        assert get_user(db, user["id"]) is None
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM strikes").fetchone()[0] == 0


# --- Email hash ---


class TestEmailHash:
    def test_hash_deterministic(self):
        assert hash_email("test@example.com") == hash_email("test@example.com")

    def test_hash_case_insensitive(self):
        assert hash_email("Test@Example.COM") == hash_email("test@example.com")

    def test_hash_strips_whitespace(self):
        assert hash_email("  test@example.com  ") == hash_email("test@example.com")


# --- E2EE device keys ---


class TestE2eeDeviceKeys:
    _JWK = '{"kty":"EC","crv":"P-256","x":"AAAA","y":"BBBB"}'
    _JWK2 = '{"kty":"EC","crv":"P-256","x":"CCCC","y":"DDDD"}'

    def test_upsert_and_get(self, db, user):
        upsert_e2ee_device_key(db, user["id"], "a" * 32, self._JWK)
        devices = get_e2ee_device_keys(db, user["id"])
        assert len(devices) == 1
        assert devices[0]["device_id"] == "a" * 32
        assert devices[0]["public_key"] == self._JWK

    def test_get_missing_returns_empty(self, db, user):
        assert get_e2ee_device_keys(db, user["id"]) == []

    def test_upsert_new_device_returns_changed(self, db, user):
        assert upsert_e2ee_device_key(db, user["id"], "a" * 32, self._JWK) is True

    def test_upsert_same_key_returns_unchanged(self, db, user):
        upsert_e2ee_device_key(db, user["id"], "a" * 32, self._JWK)
        assert upsert_e2ee_device_key(db, user["id"], "a" * 32, self._JWK) is False

    def test_upsert_rekey_same_device_returns_changed(self, db, user):
        upsert_e2ee_device_key(db, user["id"], "a" * 32, self._JWK)
        assert upsert_e2ee_device_key(db, user["id"], "a" * 32, self._JWK2) is True

    def test_multiple_devices_per_user(self, db, user):
        upsert_e2ee_device_key(db, user["id"], "a" * 32, self._JWK)
        upsert_e2ee_device_key(db, user["id"], "b" * 32, self._JWK2)
        devices = get_e2ee_device_keys(db, user["id"])
        assert {d["device_id"] for d in devices} == {"a" * 32, "b" * 32}

    def test_cascade_on_user_delete(self, db, user):
        upsert_e2ee_device_key(db, user["id"], "a" * 32, self._JWK)
        delete_user(db, user["id"])
        assert get_e2ee_device_keys(db, user["id"]) == []

    def test_prune_by_age(self, db, user):
        upsert_e2ee_device_key(db, user["id"], "a" * 32, self._JWK)
        stale = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        db.execute(
            "UPDATE e2ee_device_keys SET last_seen = ? WHERE user_id = ? AND device_id = ?",
            (stale, user["id"], "a" * 32),
        )
        db.commit()
        prune_stale_devices(db, user["id"])
        assert get_e2ee_device_keys(db, user["id"]) == []

    def test_prune_by_cap_evicts_oldest_last_seen(self, db, user):
        now = datetime.now(timezone.utc)
        for i in range(8):
            device_id = str(i) * 32
            upsert_e2ee_device_key(db, user["id"], device_id, self._JWK)
            ts = (now - timedelta(minutes=8 - i)).isoformat()
            db.execute(
                "UPDATE e2ee_device_keys SET last_seen = ? WHERE user_id = ? AND device_id = ?",
                (ts, user["id"], device_id),
            )
            db.commit()
        prune_stale_devices(db, user["id"], max_devices=6, max_age_days=7)
        remaining = {d["device_id"] for d in get_e2ee_device_keys(db, user["id"])}
        assert len(remaining) == 6
        assert "0" * 32 not in remaining
        assert "1" * 32 not in remaining
        assert "7" * 32 in remaining

    def test_migration_drops_e2ee_keys_table(self, db):
        db.execute(
            "CREATE TABLE IF NOT EXISTS e2ee_keys "
            "(user_id TEXT PRIMARY KEY, public_key TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        db.commit()
        _migrate_chat_db(db)
        tables = {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "e2ee_keys" not in tables
        assert "e2ee_device_keys" in tables

    def test_reports_unverified_default(self, db, user, user2, stage_room):
        report_id = create_report(
            db, user["id"], user2["id"], "snapshot", "grand-hall", "harassment"
        )
        row = db.execute(
            "SELECT unverified FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
        assert row["unverified"] == 0

    def test_reports_unverified_roundtrip(self, db, user, user2, stage_room):
        report_id = create_report(
            db,
            user["id"],
            user2["id"],
            "reporter-provided snapshot",
            "grand-hall",
            "harassment",
            unverified=1,
        )
        row = db.execute(
            "SELECT unverified FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
        assert row["unverified"] == 1
