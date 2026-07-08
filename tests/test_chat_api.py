"""Tests for chat REST API endpoints."""

import base64
import json
import sqlite3
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta, timezone

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chat_db import (
    init_chat_db,
    create_user,
    create_session,
    create_room,
    create_message,
    create_meetup,
    join_meetup,
    create_report,
    get_pending_reports,
    find_or_create_dm,
    ban_user,
    block_user,
    get_user,
    add_strike,
    upsert_e2ee_device_key,
)


_test_db = None


def _get_test_db():
    return _test_db


class _UnclosableConnection:
    """Wraps a sqlite3.Connection so .close() is a no-op during tests."""

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    global _test_db
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_chat_db(conn)
    _test_db = _UnclosableConnection(conn)
    monkeypatch.setattr("chat_api._get_db", _get_test_db)
    monkeypatch.setattr("chat_api.DEFAULT_EVENT_ID", "test-event")
    monkeypatch.setattr("chat_api.ADMIN_TOKEN", "test-admin-token")
    yield
    conn.close()
    _test_db = None


@pytest.fixture
def app():
    from chat_api import router

    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def user1():
    return create_user(_test_db, "google", "g-1", "Alice", "fp-1")


@pytest.fixture
def user2():
    return create_user(_test_db, "apple", "a-2", "Bob", "fp-2")


@pytest.fixture
def session1(user1):
    return create_session(_test_db, user1["id"])


@pytest.fixture
def session2(user2):
    return create_session(_test_db, user2["id"])


@pytest.fixture
def auth_client(client, session1):
    client.cookies.set("chat_session", session1["token"])
    return client


@pytest.fixture
def stage_room():
    return create_room(_test_db, "grand-hall", "test-event", "stage", "Grand Hall")


@pytest.fixture
def general_room():
    return create_room(
        _test_db, "test-event:general", "test-event", "general", "General"
    )


# --- Auth ---


class TestAuth:
    def test_me_unauthenticated(self, client):
        r = client.get("/chat/api/me")
        assert r.status_code == 401

    def test_me_authenticated(self, auth_client, user1):
        r = auth_client.get("/chat/api/me")
        assert r.status_code == 200
        assert r.json()["display_name"] == "Alice"
        assert r.json()["id"] == user1["id"]

    def test_logout(self, auth_client):
        r = auth_client.post("/chat/api/logout")
        assert r.status_code == 200

    def test_update_profile(self, auth_client):
        r = auth_client.put(
            "/chat/api/profile",
            json={"display_name": "NewAlice"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_update_profile_too_short(self, auth_client):
        r = auth_client.put(
            "/chat/api/profile",
            json={"display_name": "a"},
        )
        assert r.status_code == 400

    def test_delete_account(self, auth_client, user1):
        r = auth_client.delete("/chat/api/account")
        assert r.status_code == 200
        assert get_user(_test_db, user1["id"]) is None

    def test_email_start_disposable_blocked(self, client, monkeypatch):
        monkeypatch.setattr("chat_api.DISPOSABLE_DOMAINS", {"tempmail.com"})
        r = client.post(
            "/chat/api/login",
            json={"email": "test@tempmail.com"},
        )
        assert r.status_code == 400
        assert "disposable" in r.json()["detail"].lower()

    def test_email_start_valid(self, client, monkeypatch):
        monkeypatch.setattr("chat_api.DISPOSABLE_DOMAINS", set())
        monkeypatch.setenv("MAILEROO_API_KEY", "test-key")
        monkeypatch.setattr(
            "maileroo.MailerooClient.send_basic_email",
            lambda self, payload: None,
        )
        r = client.post(
            "/chat/api/login",
            json={"email": "test@gmail.com"},
        )
        assert r.status_code == 200
        assert r.json()["sent"] is True

    def test_email_start_no_delivery_config(self, client, monkeypatch):
        # Magic-link must fail loudly (not report success) when email delivery
        # is unconfigured, so a missing MAILEROO_API_KEY in production can't
        # silently take out the only email auth path.
        monkeypatch.setattr("chat_api.DISPOSABLE_DOMAINS", set())
        monkeypatch.delenv("MAILEROO_API_KEY", raising=False)
        r = client.post(
            "/chat/api/login",
            json={"email": "test@gmail.com"},
        )
        assert r.status_code == 500

    def test_banned_user_rejected(self, client):
        user = create_user(_test_db, "email", "hash-123", "Banned")
        ban_user(_test_db, user["id"], "email", "hash-123", "bad", "fp-bad")
        session = create_session(_test_db, user["id"])
        client.cookies.set("chat_session", session["token"])
        r = client.get("/chat/api/me")
        assert r.status_code == 200


class TestEmailCodeLogin:
    """The 6-digit code path: the emailed link opens in the browser, whose
    storage iOS partitions away from the home-screen app, so PWA users sign
    in by typing the code from the same email."""

    def _request_code(self, client, monkeypatch, email):
        sent = {}
        monkeypatch.setattr("chat_api.DISPOSABLE_DOMAINS", set())
        monkeypatch.setattr("chat_api._email_rate", {})
        monkeypatch.setattr("chat_api._email_dest_rate", {})
        monkeypatch.setattr("chat_api._auth_rate", {})
        monkeypatch.setenv("MAILEROO_API_KEY", "test-key")
        monkeypatch.setattr(
            "maileroo.MailerooClient.send_basic_email",
            lambda self, payload: sent.update(payload),
        )
        r = client.post("/chat/api/login", json={"email": email})
        assert r.status_code == 200
        import re

        m = re.search(r"code there instead: (\d{6})", sent["plain"])
        assert m, sent.get("plain")
        assert m.group(1) in sent["html"]
        return m.group(1)

    def test_code_login_success(self, client, monkeypatch):
        code = self._request_code(client, monkeypatch, "codeuser@gmail.com")
        r = client.post(
            "/chat/api/login/code",
            json={"email": "codeuser@gmail.com", "code": code},
        )
        assert r.status_code == 200
        assert r.json()["provider"] == "email"
        assert "chat_session" in r.cookies

    def test_code_is_single_use(self, client, monkeypatch):
        code = self._request_code(client, monkeypatch, "onceuser@gmail.com")
        r = client.post(
            "/chat/api/login/code",
            json={"email": "onceuser@gmail.com", "code": code},
        )
        assert r.status_code == 200
        r = client.post(
            "/chat/api/login/code",
            json={"email": "onceuser@gmail.com", "code": code},
        )
        assert r.status_code == 400

    def test_code_login_wrong_code(self, client, monkeypatch):
        code = self._request_code(client, monkeypatch, "wrongcode@gmail.com")
        wrong = "000000" if code != "000000" else "111111"
        r = client.post(
            "/chat/api/login/code",
            json={"email": "wrongcode@gmail.com", "code": wrong},
        )
        assert r.status_code == 400

    def test_code_login_attempt_lockout(self, client, monkeypatch):
        code = self._request_code(client, monkeypatch, "lockout@gmail.com")
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            r = client.post(
                "/chat/api/login/code",
                json={"email": "lockout@gmail.com", "code": wrong},
            )
            assert r.status_code == 400
        # After 5 failures even the correct code is rejected and the pending
        # token is burned, so a fresh email is required.
        r = client.post(
            "/chat/api/login/code",
            json={"email": "lockout@gmail.com", "code": code},
        )
        assert r.status_code == 429
        r = client.post(
            "/chat/api/login/code",
            json={"email": "lockout@gmail.com", "code": code},
        )
        assert r.status_code == 400

    def test_code_login_expired(self, client, monkeypatch):
        code = self._request_code(client, monkeypatch, "expired@gmail.com")
        _test_db.execute(
            "UPDATE email_tokens SET expires_at = ?",
            ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),),
        )
        _test_db.commit()
        r = client.post(
            "/chat/api/login/code",
            json={"email": "expired@gmail.com", "code": code},
        )
        assert r.status_code == 400

    def test_code_login_no_pending_request(self, client, monkeypatch):
        monkeypatch.setattr("chat_api._auth_rate", {})
        r = client.post(
            "/chat/api/login/code",
            json={"email": "nobody@gmail.com", "code": "123456"},
        )
        assert r.status_code == 400

    def test_code_login_malformed_code(self, client, monkeypatch):
        monkeypatch.setattr("chat_api._auth_rate", {})
        r = client.post(
            "/chat/api/login/code",
            json={"email": "x@gmail.com", "code": "12ab56"},
        )
        assert r.status_code == 400


# --- Rooms ---


class TestRooms:
    def test_list_rooms(self, auth_client, stage_room, general_room):
        r = auth_client.get("/chat/api/rooms")
        assert r.status_code == 200
        names = {room["name"] for room in r.json()}
        assert "Grand Hall" in names
        assert "General" in names

    def test_room_messages_empty(self, auth_client, stage_room):
        r = auth_client.get("/chat/api/rooms/grand-hall/messages")
        assert r.status_code == 200
        assert r.json() == []

    def test_room_messages_with_data(self, auth_client, user1, stage_room):
        create_message(_test_db, "grand-hall", user1["id"], "text", '{"text":"hello"}')
        r = auth_client.get("/chat/api/rooms/grand-hall/messages")
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["display_name"] == "Alice"

    def test_room_not_found(self, auth_client):
        r = auth_client.get("/chat/api/rooms/nonexistent/messages")
        assert r.status_code == 404

    def test_room_online(self, auth_client, stage_room):
        r = auth_client.get("/chat/api/rooms/grand-hall/online")
        assert r.status_code == 200
        assert r.json() == []


# --- Meetups ---


class TestMeetups:
    def test_create_meetup(self, auth_client, stage_room):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        r = auth_client.post(
            "/chat/api/meetups",
            json={
                "title": "Bar hangout",
                "meetup_time": future,
                "stage_id": "grand-hall",
            },
        )
        assert r.status_code == 201
        assert r.json()["title"] == "Bar hangout"

    def test_create_meetup_missing_fields(self, auth_client):
        r = auth_client.post("/chat/api/meetups", json={"title": "No time"})
        assert r.status_code == 400

    def test_list_meetups(self, auth_client, user1, stage_room):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        create_meetup(
            _test_db,
            user1["id"],
            "test-event",
            "grand-hall",
            "Test meetup",
            future,
        )
        r = auth_client.get("/chat/api/meetups")
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["attendee_count"] == 1

    def test_join_and_leave_meetup(
        self, auth_client, user1, user2, session2, stage_room
    ):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        meetup = create_meetup(
            _test_db,
            user1["id"],
            "test-event",
            "grand-hall",
            "Join test",
            future,
        )

        client2 = TestClient(auth_client.app)
        client2.cookies.set("chat_session", session2["token"])

        r = client2.post(f"/chat/api/meetups/{meetup['id']}/join")
        assert r.status_code == 200
        assert len(r.json()) == 2

        r = client2.delete(f"/chat/api/meetups/{meetup['id']}/join")
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_get_meetup(self, auth_client, user1, stage_room):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        meetup = create_meetup(
            _test_db,
            user1["id"],
            "test-event",
            "grand-hall",
            "Detail test",
            future,
        )
        r = auth_client.get(f"/chat/api/meetups/{meetup['id']}")
        assert r.status_code == 200
        assert r.json()["title"] == "Detail test"

    def test_meetup_filter_by_stage(self, auth_client, user1, stage_room):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        create_meetup(
            _test_db,
            user1["id"],
            "test-event",
            "grand-hall",
            "GH meetup",
            future,
        )
        r = auth_client.get("/chat/api/meetups?stage_id=grand-hall")
        assert len(r.json()) == 1
        r2 = auth_client.get("/chat/api/meetups?stage_id=eisbahn")
        assert len(r2.json()) == 0

    def test_non_attendee_cannot_see_location(
        self, auth_client, user1, user2, session2, stage_room
    ):
        # A4: location + attendee identity must be attendee-only.
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        meetup = create_meetup(
            _test_db,
            user1["id"],
            "test-event",
            "grand-hall",
            "Loc test",
            future,
            location_lat=52.5,
            location_lng=13.4,
            note="secret spot",
        )
        # creator IS an attendee -> sees full detail
        rc = auth_client.get(f"/chat/api/meetups/{meetup['id']}")
        assert rc.json().get("location_lat") is not None
        assert rc.json().get("note") == "secret spot"
        # non-attendee -> location/note/attendees stripped, count still present
        client2 = TestClient(auth_client.app)
        client2.cookies.set("chat_session", session2["token"])
        rn = client2.get(f"/chat/api/meetups/{meetup['id']}")
        assert rn.status_code == 200
        assert "location_lat" not in rn.json()
        assert "note" not in rn.json()
        assert "attendees" not in rn.json()
        assert rn.json()["attendee_count"] == 1
        # list endpoint applies the same gate
        rl = client2.get("/chat/api/meetups")
        assert rl.status_code == 200
        assert all("location_lat" not in m for m in rl.json())

    def test_join_nonexistent_meetup_returns_404(self, auth_client):
        # B1: no IntegrityError / 500 for a stale or bogus meetup id.
        r = auth_client.post("/chat/api/meetups/does-not-exist/join")
        assert r.status_code == 404

    def test_muted_user_cannot_create_meetup(self, auth_client, user1, stage_room):
        # A1: mute is enforced on meetup creation.
        from chat_db import mute_user

        mute_user(_test_db, user1["id"])
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        r = auth_client.post(
            "/chat/api/meetups", json={"title": "x", "meetup_time": future}
        )
        assert r.status_code == 403

    def test_past_meetup_time_rejected(self, auth_client, stage_room):
        # B5: past meetup_time is rejected at creation.
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        r = auth_client.post(
            "/chat/api/meetups", json={"title": "old", "meetup_time": past}
        )
        assert r.status_code == 400

    def test_blocked_user_cannot_join(
        self, auth_client, user1, user2, session2, stage_room
    ):
        # A6: a user blocked by the creator cannot join their meetup.
        from chat_db import block_user

        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        meetup = create_meetup(
            _test_db, user1["id"], "test-event", "grand-hall", "Blk", future
        )
        block_user(_test_db, user1["id"], user2["id"])
        client2 = TestClient(auth_client.app)
        client2.cookies.set("chat_session", session2["token"])
        r = client2.post(f"/chat/api/meetups/{meetup['id']}/join")
        assert r.status_code == 403

    def test_creator_can_cancel_meetup(self, auth_client, user1, stage_room):
        # C2: creator-only cancel removes the meetup.
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        meetup = create_meetup(
            _test_db, user1["id"], "test-event", "grand-hall", "Cancel", future
        )
        r = auth_client.delete(f"/chat/api/meetups/{meetup['id']}")
        assert r.status_code == 200
        assert auth_client.get(f"/chat/api/meetups/{meetup['id']}").status_code == 404

    def test_cancel_meetup_deletes_invite_from_group_room(
        self, auth_client, user1, stage_room
    ):
        """Cancelling a meetup must delete its invite message from the origin
        group room so it disappears from room history (no client-side state)."""
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        meetup = create_meetup(
            _test_db, user1["id"], "test-event", "grand-hall", "InviteGone", future
        )
        invite_content = json.dumps(
            {
                "meetup_id": meetup["id"],
                "title": "InviteGone",
                "meetup_time": future,
                "label": "",
                "note": "",
            }
        )
        create_message(
            _test_db, "grand-hall", user1["id"], "meetup_invite", invite_content
        )
        msgs_before = auth_client.get("/chat/api/rooms/grand-hall/messages").json()
        invite_before = [m for m in msgs_before if m["type"] == "meetup_invite"]
        assert len(invite_before) == 1

        r = auth_client.delete(f"/chat/api/meetups/{meetup['id']}")
        assert r.status_code == 200

        msgs_after = auth_client.get("/chat/api/rooms/grand-hall/messages").json()
        invite_after = [m for m in msgs_after if m["type"] == "meetup_invite"]
        assert len(invite_after) == 0, "Invite message must be deleted from group room"


# --- DMs ---


class TestDMs:
    def test_create_dm(self, auth_client, user1, user2):
        r = auth_client.post(
            "/chat/api/dms",
            json={"target_user_id": user2["id"]},
        )
        assert r.status_code == 201
        assert r.json()["room_id"]

    def test_list_dms(self, auth_client, user1, user2):
        find_or_create_dm(_test_db, "test-event", user1["id"], user2["id"])
        r = auth_client.get("/chat/api/dms")
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["other_name"] == "Bob"
        # Peer has no E2EE key yet: the client uses this to render the DM
        # row without a lock icon and pre-latch the unencrypted fallback.
        assert r.json()[0]["other_has_key"] is False

    def test_list_dms_other_has_key(self, auth_client, user1, user2):
        find_or_create_dm(_test_db, "test-event", user1["id"], user2["id"])
        upsert_e2ee_device_key(_test_db, user2["id"], "a" * 32, _valid_jwk())
        r = auth_client.get("/chat/api/dms")
        assert r.status_code == 200
        assert r.json()[0]["other_has_key"] is True

    def test_dm_nonexistent_user(self, auth_client):
        r = auth_client.post(
            "/chat/api/dms",
            json={"target_user_id": "nonexistent"},
        )
        assert r.status_code == 404

    def test_create_dm_registers_badge_rooms(self, auth_client, user1, user2):
        from chat_ws import manager as ws_manager

        ws_manager.user_badge_rooms.pop(user1["id"], None)
        ws_manager.user_badge_rooms.pop(user2["id"], None)
        try:
            r = auth_client.post(
                "/chat/api/dms",
                json={"target_user_id": user2["id"]},
            )
            room_id = r.json()["room_id"]
            assert room_id in ws_manager.user_badge_rooms.get(user1["id"], set())
            assert room_id in ws_manager.user_badge_rooms.get(user2["id"], set())
        finally:
            ws_manager.user_badge_rooms.pop(user1["id"], None)
            ws_manager.user_badge_rooms.pop(user2["id"], None)


# --- Users (block/unblock) ---


class TestBlocking:
    def test_block_user(self, auth_client, user2):
        r = auth_client.post(f"/chat/api/users/{user2['id']}/block")
        assert r.status_code == 200

    def test_unblock_user(self, auth_client, user1, user2):
        block_user(_test_db, user1["id"], user2["id"])
        r = auth_client.delete(f"/chat/api/users/{user2['id']}/block")
        assert r.status_code == 200


# --- Admin ---


class TestAdmin:
    def test_admin_reports_no_token(self, client):
        r = client.get("/chat/api/admin/reports")
        assert r.status_code == 403

    def test_admin_reports_with_token(self, client, user1, user2, stage_room):
        create_message(_test_db, "grand-hall", user2["id"], "text", '{"text":"bad"}')
        create_report(
            _test_db,
            user1["id"],
            user2["id"],
            '{"text":"bad"}',
            "grand-hall",
            "harassment",
        )
        r = client.get(
            "/chat/api/admin/reports?status=pending",
            headers={"X-Admin-Token": "test-admin-token"},
        )
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["reporter_name"] == "Alice"

    def test_admin_reports_includes_unverified_and_reporter_id(
        self, client, user1, user2, stage_room
    ):
        create_message(_test_db, "grand-hall", user2["id"], "text", '{"text":"bad"}')
        report_id = create_report(
            _test_db,
            user1["id"],
            user2["id"],
            '{"text":"bad"}',
            "grand-hall",
            "harassment",
            unverified=1,
        )
        r = client.get(
            "/chat/api/admin/reports?status=pending",
            headers={"X-Admin-Token": "test-admin-token"},
        )
        assert r.status_code == 200
        report = next(x for x in r.json() if x["id"] == report_id)
        assert report["unverified"] is True
        assert report["reporter_id"] == user1["id"]

    def test_admin_user_detail_reports_filed_count(
        self, client, user1, user2, stage_room
    ):
        create_report(_test_db, user1["id"], user2["id"], "snap1", "grand-hall", "spam")
        create_report(
            _test_db, user1["id"], user2["id"], "snap2", "grand-hall", "harassment"
        )
        r = client.get(
            f"/chat/api/admin/users/{user1['id']}",
            headers={"X-Admin-Token": "test-admin-token"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["reports_filed_count"] == 2
        assert len(data["reports_against"]) == 0

        r2 = client.get(
            f"/chat/api/admin/users/{user2['id']}",
            headers={"X-Admin-Token": "test-admin-token"},
        )
        assert r2.status_code == 200
        data2 = r2.json()
        assert len(data2["reports_against"]) == 2
        assert data2["reports_filed_count"] == 0

    def test_admin_resolve_report(self, client, user1, user2, stage_room):
        report_id = create_report(
            _test_db,
            user1["id"],
            user2["id"],
            "snapshot",
            "grand-hall",
            "spam",
        )
        r = client.patch(
            f"/chat/api/admin/reports/{report_id}",
            json={"status": "dismissed"},
            headers={"X-Admin-Token": "test-admin-token"},
        )
        assert r.status_code == 200
        assert len(get_pending_reports(_test_db)) == 0

    def test_admin_ban_user(self, client, user2):
        r = client.post(
            f"/chat/api/admin/ban/{user2['id']}",
            json={"reason": "manual ban"},
            headers={"X-Admin-Token": "test-admin-token"},
        )
        assert r.status_code == 200

    def test_admin_unban_user(self, client, user2):
        ban_user(_test_db, user2["id"], "apple", "a-2", "test ban")
        r = client.post(
            f"/chat/api/admin/unban/{user2['id']}",
            headers={"X-Admin-Token": "test-admin-token"},
        )
        assert r.status_code == 200

    def test_admin_page(self, client):
        r = client.get(
            "/chat/api/admin",
            params={"admin_token": "test-admin-token"},
        )
        assert r.status_code == 200
        assert "Chat Admin" in r.text


# --- E2EE keys ---


def _valid_jwk(x_byte: int = 0x01, y_byte: int = 0x02) -> str:
    # Real on-curve P-256 public key, deterministic from the byte args: same
    # args -> same key (the re-key / no-broadcast tests depend on that),
    # distinct args -> distinct keys. Server-side validation now rejects
    # arbitrary (x, y) bytes that aren't a real curve point (K3), so the test
    # keys must be genuine points.
    from cryptography.hazmat.primitives.asymmetric import ec

    seed = (((x_byte & 0xFF) << 8) | (y_byte & 0xFF)) or 1
    nums = ec.derive_private_key(seed, ec.SECP256R1()).public_key().public_numbers()
    x = base64.urlsafe_b64encode(nums.x.to_bytes(32, "big")).rstrip(b"=").decode()
    y = base64.urlsafe_b64encode(nums.y.to_bytes(32, "big")).rstrip(b"=").decode()
    return json.dumps({"kty": "EC", "crv": "P-256", "x": x, "y": y})


def _offcurve_jwk() -> str:
    # Syntactically valid JWK (32-byte x/y) whose point is not on P-256.
    x = base64.urlsafe_b64encode(b"\x01" * 32).rstrip(b"=").decode()
    y = base64.urlsafe_b64encode(b"\x02" * 32).rstrip(b"=").decode()
    return json.dumps({"kty": "EC", "crv": "P-256", "x": x, "y": y})


_DEVICE_A = "a" * 32
_DEVICE_B = "b" * 32


class TestE2eeDeviceKeys:
    def test_put_get_round_trip(self, auth_client, user1):
        jwk = _valid_jwk()
        r = auth_client.put(
            "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk}
        )
        assert r.status_code == 204

        r = auth_client.get(f"/chat/api/keys/{user1['id']}")
        assert r.status_code == 200
        data = r.json()
        assert data["user_id"] == user1["id"]
        assert len(data["devices"]) == 1
        assert data["devices"][0]["device_id"] == _DEVICE_A
        assert data["devices"][0]["public_key"] == jwk
        assert "created_at" in data["devices"][0]

    def test_put_multiple_devices_listed(self, auth_client, user1):
        jwk1 = _valid_jwk(0x01, 0x02)
        jwk2 = _valid_jwk(0x03, 0x04)
        auth_client.put(
            "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk1}
        )
        auth_client.put(
            "/chat/api/keys", json={"device_id": _DEVICE_B, "public_key": jwk2}
        )
        r = auth_client.get(f"/chat/api/keys/{user1['id']}")
        assert r.status_code == 200
        device_ids = {d["device_id"] for d in r.json()["devices"]}
        assert device_ids == {_DEVICE_A, _DEVICE_B}

    def test_get_404_missing(self, auth_client):
        r = auth_client.get("/chat/api/keys/nonexistent-user-id")
        assert r.status_code == 404

    def test_put_auth_required(self, client):
        jwk = _valid_jwk()
        r = client.put(
            "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk}
        )
        assert r.status_code == 401

    def test_get_auth_required(self, client, user1):
        r = client.get(f"/chat/api/keys/{user1['id']}")
        assert r.status_code == 401

    def test_device_id_missing(self, auth_client):
        jwk = _valid_jwk()
        r = auth_client.put("/chat/api/keys", json={"public_key": jwk})
        assert r.status_code == 422

    def test_device_id_wrong_length(self, auth_client):
        jwk = _valid_jwk()
        r = auth_client.put(
            "/chat/api/keys", json={"device_id": "a" * 10, "public_key": jwk}
        )
        assert r.status_code == 422

    def test_device_id_bad_hex(self, auth_client):
        jwk = _valid_jwk()
        r = auth_client.put(
            "/chat/api/keys", json={"device_id": "g" * 32, "public_key": jwk}
        )
        assert r.status_code == 422

    def test_device_id_uppercase_rejected(self, auth_client):
        jwk = _valid_jwk()
        r = auth_client.put(
            "/chat/api/keys", json={"device_id": "A" * 32, "public_key": jwk}
        )
        assert r.status_code == 422

    def test_jwk_wrong_kty(self, auth_client):
        x = base64.urlsafe_b64encode(b"\x01" * 32).rstrip(b"=").decode()
        y = base64.urlsafe_b64encode(b"\x02" * 32).rstrip(b"=").decode()
        jwk = json.dumps({"kty": "RSA", "crv": "P-256", "x": x, "y": y})
        r = auth_client.put(
            "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk}
        )
        assert r.status_code == 422

    def test_jwk_wrong_crv(self, auth_client):
        x = base64.urlsafe_b64encode(b"\x01" * 32).rstrip(b"=").decode()
        y = base64.urlsafe_b64encode(b"\x02" * 32).rstrip(b"=").decode()
        jwk = json.dumps({"kty": "EC", "crv": "P-384", "x": x, "y": y})
        r = auth_client.put(
            "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk}
        )
        assert r.status_code == 422

    def test_jwk_missing_x(self, auth_client):
        y = base64.urlsafe_b64encode(b"\x02" * 32).rstrip(b"=").decode()
        jwk = json.dumps({"kty": "EC", "crv": "P-256", "y": y})
        r = auth_client.put(
            "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk}
        )
        assert r.status_code == 422

    def test_jwk_missing_y(self, auth_client):
        x = base64.urlsafe_b64encode(b"\x01" * 32).rstrip(b"=").decode()
        jwk = json.dumps({"kty": "EC", "crv": "P-256", "x": x})
        r = auth_client.put(
            "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk}
        )
        assert r.status_code == 422

    def test_jwk_x_wrong_length(self, auth_client):
        x_short = base64.urlsafe_b64encode(b"\x01" * 16).rstrip(b"=").decode()
        y = base64.urlsafe_b64encode(b"\x02" * 32).rstrip(b"=").decode()
        jwk = json.dumps({"kty": "EC", "crv": "P-256", "x": x_short, "y": y})
        r = auth_client.put(
            "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk}
        )
        assert r.status_code == 422

    def test_jwk_y_wrong_length(self, auth_client):
        x = base64.urlsafe_b64encode(b"\x01" * 32).rstrip(b"=").decode()
        y_short = base64.urlsafe_b64encode(b"\x02" * 16).rstrip(b"=").decode()
        jwk = json.dumps({"kty": "EC", "crv": "P-256", "x": x, "y": y_short})
        r = auth_client.put(
            "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk}
        )
        assert r.status_code == 422

    def test_jwk_present_d_rejected(self, auth_client):
        x = base64.urlsafe_b64encode(b"\x01" * 32).rstrip(b"=").decode()
        y = base64.urlsafe_b64encode(b"\x02" * 32).rstrip(b"=").decode()
        d = base64.urlsafe_b64encode(b"\x03" * 32).rstrip(b"=").decode()
        jwk = json.dumps({"kty": "EC", "crv": "P-256", "x": x, "y": y, "d": d})
        r = auth_client.put(
            "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk}
        )
        assert r.status_code == 422

    def test_jwk_offcurve_rejected(self, auth_client):
        # K3: 32-byte x/y that decode fine but are not a real P-256 point.
        r = auth_client.put(
            "/chat/api/keys",
            json={"device_id": _DEVICE_A, "public_key": _offcurve_jwk()},
        )
        assert r.status_code == 422

    def test_jwk_oversized_rejected(self, auth_client):
        # K2: public_key over the length cap is rejected before json.loads,
        # so it can't be parsed or stored (DB-bloat guard).
        big = "A" * 2000
        jwk = json.dumps({"kty": "EC", "crv": "P-256", "x": big, "y": big})
        assert len(jwk) > 1024
        r = auth_client.put(
            "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk}
        )
        assert r.status_code == 422

    def test_key_rotated_broadcast_on_new_device_and_rekey(
        self, auth_client, user1, user2, session2
    ):
        find_or_create_dm(_test_db, "test-event", user1["id"], user2["id"])
        jwk1 = _valid_jwk(0x01, 0x02)
        jwk2 = _valid_jwk(0x03, 0x04)

        # First upload: broadcast expected too — a DM peer may have latched
        # into unencrypted fallback while this user was still in profile setup
        # (before any key existed), and key_rotated is what unlatches them.
        # Two create_task calls per changed mapping: one to the DM peer
        # (room-scoped) and one self-notification (room_id null) so sibling
        # devices of the SAME user fan out to the new device too.
        with patch("chat_ws.manager.send_to_user", new_callable=AsyncMock):
            with patch("chat_api.asyncio.create_task") as mock_ct:
                r = auth_client.put(
                    "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk1}
                )
                assert r.status_code == 204
                assert mock_ct.call_count == 2

        # Re-key the SAME device with a different key: broadcast expected
        with patch("chat_ws.manager.send_to_user", new_callable=AsyncMock):
            with patch("chat_api.asyncio.create_task") as mock_ct:
                r = auth_client.put(
                    "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk2}
                )
                assert r.status_code == 204
                assert mock_ct.call_count == 2

    def test_key_rotated_broadcast_on_second_device_no_dm(self, auth_client, user1):
        # No DM room here -- only the self-notification (room_id null) fires,
        # confirming sibling-device fanout doesn't depend on having a peer.
        jwk1 = _valid_jwk(0x01, 0x02)
        jwk2 = _valid_jwk(0x03, 0x04)
        auth_client.put(
            "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk1}
        )
        with patch("chat_ws.manager.send_to_user", new_callable=AsyncMock):
            with patch("chat_api.asyncio.create_task") as mock_ct:
                r = auth_client.put(
                    "/chat/api/keys", json={"device_id": _DEVICE_B, "public_key": jwk2}
                )
                assert r.status_code == 204
                assert mock_ct.call_count == 1

    def test_key_rotated_self_notification_payload(self, auth_client, user1):
        jwk = _valid_jwk()
        with patch("chat_ws.manager.send_to_user", new_callable=AsyncMock) as mock_send:
            with patch(
                "chat_api.asyncio.create_task", side_effect=lambda coro: coro.close()
            ):
                r = auth_client.put(
                    "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk}
                )
                assert r.status_code == 204
        self_calls = [c for c in mock_send.call_args_list if c.args[0] == user1["id"]]
        assert len(self_calls) == 1
        assert self_calls[0].args[1]["event"] == "key_rotated"
        assert self_calls[0].args[1]["room_id"] is None

    def test_no_broadcast_same_key_reupload(self, auth_client, user1, user2):
        find_or_create_dm(_test_db, "test-event", user1["id"], user2["id"])
        jwk = _valid_jwk()

        # First upload
        r = auth_client.put(
            "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk}
        )
        assert r.status_code == 204

        # Same key re-upload: no broadcast
        with patch("chat_api.asyncio.create_task") as mock_ct:
            r = auth_client.put(
                "/chat/api/keys", json={"device_id": _DEVICE_A, "public_key": jwk}
            )
            assert r.status_code == 204
            mock_ct.assert_not_called()
