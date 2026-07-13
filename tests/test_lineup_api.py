"""Tests for the non-chat lineup HTTP surface of services/companion/api.py.

Covers /ics/{slot_id} format validation + its own rate-limit bucket, and the
push subscribe endpoint's dedicated rate-limit bucket (separate from the
favorites "pick" bucket). TestClient is used WITHOUT the context manager so
the app lifespan (push scheduler etc.) never starts; DB_PATH is monkeypatched
to a scratch sqlite file per test (services/companion/api.py hardcodes DB_PATH
relative to the source file with no environment override, unlike chat_db.CHAT_DB_PATH).
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "companion"))

import api  # noqa: E402

client = TestClient(api.app)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "hearts.db")
    api._init_db()
    api._rate_limits.clear()
    yield
    api._rate_limits.clear()


def _create_session() -> str:
    r = client.post("/api/session")
    assert r.status_code == 201
    return r.json()["session_id"]


class TestIcsValidation:
    def test_invalid_slot_id_format_is_422(self):
        r = client.get("/ics/not-a-uuid")
        assert r.status_code == 422

    def test_valid_format_unknown_slot_is_404(self):
        r = client.get("/ics/3fa85f64-5717-4562-b3fc-2c963f66afa6")
        assert r.status_code == 404

    def test_rate_limited(self):
        codes = [
            client.get(f"/ics/3fa85f64-5717-4562-b3fc-2c963f66af{i:02d}").status_code
            for i in range(31)
        ]
        assert codes[0] == 404
        assert codes[-1] == 429


class TestPushSubscribeRateLimit:
    def test_own_bucket_separate_from_pick(self):
        session_id = _create_session()
        body = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
            "keys": {"p256dh": "p", "auth": "a"},
        }
        codes = [
            client.post(
                f"/api/session/{session_id}/push/subscribe", json=body
            ).status_code
            for _ in range(31)
        ]
        assert codes[0] == 204
        assert codes[-1] == 429

        # Exhausting push_subscribe must not have touched the "pick" bucket.
        pick_r = client.post(
            f"/api/session/{session_id}/pick/3fa85f64-5717-4562-b3fc-2c963f66afa6"
        )
        assert pick_r.status_code == 204


class TestBiosCacheControl:
    def test_bios_json_sets_no_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api, "STATIC_DIR", tmp_path)
        (tmp_path / "bios.json").write_text("{}")
        r = client.get("/bios.json")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"
