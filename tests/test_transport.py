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
    last_params = None
    last_url = None

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **kw):
        _FakeAsyncClient.calls += 1
        _FakeAsyncClient.last_params = kw.get("params")
        _FakeAsyncClient.last_url = a[0] if a else None
        return _FakeResponse(_FakeAsyncClient.payload, _FakeAsyncClient.status)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.payload = None
    _FakeAsyncClient.status = 200
    _FakeAsyncClient.calls = 0
    _FakeAsyncClient.last_params = None
    _FakeAsyncClient.last_url = None
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

    def test_outbound_default_uses_zollverein_stop(self):
        _FakeAsyncClient.payload = {"departureList": [_efa_departure()]}
        client.get("/api/transport/departures?date=10.07.2026&time=14:00")
        assert _FakeAsyncClient.last_params["name_dm"] == "20009206"

    def test_inbound_uses_hbf_stop_and_reverse_filter(self):
        _FakeAsyncClient.payload = {
            "departureList": [
                _efa_departure("107", "Gelsenkirchen Hbf"),
                _efa_departure("107", "Essen Hanielstr. Schleife"),
                _efa_departure("107", "Essen Hauptbahnhof"),  # outbound terminus
                _efa_departure("107", "Essen Bredeney"),  # outbound terminus
            ]
        }
        r = client.get(
            "/api/transport/departures?date=10.07.2026&time=14:00&dir=inbound"
        )
        assert r.status_code == 200
        dirs = [d["direction"] for d in r.json()["departures"]]
        assert "Gelsenkirchen Hbf" in dirs
        assert any("Hanielstr" in d for d in dirs)
        # Outbound termini must be filtered out of the inbound board.
        assert all("Hauptbahnhof" not in d and "Bredeney" not in d for d in dirs)
        # Inbound queries the Essen Hbf stop, never a client-supplied id.
        assert _FakeAsyncClient.last_params["name_dm"] == "20009289"

    def test_invalid_dir_rejected(self):
        r = client.get(
            "/api/transport/departures?date=10.07.2026&time=14:00&dir=sideways"
        )
        assert r.status_code == 400

    def test_direction_has_separate_cache(self):
        _FakeAsyncClient.payload = {"departureList": [_efa_departure()]}
        client.get("/api/transport/departures?date=10.07.2026&time=14:00&dir=outbound")
        client.get("/api/transport/departures?date=10.07.2026&time=14:00&dir=inbound")
        # Same (date, time) but different dir must not collide in the cache.
        assert _FakeAsyncClient.calls == 2

    def test_cache_collapses_upstream_calls(self):
        _FakeAsyncClient.payload = {"departureList": [_efa_departure()]}
        for _ in range(5):
            r = client.get("/api/transport/departures?date=10.07.2026&time=14:00")
            assert r.status_code == 200
        assert _FakeAsyncClient.calls == 1
        # Same date+dir with a different time still hits the cache (no time in key)
        client.get("/api/transport/departures?date=10.07.2026&time=14:05")
        assert _FakeAsyncClient.calls == 1
        # A different date is a fresh upstream call
        client.get("/api/transport/departures?date=11.07.2026&time=14:05")
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


def _trip_journey(name="RE6", cls=13, **extra):
    leg = {
        "origin": {
            "departureTimePlanned": "2026-07-10T12:03:00Z",
            "departureTimeEstimated": "2026-07-10T12:07:00Z",
            "properties": {"platform": "5"},
        },
        "destination": {
            "arrivalTimePlanned": "2026-07-10T12:27:00Z",
            "arrivalTimeEstimated": "2026-07-10T12:30:00Z",
        },
        "transportation": {
            "disassembledName": name,
            "product": {"class": cls},
            "properties": {"trainNumber": "89737"},
        },
    }
    return {"interchanges": extra.get("interchanges", 0), "legs": [leg]}


class TestDuesseldorf:
    def test_route_maps_trip_realtime(self):
        _FakeAsyncClient.payload = {"journeys": [_trip_journey()]}
        r = client.get(
            "/api/transport/departures?date=10.07.2026&time=14:00&route=duesseldorf"
        )
        assert r.status_code == 200
        deps = r.json()["departures"]
        assert len(deps) == 1
        d = deps[0]
        # 12:03 UTC -> 14:03 Berlin (CEST); estimated 12:07 -> 14:07, +4 min.
        assert d["line"] == "RE6"
        assert d["scheduled"] == "14:03"
        assert d["real"] == "14:07"
        assert d["delay"] == 4
        assert d["platform"] == "5"
        assert d["trainNumber"] == "89737"
        assert d["arr"] == "14:27"
        assert d["arrReal"] == "14:30"
        assert "_iso" not in d
        # Origin is pinned server-side (D-Flughafen), never client-supplied.
        assert _FakeAsyncClient.last_params["name_origin"] == "20018488"

    def test_inbound_swaps_origin_to_essen(self):
        _FakeAsyncClient.payload = {"journeys": []}
        client.get(
            "/api/transport/departures?date=10.07.2026&time=14:00&route=duesseldorf&dir=inbound"
        )
        assert _FakeAsyncClient.last_params["name_origin"] == "20009289"

    def test_long_distance_filtered_out(self):
        # ICE/IC come through as bare train numbers (class 15/16) -> dropped.
        _FakeAsyncClient.payload = {"journeys": [_trip_journey(name="849", cls=16)]}
        r = client.get(
            "/api/transport/departures?date=10.07.2026&time=14:00&route=duesseldorf"
        )
        assert r.json()["departures"] == []

    def test_non_direct_filtered_out(self):
        _FakeAsyncClient.payload = {"journeys": [_trip_journey(interchanges=1)]}
        r = client.get(
            "/api/transport/departures?date=10.07.2026&time=14:00&route=duesseldorf"
        )
        assert r.json()["departures"] == []


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

    def test_walk_dir_routes_to_direction_departure_stop(self):
        _FakeAsyncClient.payload = {
            "code": "Ok",
            "routes": [{"distance": 100, "duration": 90}],
        }
        client.get("/api/transport/walk?lat=51.49&lng=7.05&dir=outbound")
        assert "7.046062,51.486095" in _FakeAsyncClient.last_url  # Zollverein
        client.get("/api/transport/walk?lat=51.49&lng=7.05&dir=inbound")
        assert "7.012213,51.449732" in _FakeAsyncClient.last_url  # Essen Hbf

    def test_walk_invalid_dir_rejected(self):
        r = client.get("/api/transport/walk?lat=51.49&lng=7.05&dir=nope")
        assert r.status_code == 400

    def test_walk_route_duesseldorf_targets_airport(self):
        _FakeAsyncClient.payload = {
            "code": "Ok",
            "routes": [{"distance": 50, "duration": 60}],
        }
        client.get("/api/transport/walk?lat=51.29&lng=6.79&route=duesseldorf")
        # Düsseldorf view walks to D-Flughafen Bf, never a client-supplied point.
        assert "6.787158,51.291368" in _FakeAsyncClient.last_url

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
