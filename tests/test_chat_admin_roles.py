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
