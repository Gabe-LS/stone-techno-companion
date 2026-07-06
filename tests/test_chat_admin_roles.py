"""Multi-admin / super-admin surface (Stage B).

Exercises role resolution, super-admin gating, admin-account protection, the
admins-management endpoints, and the audit log through the real ASGI stack.
Reuses the fixtures/harness style of test_chat_api.py.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chat_db import (
    init_chat_db,
    create_user,
    create_session,
    add_admin,
    hash_email,
)

_test_db = None


class _Unclosable(sqlite3.Connection):
    def close(self):  # noqa: D401 - keep the in-memory DB alive across requests
        pass


def _get_test_db():
    return _test_db


@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    global _test_db
    conn = sqlite3.connect(":memory:", check_same_thread=False, factory=_Unclosable)
    conn.row_factory = sqlite3.Row
    init_chat_db(conn)
    _test_db = conn
    monkeypatch.setattr("chat_api._get_db", _get_test_db)
    monkeypatch.setattr("chat_api.DEFAULT_EVENT_ID", "test-event")
    monkeypatch.setattr("chat_api.ADMIN_TOKEN", "test-admin-token")
    # env super-admin: owner@example.com
    monkeypatch.setattr(
        "chat_api._ADMIN_EMAIL_HASHES", {hash_email("owner@example.com")}
    )
    yield
    sqlite3.Connection.close(conn)
    _test_db = None


@pytest.fixture
def client():
    from chat_api import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


TOKEN = {"X-Admin-Token": "test-admin-token"}


def _cookie_client(client, user_id):
    sess = create_session(_test_db, user_id)
    client.cookies.set("chat_session", sess["token"])
    return client


# --- /admin/me ---


def test_me_token_is_super_admin(client):
    r = client.get("/chat/api/admin/me", headers=TOKEN)
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "super_admin" and body["kind"] == "token"


def test_me_env_email_is_permanent_super_admin(client):
    u = create_user(_test_db, "email", hash_email("owner@example.com"), "Owner", None)
    _cookie_client(client, u["id"])
    r = client.get("/chat/api/admin/me")
    assert r.status_code == 200
    assert r.json()["role"] == "super_admin" and r.json()["kind"] == "cookie"


def test_me_db_admin_is_admin_role(client):
    u = create_user(_test_db, "email", hash_email("staff@example.com"), "Staff", None)
    add_admin(_test_db, hash_email("staff@example.com"), "admin", "Staff", "test")
    _cookie_client(client, u["id"])
    r = client.get("/chat/api/admin/me")
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_non_admin_rejected(client):
    u = create_user(_test_db, "email", hash_email("nobody@example.com"), "Nobody", None)
    _cookie_client(client, u["id"])
    assert client.get("/chat/api/admin/me").status_code == 403


# --- super-admin gating ---


def test_regular_admin_cannot_delete_room(client):
    u = create_user(_test_db, "email", hash_email("staff@example.com"), "Staff", None)
    add_admin(_test_db, hash_email("staff@example.com"), "admin", "Staff", "test")
    from chat_db import create_room

    create_room(_test_db, "party", "test-event", "general", "Party")
    _cookie_client(client, u["id"])
    r = client.delete("/chat/api/admin/rooms/party")
    assert r.status_code == 403  # super-admin only


def test_token_super_admin_can_delete_room(client):
    from chat_db import create_room

    create_room(_test_db, "party", "test-event", "general", "Party")
    r = client.delete("/chat/api/admin/rooms/party", headers=TOKEN)
    assert r.status_code == 200


def test_regular_admin_cannot_manage_admins(client):
    u = create_user(_test_db, "email", hash_email("staff@example.com"), "Staff", None)
    add_admin(_test_db, hash_email("staff@example.com"), "admin", "Staff", "test")
    _cookie_client(client, u["id"])
    assert client.get("/chat/api/admin/admins").status_code == 403


# --- admin-account protection ---


def test_admin_cannot_ban_another_admin(client):
    # actor = regular admin (cookie); target = another db admin
    actor = create_user(_test_db, "email", hash_email("a1@example.com"), "A1", None)
    add_admin(_test_db, hash_email("a1@example.com"), "admin", "A1", "test")
    target = create_user(_test_db, "email", hash_email("a2@example.com"), "A2", None)
    add_admin(_test_db, hash_email("a2@example.com"), "admin", "A2", "test")
    _cookie_client(client, actor["id"])
    r = client.post("/chat/api/admin/ban/" + target["id"], json={"reason": "x"})
    assert r.status_code == 403


def test_nobody_can_ban_env_super_admin(client):
    owner = create_user(
        _test_db, "email", hash_email("owner@example.com"), "Owner", None
    )
    # token actor is super_admin, yet env super-admin is untouchable
    r = client.post(
        "/chat/api/admin/ban/" + owner["id"], json={"reason": "x"}, headers=TOKEN
    )
    assert r.status_code == 403


def test_super_admin_can_ban_lower_admin(client):
    target = create_user(_test_db, "email", hash_email("a2@example.com"), "A2", None)
    add_admin(_test_db, hash_email("a2@example.com"), "admin", "A2", "test")
    r = client.post(
        "/chat/api/admin/ban/" + target["id"], json={"reason": "x"}, headers=TOKEN
    )
    assert r.status_code == 200


# --- admins management + audit ---


def test_add_and_list_and_remove_admin(client):
    r = client.post(
        "/chat/api/admin/admins",
        json={"email": "new@example.com", "role": "admin", "label": "New"},
        headers=TOKEN,
    )
    assert r.status_code == 200
    lst = client.get("/chat/api/admin/admins", headers=TOKEN).json()
    hashes = {a["email_hash"] for a in lst}
    assert hash_email("new@example.com") in hashes
    assert hash_email("owner@example.com") in hashes  # env permanent shown
    perm = [a for a in lst if a["permanent"]]
    assert any(a["email_hash"] == hash_email("owner@example.com") for a in perm)
    # remove the DB admin
    r = client.delete(
        "/chat/api/admin/admins/" + hash_email("new@example.com"), headers=TOKEN
    )
    assert r.status_code == 200


def test_cannot_add_env_email_as_db_admin(client):
    r = client.post(
        "/chat/api/admin/admins",
        json={"email": "owner@example.com", "role": "admin"},
        headers=TOKEN,
    )
    assert r.status_code == 409


def test_cannot_remove_permanent_super_admin(client):
    r = client.delete(
        "/chat/api/admin/admins/" + hash_email("owner@example.com"), headers=TOKEN
    )
    assert r.status_code == 400


def test_audit_records_actions(client):
    from chat_db import create_room

    create_room(_test_db, "party", "test-event", "general", "Party")
    client.post("/chat/api/admin/rooms/party/main", headers=TOKEN)
    audit = client.get("/chat/api/admin/audit", headers=TOKEN).json()
    actions = {a["action"] for a in audit}
    assert "set_main" in actions
    assert all(a["actor"] == "token" for a in audit if a["action"] == "set_main")


def test_audit_entries_are_descriptive(client):
    from chat_db import create_room

    create_room(_test_db, "party", "test-event", "general", "Party")
    # a room action: the audit entry should resolve the room's name, not just the id
    client.post("/chat/api/admin/rooms/party/main", headers=TOKEN)
    client.patch(
        "/chat/api/admin/rooms/party",
        json={"is_read_only": True, "description": "x"},
        headers=TOKEN,
    )
    audit = client.get("/chat/api/admin/audit", headers=TOKEN).json()
    set_main = next(a for a in audit if a["action"] == "set_main")
    assert set_main["target_room_name"] == "Party"  # room name resolved, not the slug
    upd = next(a for a in audit if a["action"] == "update_room")
    # detail names which fields changed, so the entry is concrete
    assert "is_read_only" in upd["detail"] and "description" in upd["detail"]


# --- Stage C: message view/delete, settings, meetups, reports filter ---


def test_room_messages_view_includes_pending_and_delete(client):
    from chat_db import create_room, create_message

    create_room(_test_db, "party", "test-event", "general", "Party")
    u = create_user(_test_db, "email", hash_email("m@example.com"), "Poster", None)
    create_message(
        _test_db, "party", u["id"], "text", "hello", moderation_status="approved"
    )
    pend = create_message(
        _test_db, "party", u["id"], "text", "pending one", moderation_status="pending"
    )
    msgs = client.get("/chat/api/admin/rooms/party/messages", headers=TOKEN).json()
    assert len(msgs) == 2  # admin view shows pending too
    assert any(m["moderation_status"] == "pending" for m in msgs)
    r = client.delete("/chat/api/admin/messages/" + pend["id"], headers=TOKEN)
    assert r.status_code == 200
    msgs2 = client.get("/chat/api/admin/rooms/party/messages", headers=TOKEN).json()
    assert all(m["id"] != pend["id"] for m in msgs2)


def test_room_messages_rejects_dm(client):
    from chat_db import create_room

    create_room(_test_db, "dm-x", "test-event", "dm", "DM")
    r = client.get("/chat/api/admin/rooms/dm-x/messages", headers=TOKEN)
    assert r.status_code == 400


def test_settings_get_and_super_only_patch(client):
    s = client.get("/chat/api/admin/settings", headers=TOKEN).json()
    for k in (
        "room_sort",
        "msg_char_limit",
        "dm_ttl_minutes",
        "room_ttl_minutes",
        "meetup_ttl_minutes",
    ):
        assert k in s
    # valid patch
    assert (
        client.patch(
            "/chat/api/admin/settings", json={"msg_char_limit": 500}, headers=TOKEN
        ).status_code
        == 200
    )
    # invalid (out of range / bool)
    assert (
        client.patch(
            "/chat/api/admin/settings", json={"msg_char_limit": 0}, headers=TOKEN
        ).status_code
        == 400
    )
    # regular admin cannot patch
    u = create_user(_test_db, "email", hash_email("staff@example.com"), "Staff", None)
    add_admin(_test_db, hash_email("staff@example.com"), "admin", "Staff", "t")
    _cookie_client(client, u["id"])
    assert (
        client.patch(
            "/chat/api/admin/settings", json={"msg_char_limit": 800}
        ).status_code
        == 403
    )


def test_meetup_list_and_delete(client):
    from chat_db import create_meetup

    u = create_user(_test_db, "email", hash_email("c@example.com"), "Creator", None)
    mt = create_meetup(
        _test_db, u["id"], "test-event", None, "Rave", "2099-01-01T22:00:00+00:00"
    )
    lst = client.get("/chat/api/admin/meetups", headers=TOKEN).json()
    assert any(m["id"] == mt["id"] and m["title"] == "Rave" for m in lst)
    assert (
        client.delete("/chat/api/admin/meetups/" + mt["id"], headers=TOKEN).status_code
        == 200
    )
    assert (
        client.delete("/chat/api/admin/meetups/" + mt["id"], headers=TOKEN).status_code
        == 404
    )


def test_reports_status_filter(client):
    from chat_db import create_report, resolve_report

    rep = create_user(_test_db, "email", hash_email("rep@example.com"), "Rep", None)
    tgt = create_user(_test_db, "email", hash_email("tgt@example.com"), "Tgt", None)
    from chat_db import create_room

    create_room(_test_db, "party", "test-event", "general", "Party")
    rid = create_report(_test_db, rep["id"], tgt["id"], "snap", "party", "spam")
    # pending shows it, with room_name
    pending = client.get("/chat/api/admin/reports?status=pending", headers=TOKEN).json()
    assert any(x["id"] == rid and x.get("room_name") == "Party" for x in pending)
    resolve_report(_test_db, rid, "dismissed")
    assert not client.get(
        "/chat/api/admin/reports?status=pending", headers=TOKEN
    ).json()
    dismissed = client.get(
        "/chat/api/admin/reports?status=dismissed", headers=TOKEN
    ).json()
    assert any(x["id"] == rid for x in dismissed)
    assert any(
        x["id"] == rid
        for x in client.get("/chat/api/admin/reports?status=all", headers=TOKEN).json()
    )


def test_rooms_tab_excludes_dm_rooms(client):
    from chat_db import create_room

    create_room(_test_db, "party", "test-event", "general", "Party")
    create_room(_test_db, "dm-x", "test-event", "dm", "DM")
    create_room(_test_db, "mt-x", "test-event", "meetup", "Meetup")
    rooms = client.get("/chat/api/admin/rooms", headers=TOKEN).json()
    types = {r["type"] for r in rooms}
    assert "dm" not in types  # DMs hidden from the Rooms tab
    assert "general" in types and "meetup" in types  # group + meetup still shown


def test_cannot_set_dm_or_meetup_as_main_room(client):
    from chat_db import create_room, get_room

    create_room(_test_db, "dm-x", "test-event", "dm", "DM")
    create_room(_test_db, "mt-x", "test-event", "meetup", "Meetup")
    create_room(_test_db, "party", "test-event", "general", "Party")
    assert client.post("/chat/api/admin/rooms/dm-x/main", headers=TOKEN).status_code == 400
    assert client.post("/chat/api/admin/rooms/mt-x/main", headers=TOKEN).status_code == 400
    assert not get_room(_test_db, "dm-x")["is_main"]
    # a group room can still be set main
    assert client.post("/chat/api/admin/rooms/party/main", headers=TOKEN).status_code == 200
    assert get_room(_test_db, "party")["is_main"]
