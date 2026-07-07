"""Tests for the public transport proxy endpoints (/api/transport/*).

The EFA departure proxy and the OSRM walk proxy are tested with a mocked
httpx layer: no network. TestClient is used WITHOUT the context manager so
the app lifespan (push scheduler etc.) never starts.
"""

import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import api  # noqa: E402

client = TestClient(api.app)


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient; serves canned payloads and counts calls."""

    payload = None
    status = 200
    calls = 0

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **kw):
        _FakeAsyncClient.calls += 1
        return _FakeResponse(_FakeAsyncClient.payload, _FakeAsyncClient.status)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.payload = None
    _FakeAsyncClient.status = 200
    _FakeAsyncClient.calls = 0
    api._transport_cache.clear()
    api._transport_rate.clear()
    yield
    api._transport_cache.clear()
    api._transport_rate.clear()


def _efa_departure(line="107", direction="Essen Hauptbahnhof", **extra):
    dep = {
        "servingLine": {
            "number": line,
            "direction": direction,
            "realtime": extra.pop("realtime", "1"),
        },
        "dateTime": {
            "hour": "14",
            "minute": "5",
            "day": "10",
            "month": "7",
            "year": "2026",
        },
        "platform": "1",
    }
    if "delay" in extra:
        dep["servingLine"]["delay"] = extra.pop("delay")
    dep.update(extra)
    return dep


class TestDepartures:
    def test_requires_valid_params(self):
        assert client.get("/api/transport/departures").status_code == 422
        r = client.get("/api/transport/departures?date=bogus&time=14:00")
        assert r.status_code == 400
        r = client.get("/api/transport/departures?date=10.07.2026&time=xx")
        assert r.status_code == 400

    def test_filters_lines_and_directions(self):
        _FakeAsyncClient.payload = {
            "departureList": [
                _efa_departure("107", "Essen Hauptbahnhof"),
                _efa_departure("107", "Gelsenkirchen Hbf"),  # wrong direction
                _efa_departure("U11", "Essen Hauptbahnhof"),  # wrong line
                _efa_departure(
                    "NE2",
                    "Essen Bredeney",
                    delay="3",
                    realDateTime={"hour": "14", "minute": "8"},
                    realtimeTripStatus="MONITORED",
                    countdown="12",
                ),
            ]
        }
        r = client.get("/api/transport/departures?date=10.07.2026&time=14:00")
        assert r.status_code == 200
        deps = r.json()["departures"]
        assert [d["line"] for d in deps] == ["107", "NE2"]
        ne2 = deps[1]
        assert ne2["scheduled"] == "14:05"
        assert ne2["scheduledDate"] == "10.07.2026"
        assert ne2["real"] == "14:08"
        assert ne2["delay"] == 3
        assert ne2["status"] == "MONITORED"
        assert ne2["countdown"] == 12

    def test_cache_collapses_upstream_calls(self):
        _FakeAsyncClient.payload = {"departureList": [_efa_departure()]}
        for _ in range(5):
            r = client.get("/api/transport/departures?date=10.07.2026&time=14:00")
            assert r.status_code == 200
        assert _FakeAsyncClient.calls == 1
        # A different minute bucket is a fresh upstream call
        client.get("/api/transport/departures?date=10.07.2026&time=14:01")
        assert _FakeAsyncClient.calls == 2

    def test_upstream_error_without_cache_is_502(self):
        _FakeAsyncClient.status = 500
        r = client.get("/api/transport/departures?date=10.07.2026&time=15:00")
        assert r.status_code == 502

    def test_rate_limited(self):
        _FakeAsyncClient.payload = {"departureList": []}
        codes = [
            client.get(
                f"/api/transport/departures?date=10.07.2026&time=14:{i:02d}"
            ).status_code
            for i in range(31)
        ]
        assert codes[-1] == 429
        assert codes[0] == 200


class TestWalk:
    def test_out_of_service_area(self):
        r = client.get("/api/transport/walk?lat=48.13&lng=11.58")  # Munich
        assert r.status_code == 400

    def test_proxies_osrm(self):
        _FakeAsyncClient.payload = {
            "code": "Ok",
            "routes": [{"distance": 850.3, "duration": 640.0}],
        }
        r = client.get("/api/transport/walk?lat=51.49&lng=7.05")
        assert r.status_code == 200
        assert r.json() == {"distanceM": 850.3, "durationS": 640.0}

    def test_no_route_is_502(self):
        _FakeAsyncClient.payload = {"code": "NoRoute", "routes": []}
        r = client.get("/api/transport/walk?lat=51.49&lng=7.05")
        assert r.status_code == 502

    def test_rate_limited(self):
        _FakeAsyncClient.payload = {
            "code": "Ok",
            "routes": [{"distance": 1, "duration": 1}],
        }
        codes = [
            client.get("/api/transport/walk?lat=51.49&lng=7.05").status_code
            for _ in range(11)
        ]
        assert codes[-1] == 429
