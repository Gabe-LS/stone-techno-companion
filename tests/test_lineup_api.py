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

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "services" / "companion")
)

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


class TestGettingThereStaticRoute:
    """GET /getting-there.json -- served the same way as timetable-transport.json
    (docs/getting-there-design.md section 5): a hand-maintained static file,
    no-cache so an edit ships on the next request without a container rebuild.
    """

    def test_serves_file_with_no_cache_header(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api, "STATIC_DIR", tmp_path)
        (tmp_path / "getting-there.json").write_text(
            '{"event_id": "stone-techno-2026", "methods": []}'
        )
        r = client.get("/getting-there.json")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"
        assert r.json() == {"event_id": "stone-techno-2026", "methods": []}

    def test_missing_file_is_404(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api, "STATIC_DIR", tmp_path)
        r = client.get("/getting-there.json")
        assert r.status_code == 404


class TestLineupDataApi:
    """GET /api/v1/events/{event_id}[/lineup|/timetable|/artists/{artist_id}].

    Static JSON files served straight off disk (docs/api/lineup-data-api.md,
    hybrid strategy). Point LINEUP_API_DIR at a scratch tree per test and
    write only the fixture files each test needs.
    """

    EVENT_JSON = (
        '{"id":"stone-techno-2026","name":"Stone Techno","short_name":"ST26",'
        '"edition":"2026","timezone":"Europe/Berlin","start_date":null,'
        '"end_date":null,"address":null,"website":null,'
        '"source_url":"https://www.stone-techno.com/","latitude":null,"longitude":null}'
    )
    LINEUP_JSON = '{"event_id":"stone-techno-2026","days":[]}'
    TIMETABLE_JSON = '{"event_id":"stone-techno-2026","floors":[],"days":[]}'
    ARTIST_JSON = (
        '{"id":"3fa85f64-5717-4562-b3fc-2c963f66afa6","name":"Test Artist",'
        '"photo":"","bio_html":"","links":[],"sets":[]}'
    )

    def _seed(self, tmp_path, monkeypatch, event_id="stone-techno-2026"):
        monkeypatch.setattr(api, "LINEUP_API_DIR", tmp_path)
        events_dir = tmp_path / "events"
        events_dir.mkdir(parents=True)
        (events_dir / f"{event_id}.json").write_text(self.EVENT_JSON)
        event_subdir = events_dir / event_id
        event_subdir.mkdir()
        (event_subdir / "lineup.json").write_text(self.LINEUP_JSON)
        (event_subdir / "timetable.json").write_text(self.TIMETABLE_JSON)
        artists_dir = tmp_path / "artists"
        artists_dir.mkdir()
        (artists_dir / "3fa85f64-5717-4562-b3fc-2c963f66afa6.json").write_text(
            self.ARTIST_JSON
        )

    def test_event_unknown_is_404(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        r = client.get("/api/v1/events/no-such-event")
        assert r.status_code == 404

    def test_event_found_shape_and_cache_header(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        r = client.get("/api/v1/events/stone-techno-2026")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"
        body = r.json()
        assert body["id"] == "stone-techno-2026"
        assert set(body.keys()) >= {"id", "name", "short_name", "edition", "timezone"}

    def test_lineup_unknown_event_is_404(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        r = client.get("/api/v1/events/no-such-event/lineup")
        assert r.status_code == 404

    def test_lineup_found_shape_and_cache_header(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        r = client.get("/api/v1/events/stone-techno-2026/lineup")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"
        body = r.json()
        assert body["event_id"] == "stone-techno-2026"
        assert "days" in body

    def test_timetable_unknown_event_is_404(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        r = client.get("/api/v1/events/no-such-event/timetable")
        assert r.status_code == 404

    def test_timetable_found_shape_and_cache_header(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        r = client.get("/api/v1/events/stone-techno-2026/timetable")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"
        body = r.json()
        assert body["event_id"] == "stone-techno-2026"
        assert "floors" in body and "days" in body

    def test_timetable_missing_file_is_404(self, tmp_path, monkeypatch):
        # An event with no timed schedule never gets a timetable.json written
        # (generate_lineup_api_json only emits it when has_timetable=True).
        self._seed(tmp_path, monkeypatch)
        (tmp_path / "events" / "stone-techno-2026" / "timetable.json").unlink()
        r = client.get("/api/v1/events/stone-techno-2026/timetable")
        assert r.status_code == 404

    def test_artist_unknown_event_is_404(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        r = client.get(
            "/api/v1/events/no-such-event/artists/3fa85f64-5717-4562-b3fc-2c963f66afa6"
        )
        assert r.status_code == 404

    def test_artist_unknown_artist_is_404(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        r = client.get("/api/v1/events/stone-techno-2026/artists/no-such-artist")
        assert r.status_code == 404

    def test_artist_found_shape_and_cache_header(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        r = client.get(
            "/api/v1/events/stone-techno-2026/artists/3fa85f64-5717-4562-b3fc-2c963f66afa6"
        )
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"
        body = r.json()
        assert body["id"] == "3fa85f64-5717-4562-b3fc-2c963f66afa6"
        assert set(body.keys()) >= {"id", "name", "photo", "bio_html", "links", "sets"}

    def test_event_id_invalid_shape_is_404(self, tmp_path, monkeypatch):
        # Fails EVENT_ID_RE (uppercase not allowed in the slug shape) --
        # rejected before any filesystem lookup happens.
        self._seed(tmp_path, monkeypatch)
        r = client.get("/api/v1/events/Stone_Techno_2026")
        assert r.status_code == 404

    def test_rate_limited(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        codes = [
            client.get("/api/v1/events/stone-techno-2026").status_code
            for _ in range(601)
        ]
        assert codes[0] == 200
        assert codes[-1] == 429
