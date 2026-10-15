"""Citizen area loads. No FalkorDB and no live Overpass."""

import threading
import time

import networkx as nx
import pytest
from fastapi.testclient import TestClient

from floodline.api import app
from floodline.area import (
    OverpassFailed,
    RateLimited,
    load_citizen_area,
    reset_area_jobs,
    reset_rate_limit,
    tile_graph_id,
)
from floodline.network import haversine_m
from floodline.regions import REGIONS, crop_tile, region_containing
from floodline.osm_network import (
    USER_AGENT,
    _post_overpass,
    fetch_area_drive_graph,
    highway_overpass_query,
    nearest_shelters,
)
from floodline.stubs import ensure_shelter_citizen_model, route_to_shelter


def _street():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=-0.010, y=39.450)
    graph.add_node(2, x=0.010, y=39.452)
    graph.add_edge(
        1, 2, osmid=10, name="Carrer Prova", highway="residential", length=80,
        speed_kph=30, travel_time=10,
    )
    return graph


class _InlineThread:
    """Run the worker before ``start`` returns, so status is settled in the test."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args

    def start(self):
        if self._target is not None:
            self._target(*self._args)


def _wait_job(client: TestClient, job_id: str) -> dict:
    last = {}
    for _ in range(40):
        response = client.get("/api/area/status", params={"job_id": job_id})
        assert response.status_code == 200
        last = response.json()
        if last["status"] in {"ready", "failed"}:
            return last
        time.sleep(0.02)
    raise AssertionError(last)


def test_tile_id_is_stable_for_a_rounded_cell():
    assert tile_graph_id(39.45, -0.40) == "fl-p3945-n00040"
    assert tile_graph_id(39.47, -0.38) == "fl-p3945-n00040"
    assert tile_graph_id(39.48, -0.37) == "fl-p3950-n00035"
    assert tile_graph_id(-33.87, 151.21) == "fl-n3385-p15120"


def test_area_fetch_retries_the_mirror_and_does_not_write_the_extract(monkeypatch):
    calls = []

    def download(bbox, url, timeout_s):
        calls.append((url, timeout_s))
        if "overpass-api.de" in url:
            raise TimeoutError("slow")
        return _street()

    def refuse(*_args, **_kwargs):
        raise AssertionError("Valencia extract must stay untouched")

    monkeypatch.setattr("floodline.osm_network.write_extract", refuse)
    prepared, url = fetch_area_drive_graph((-0.02, 39.44, 0.02, 39.46), download=download)
    assert len(calls) == 2
    assert calls[0][0] == "https://overpass-api.de/api"
    assert calls[0][1] == 10
    assert "kumi.systems" in url
    assert prepared["bbox"] == {"west": -0.02, "south": 39.44, "east": 0.02, "north": 39.46}
    assert prepared["district_method"] == "area"
    assert prepared["nodes"][0]["district"] == "here"
    assert prepared["edges"]


def test_shelter_candidates_are_capped_at_eight():
    records = [
        {"id": str(i), "name": f"School {i}", "kind": "school", "lat": 39.4 + i * 0.01, "lon": -0.4}
        for i in range(20)
    ]
    chosen = nearest_shelters(records, 39.4, -0.4)
    assert len(chosen) == 8
    assert chosen[0]["id"] == "0"
    assert chosen[0]["dist_m"] <= chosen[-1]["dist_m"]


def test_cached_tile_skips_overpass_and_the_rate_limit(monkeypatch):
    reset_rate_limit()
    monkeypatch.setattr("floodline.area.area_is_cached", lambda settings: True)
    monkeypatch.setattr(
        "floodline.area.read_cached_area",
        lambda settings: {"ok": True, "cached": True, "graph": settings.graph},
    )

    def boom(*_args, **_kwargs):
        raise AssertionError("cached tile must not call Overpass")

    monkeypatch.setattr("floodline.area.fetch_area_drive_graph", boom)
    for _ in range(6):
        payload = load_citizen_area(39.45, -0.40, client="same")
    assert payload["cached"] is True
    assert payload["graph"] == "fl-p3945-n00040"


def _force_overpass(monkeypatch):
    monkeypatch.setattr("floodline.area.region_containing", lambda lat, lon: None)


def test_uncached_loads_are_rate_limited(monkeypatch):
    reset_rate_limit()
    _force_overpass(monkeypatch)
    monkeypatch.setattr("floodline.area.area_is_cached", lambda settings: False)
    monkeypatch.setattr(
        "floodline.area.fetch_area_drive_graph",
        lambda bbox: ({"nodes": [], "edges": [{"src": 1}], "bbox": {}}, "https://mirror.test"),
    )
    seen = {}

    def persist(settings, prepared, candidates, *, origin):
        seen["candidates"] = candidates
        seen["origin"] = origin
        return {"ok": True, "graph": settings.graph, "cached": False, "origin": origin}

    monkeypatch.setattr("floodline.area.fetch_shelter_candidates", lambda *_a, **_k: [{"id": "school-1", "name": "School", "kind": "school", "lat": 39.45, "lon": -0.4}])
    monkeypatch.setattr("floodline.area.persist_area", persist)
    for _ in range(4):
        assert load_citizen_area(39.45, -0.40, client="citizen")["cached"] is False
    with pytest.raises(RateLimited) as exc:
        load_citizen_area(39.45, -0.40, client="citizen")
    assert "bundled city" in str(exc.value)
    assert seen["origin"] == "overpass"
    assert seen["candidates"][0]["name"] == "School"


def test_overpass_failure_names_the_valencia_demo(monkeypatch):
    reset_rate_limit()
    monkeypatch.setattr("floodline.area.area_is_cached", lambda settings: False)

    def boom(_bbox):
        raise TimeoutError("down")

    monkeypatch.setattr("floodline.area.fetch_area_drive_graph", boom)
    with pytest.raises(OverpassFailed) as exc:
        load_citizen_area(10, 10, client="citizen")
    assert "bundled city" in str(exc.value)


def test_highway_query_is_one_bounded_overpass_request():
    query = highway_overpass_query((-0.02, 39.44, 0.02, 39.46), 20)
    assert '["highway"]' in query
    assert "[timeout:20]" in query
    assert "out body;" in query
    assert "(39.44,-0.02,39.46,0.02)" in query


def test_a_hung_mirror_is_abandoned_at_the_deadline():
    calls = []

    def download(bbox, url, timeout_s):
        calls.append(url)
        if "overpass-api.de" in url:
            time.sleep(2)
            return _street()
        return _street()

    started = time.monotonic()
    prepared, url = fetch_area_drive_graph(
        (-0.02, 39.44, 0.02, 39.46),
        timeout_s=0.2,
        download=download,
    )
    assert time.monotonic() - started < 1
    assert len(calls) == 2
    assert "kumi" in url
    assert prepared["edges"]


def test_load_reports_fetching_then_indexing(monkeypatch):
    reset_rate_limit()
    _force_overpass(monkeypatch)
    notes = []
    monkeypatch.setattr("floodline.area.area_is_cached", lambda settings: False)
    monkeypatch.setattr(
        "floodline.area.fetch_area_drive_graph",
        lambda bbox: ({"nodes": [], "edges": [{"src": 1}], "bbox": {}}, "https://mirror.test"),
    )
    monkeypatch.setattr("floodline.area.fetch_shelter_candidates", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "floodline.area.persist_area",
        lambda settings, prepared, candidates, origin: {"ok": True, "graph": settings.graph},
    )
    load_citizen_area(19.076, 72.877, 1000, client="stages", on_status=lambda status, _message: notes.append(status))
    assert notes == ["fetching", "indexing"]


def test_shelters_use_only_the_mirror_that_served_streets(monkeypatch):
    reset_rate_limit()
    _force_overpass(monkeypatch)
    seen = {}
    monkeypatch.setattr("floodline.area.area_is_cached", lambda settings: False)
    monkeypatch.setattr(
        "floodline.area.fetch_area_drive_graph",
        lambda bbox: ({"nodes": [], "edges": [{"src": 1}], "bbox": {}}, "https://overpass.kumi.systems/api"),
    )

    def shelters(*_args, **kwargs):
        seen["urls"] = kwargs.get("urls")
        return []

    monkeypatch.setattr("floodline.area.fetch_shelter_candidates", shelters)
    monkeypatch.setattr(
        "floodline.area.persist_area",
        lambda settings, prepared, candidates, origin: {"ok": True, "graph": settings.graph, "origin": origin},
    )
    load_citizen_area(19.076, 72.877, 1000, client="mumbai")
    assert seen["urls"] == ("https://overpass.kumi.systems/api",)


def test_area_endpoint_caps_radius_and_skips_admin(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setattr("floodline.area.threading.Thread", _InlineThread)
    seen = {}

    def fake(lat, lon, radius_m=2000, *, client="unknown", settings=None, on_status=None):
        seen.update(lat=lat, lon=lon, radius_m=radius_m, client=client)
        if on_status:
            on_status("fetching", "Downloading streets from OpenStreetMap.")
        return {"ok": True, "graph": "fl-p0000-p00000", "bbox": {}}

    monkeypatch.setattr("floodline.area.load_citizen_area", fake)
    client = TestClient(app)
    too_wide = client.post("/api/area/load", json={"lat": 1, "lon": 2, "radius_m": 5001})
    assert too_wide.status_code == 422
    ok = client.post(
        "/api/area/load",
        json={"lat": 1, "lon": 2},
        headers={"X-Forwarded-For": "203.0.113.8"},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "queued"
    assert seen["radius_m"] == 2000
    assert seen["client"] == "203.0.113.8"
    settled = _wait_job(client, ok.json()["job_id"])
    assert settled["status"] == "ready"
    capped = client.post("/api/area/load", json={"lat": 1, "lon": 2, "radius_m": 5000})
    assert capped.status_code == 200
    assert seen["radius_m"] == 5000
    assert client.get("/api/area/status", params={"job_id": "missing-job"}).status_code == 404


def test_post_returns_before_a_blocked_download(monkeypatch):
    release = threading.Event()

    def block(*_args, **_kwargs):
        release.wait(3)
        return {"ok": True, "graph": "fl-p1908-p7288", "cached": False}

    monkeypatch.setattr("floodline.area.load_citizen_area", block)
    client = TestClient(app)
    started = time.perf_counter()
    response = client.post("/api/area/load", json={"lat": 19.076, "lon": 72.877, "radius_m": 1000})
    elapsed = time.perf_counter() - started
    release.set()
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["job_id"]
    assert elapsed < 0.5


def test_api_rate_limit_and_overpass_status(monkeypatch):
    reset_rate_limit()
    reset_area_jobs()
    _force_overpass(monkeypatch)
    monkeypatch.setattr("floodline.area.threading.Thread", _InlineThread)
    monkeypatch.setattr("floodline.area.area_is_cached", lambda settings: False)
    monkeypatch.setattr("floodline.area.fetch_shelter_candidates", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "floodline.area.persist_area",
        lambda settings, prepared, candidates, origin: {"ok": True, "graph": settings.graph},
    )
    state = {"fail": False}

    def fetch(_bbox):
        if state["fail"]:
            raise TimeoutError("down")
        return {"edges": [1], "nodes": [], "bbox": {}}, "https://mirror.test"

    monkeypatch.setattr("floodline.area.fetch_area_drive_graph", fetch)
    client = TestClient(app)
    for _ in range(4):
        queued = client.post("/api/area/load", json={"lat": 39.45, "lon": -0.4})
        assert queued.status_code == 200
        assert queued.json()["status"] == "queued"
        assert _wait_job(client, queued.json()["job_id"])["status"] == "ready"
    blocked = client.post("/api/area/load", json={"lat": 39.45, "lon": -0.4})
    assert blocked.status_code == 200
    failed_limit = _wait_job(client, blocked.json()["job_id"])
    assert failed_limit["status"] == "failed"
    assert "bundled city" in failed_limit["message"]

    reset_rate_limit()
    state["fail"] = True
    queued = client.post("/api/area/load", json={"lat": 12, "lon": 12})
    failed = _wait_job(client, queued.json()["job_id"])
    assert failed["status"] == "failed"
    assert "bundled city" in failed["message"]


def test_valencia_demo_uses_the_bundled_extract(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setattr("floodline.area.area_is_cached", lambda settings: False)

    def fake(settings, refresh=False):
        assert refresh is False
        assert settings.graph == "valencia"
        return {
            "origin": "extract",
            "loaded": {"intersections": 2526, "road_edges": 4742, "passable": 4742},
            "counts_by_kind": {"shelter": 2},
        }

    monkeypatch.setattr("floodline.area.load_osm_graph", fake)
    response = TestClient(app).post("/api/area/valencia")
    assert response.status_code == 200
    body = response.json()
    assert body["graph"] == "valencia"
    assert body["origin"] == "extract"
    assert body["cached"] is False
    assert body["intersections"] == 2526
    assert body["shelters"] == 2


def test_valencia_demo_does_not_reload_a_warm_graph(monkeypatch):
    monkeypatch.setattr("floodline.area.area_is_cached", lambda settings: True)
    monkeypatch.setattr(
        "floodline.area.read_cached_area",
        lambda settings: {"ok": True, "graph": settings.graph, "cached": True, "origin": "cache"},
    )

    def boom(*_args, **_kwargs):
        raise AssertionError("warm Valencia graph must not be deleted")

    monkeypatch.setattr("floodline.area.load_osm_graph", boom)
    body = TestClient(app).post("/api/area/valencia").json()
    assert body["cached"] is True
    assert body["graph"] == "valencia"


def test_routing_endpoints_take_a_graph_id(monkeypatch):
    seen = {}

    def fake_route(lat, lon, settings=None, dest_id=None):
        seen["route"] = settings.graph
        seen["dest"] = dest_id
        return {"ok": True}

    def fake_overlay(settings=None):
        seen["overlay"] = settings.graph
        return {"loaded": False}

    def fake_graph(settings=None):
        seen["zones"] = settings.graph
        return object()

    monkeypatch.setattr("floodline.api.route_payload", fake_route)
    monkeypatch.setattr("floodline.api.overlay_payload", fake_overlay)
    monkeypatch.setattr("floodline.api.get_graph", fake_graph)
    monkeypatch.setattr("floodline.api.cutoff_zones", lambda graph: {"zones": []})
    monkeypatch.setattr("floodline.api.collect_events", lambda graph, since: [])
    client = TestClient(app)
    assert client.get("/api/route?lat=1&lon=2&graph=fl-p3945-n00040").status_code == 200
    assert seen["route"] == "fl-p3945-n00040"
    assert client.get("/api/route?lat=1&lon=2&graph=valencia").status_code == 200
    assert seen["route"] == "valencia"
    assert client.get("/api/route?lat=19.076&lon=72.877&graph=mumbai").status_code == 200
    assert seen["route"] == "mumbai"
    assert client.get("/api/route?lat=40.7074&lon=-74.0113&graph=nyc").status_code == 200
    assert seen["route"] == "nyc"
    assert client.get("/api/route?lat=19.1663&lon=72.8526&graph=fl-p1915-p07285&dest=school-1").status_code == 200
    assert seen["dest"] == "school-1"
    assert client.get("/api/route?lat=1&lon=2&graph=not-a-graph").status_code == 400
    assert client.get("/api/escape/overlay?graph=valencia").status_code == 200
    assert seen["overlay"] == "valencia"
    assert client.get("/api/escape/zones?graph=fl-p3945-n00040").status_code == 200
    assert seen["zones"] == "fl-p3945-n00040"
    assert client.get("/api/feed?graph=valencia").status_code == 200
    assert client.get("/api/stream?once=true&graph=drop").status_code == 400


class _Result:
    def __init__(self, result_set):
        self.result_set = result_set


class _Graph:
    def __init__(self):
        self.queries = []

    def query(self, query, params=None):
        self.queries.append(query)
        if "count" in query:
            return _Result([[0]])
        return _Result([])


def test_osm_candidates_go_through_the_shelter_stub_not_a_near_model():
    graph = _Graph()
    candidates = [
        {
            "id": "osm-node-9",
            "name": "Escola",
            "kind": "school",
            "osmid": 9,
            "lat": 39.45,
            "lon": -0.4,
            "dist_m": 12,
        }
    ]
    result = ensure_shelter_citizen_model(graph, candidates)
    assert result["written"] is False
    assert result["stub"] == "ensure_shelter_citizen_model"
    assert result["owner"] == "Sahil"
    assert result["candidates"] == 1
    writes = [query for query in graph.queries if "MERGE" in query or "CREATE" in query]
    assert writes
    assert all("NEAR" not in query for query in writes)
    assert any("ACCESS" in query for query in writes)
    assert "candidates" in (route_to_shelter.__doc__ or "")
    assert "TODO(Sahil)" in (route_to_shelter.__doc__ or "")


def test_overpass_tries_every_mirror_and_sends_a_user_agent(monkeypatch):
    calls = []

    def download(bbox, url, timeout_s):
        calls.append((url, timeout_s))
        raise TimeoutError("down")

    started = time.monotonic()
    with pytest.raises(RuntimeError):
        fetch_area_drive_graph((-0.02, 39.44, 0.02, 39.46), download=download)
    assert [url for url, _slot in calls] == [
        "https://overpass-api.de/api",
        "https://overpass.kumi.systems/api",
        "https://overpass.private.coffee/api",
        "https://maps.mail.ru/osm/tools/overpass/api",
    ]
    assert calls[0][1] == 10
    assert time.monotonic() - started < 4

    import requests

    seen = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"elements": []}

    def post(url, data=None, timeout=None, headers=None):
        seen["url"] = url
        seen["headers"] = headers
        return _Response()

    monkeypatch.setattr(requests, "post", post)
    _post_overpass("https://overpass.private.coffee/api", "[out:json];out;", 5)
    assert seen["headers"]["User-Agent"] == USER_AGENT
    assert seen["url"] == "https://overpass.private.coffee/api/interpreter"


def _nearest_node(nodes, lat, lon):
    return min(nodes, key=lambda node: haversine_m(lat, lon, node["lat"], node["lon"]))


def test_offline_regions_cover_goregaon_manhattan_and_valencia():
    caps = {"mumbai": 50_000_000, "manhattan": 20_000_000, "valencia": 20_000_000}
    by_id = {region.id: region for region in REGIONS}
    assert set(by_id) == set(caps)
    goregaon = region_containing(19.1663, 72.8526)
    assert goregaon is not None and goregaon.id == "mumbai"
    midtown = region_containing(40.758, -73.9855)
    assert midtown is not None and midtown.id == "manhattan"
    paiporta = region_containing(39.4294, -0.4183)
    assert paiporta is not None and paiporta.id == "valencia"
    assert region_containing(0, 0) is None
    for region in REGIONS:
        assert region.path.is_file()
        assert region.path.stat().st_size < caps[region.id]
        assert region.contains(region.center_lat, region.center_lon)


def test_goregaon_offline_tile_starts_on_a_nearby_street():
    region = region_containing(19.1663, 72.8526)
    assert region is not None
    prepared, shelters, hospitals = crop_tile(region, 19.1663, 72.8526, 2500)
    nearest = _nearest_node(prepared["nodes"], 19.1663, 72.8526)
    dist = haversine_m(19.1663, 72.8526, nearest["lat"], nearest["lon"])
    assert dist < 50
    assert shelters and hospitals
    assert len(shelters) <= 8
    assert len(hospitals) <= 4
    roads = nx.DiGraph()
    for edge in prepared["edges"]:
        roads.add_edge(edge["src"], edge["dst"])
    shelter_node = _nearest_node(prepared["nodes"], shelters[0]["lat"], shelters[0]["lon"])
    assert nx.has_path(roads, nearest["osmid"], shelter_node["osmid"])
    assert tile_graph_id(19.1663, 72.8526) == "fl-p1915-p07285"


def test_offline_goregaon_loads_skip_overpass_and_the_rate_limit(monkeypatch):
    reset_rate_limit()
    monkeypatch.setattr("floodline.area.area_is_cached", lambda settings: False)

    def persist(settings, prepared, candidates, *, origin, hospitals=None, unit=None):
        return {
            "ok": True,
            "graph": settings.graph,
            "cached": False,
            "origin": origin,
            "shelters": len(candidates),
            "hospitals": len(hospitals or []),
            "callsign": (unit or {}).get("callsign"),
        }

    def boom(*_args, **_kwargs):
        raise AssertionError("a point inside a bundled region must not call Overpass")

    monkeypatch.setattr("floodline.area.persist_area", persist)
    monkeypatch.setattr("floodline.area.fetch_area_drive_graph", boom)
    for _ in range(6):
        payload = load_citizen_area(19.1663, 72.8526, client="goregaon")
    assert payload["origin"] == "offline"
    assert payload["graph"] == "fl-p1915-p07285"
    assert payload["shelters"] >= 1
    assert payload["hospitals"] >= 1
    assert payload["callsign"] == "M-1"


def test_city_endpoint_loads_the_goregaon_tile(monkeypatch):
    monkeypatch.setattr("floodline.area.area_is_cached", lambda settings: False)
    seen = {}

    def persist(settings, prepared, candidates, *, origin, hospitals=None, unit=None):
        seen["nodes"] = prepared["nodes"]
        seen["shelters"] = candidates
        seen["hospitals"] = hospitals or []
        seen["unit"] = unit
        seen["origin"] = origin
        return {
            "ok": True,
            "graph": settings.graph,
            "cached": False,
            "origin": origin,
            "place": prepared.get("place"),
            "center": prepared.get("center"),
        }

    def boom(*_args, **_kwargs):
        raise AssertionError("bundled city must not call Overpass")

    monkeypatch.setattr("floodline.area.persist_area", persist)
    monkeypatch.setattr("floodline.area.fetch_area_drive_graph", boom)
    client = TestClient(app)
    response = client.post("/api/area/city/mumbai")
    assert response.status_code == 200
    body = response.json()
    assert body["graph"] == "fl-p1915-p07285"
    assert body["origin"] == "offline"
    assert body["cached"] is False
    assert body["center"]["lat"] == pytest.approx(19.1663)
    assert body["center"]["lon"] == pytest.approx(72.8526)
    nearest = _nearest_node(seen["nodes"], 19.1663, 72.8526)
    assert haversine_m(19.1663, 72.8526, nearest["lat"], nearest["lon"]) < 50
    assert seen["shelters"] and seen["hospitals"]
    assert seen["unit"]["callsign"] == "M-1"
    assert client.post("/api/area/city/lisbon").status_code == 404


def test_warm_city_graph_is_not_reloaded(monkeypatch):
    monkeypatch.setattr("floodline.area.area_is_cached", lambda settings: True)
    monkeypatch.setattr(
        "floodline.area.read_cached_area",
        lambda settings: {"ok": True, "graph": settings.graph, "cached": True, "origin": "offline"},
    )

    def boom(*_args, **_kwargs):
        raise AssertionError("warm city graph must not be reloaded")

    monkeypatch.setattr("floodline.area.crop_tile", boom)
    monkeypatch.setattr("floodline.area.fetch_area_drive_graph", boom)
    body = TestClient(app).post("/api/area/city/nyc").json()
    assert body["cached"] is True
    assert body["graph"] == tile_graph_id(40.758, -73.9855)
    assert body["place"] == "New York"
    assert body["center"]["lat"] == pytest.approx(40.758)
    assert body["center"]["lon"] == pytest.approx(-73.9855)


def test_valencia_city_slug_uses_the_existing_demo(monkeypatch):
    monkeypatch.setattr("floodline.area.area_is_cached", lambda settings: False)

    def fake(settings, refresh=False):
        assert refresh is False
        assert settings.graph == "valencia"
        return {
            "origin": "extract",
            "loaded": {"intersections": 2526, "road_edges": 4742, "passable": 4742},
            "counts_by_kind": {"shelter": 2},
        }

    monkeypatch.setattr("floodline.area.load_osm_graph", fake)
    body = TestClient(app).post("/api/area/city/valencia").json()
    assert body["graph"] == "valencia"
    assert body["place"] == "Valencia"
    assert body["center"]["lat"] == pytest.approx(39.4294)
    assert body["shelters"] == 2


def test_report_and_sse_accept_every_bundled_city(monkeypatch):
    seen = {}

    def fake_report(text, settings=None):
        seen["report"] = settings.graph
        return {"closed_roads": []}

    monkeypatch.setattr("floodline.api.file_report", fake_report)
    monkeypatch.setattr("floodline.api.note_reroute", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("floodline.api.get_graph", lambda settings=None: object())
    monkeypatch.setattr("floodline.api.collect_events", lambda graph, since: [])
    client = TestClient(app)
    for graph in ("valencia", "mumbai", "nyc"):
        response = client.post("/api/scenario/report", json={"text": "Water over the road ahead.", "graph": graph})
        assert response.status_code == 200, response.text
        assert seen["report"] == graph
        stream = client.get("/api/stream", params={"once": "true", "graph": graph})
        assert stream.status_code == 200
        assert "heartbeat" in stream.text
    from pathlib import Path

    provisional = Path(__file__).resolve().parents[1].joinpath("floodline", "provisional.py").read_text(encoding="utf-8")
    body = provisional.split("def attach_provisional_shelters", 1)[1]
    assert "NEAR" not in body
    assert "ACCESS" in body
