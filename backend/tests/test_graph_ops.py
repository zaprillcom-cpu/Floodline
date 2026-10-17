"""Shelter model, geocoder, citizen reroute, WCC, and live analyst coordination."""

import socket

import pytest

from floodline.analysis import close_district_exits
from floodline.config import Settings
from floodline.db import get_graph, rows
from floodline.navigation import collapse_names, dedupe_route_title
from floodline.routing import ensure_cch
from floodline.scenario import file_report, reset_scenario
from floodline.stubs import (
    ensure_shelter_citizen_model,
    geocode_report,
    reroute_affected,
    route_to_shelter,
    shelter_reachability,
    snap_point,
)
from floodline.tools import close_road, create_report, link_report_closes


def test_route_titles_drop_repeated_road_names():
    assert collapse_names(["Aarey Road", "Aarey Road", "Link Road"]) == ["Aarey Road", "Link Road"]
    assert dedupe_route_title("Aarey Road -> Aarey Road") == "Aarey Road"
    assert dedupe_route_title("Aarey Road → Aarey Road → Link Road (42s)") == "Aarey Road → Link Road (42s)"


def test_generic_highway_names_drop_out_of_titles():
    assert collapse_names(["Aarey Road", "residential", "Aarey Road"]) == ["Aarey Road"]
    assert collapse_names(["Station Road", "unclassified", "Link Road"]) == ["Station Road", "Link Road"]
    assert collapse_names(["residential", "service", "living_street"]) == ["unnamed road"]
    assert dedupe_route_title("Aarey Road → residential → Link Road (175s)") == "Aarey Road → Link Road (175s)"
    assert dedupe_route_title("residential → service (12s)") == "unnamed road (12s)"


def _db_up() -> bool:
    sock = socket.socket()
    sock.settimeout(1)
    try:
        sock.connect(("127.0.0.1", 6379))
        return True
    except OSError:
        return False
    finally:
        sock.close()


needs_db = pytest.mark.skipif(not _db_up(), reason="FalkorDB is not listening on localhost:6379")


@needs_db
def test_shelter_model_is_idempotent_and_snap_uses_the_point():
    reset_scenario()
    graph = get_graph()
    candidates = [
        {
            "id": "school-1",
            "name": "Escola",
            "kind": "school",
            "osmid": 1008,
            "lat": 39.427,
            "lon": -0.385,
            "dist_m": 12,
        }
    ]
    first = ensure_shelter_citizen_model(graph, candidates)
    second = ensure_shelter_citizen_model(graph, candidates)
    assert first["written"] is True
    assert second["near"] == first["near"]
    assert first["near"] >= 2
    near = rows(
        graph.query(
            """
            MATCH (s:Shelter {id: 'school-1'})-[n:NEAR]->(i:Intersection {osmid: 1008})
            RETURN n.dist_m
            """
        )
    )
    assert near and int(near[0][0]) == 12
    snapped = snap_point(graph, 39.4260, -0.4170)
    assert snapped["osmid"] == 1005
    assert snapped["method"] in {"point-index", "haversine"}
    assert "stub" not in snapped


@needs_db
def test_geocode_prefers_the_named_road():
    reset_scenario()
    graph = get_graph()
    geo = geocode_report("CV-36 flooded near Paiporta", graph, lat=39.422, lon=-0.391)
    assert geo["edges"]
    assert {edge["road_id"] for edge in geo["edges"]} == {"cv-36"}
    assert geo["edges"][0]["name"] == "CV-36"


@needs_db
def test_route_to_shelter_writes_a_citizen_and_uses_falkordb():
    reset_scenario()
    graph = get_graph()
    result = route_to_shelter(graph, 39.4260, -0.4170, citizen_id="evalcitizen0001")
    assert result["destination"]["kind"] == "shelter"
    assert result["destination"]["id"] == "alfafar-sports"
    assert result["algo"] in {"cch", "astar", "sppaths"}
    assert "stub" not in result
    citizen = rows(
        graph.query(
            """
            MATCH (c:Citizen {id: 'evalcitizen0001'})-[:AT]->(i:Intersection)
            RETURN i.osmid, c.eta_s
            """
        )
    )
    assert citizen and int(citizen[0][0]) == 1005
    roads = {
        road_id
        for (road_id,) in rows(
            graph.query("MATCH (:Citizen {id: 'evalcitizen0001'})-[r:ROUTED_TO]->() RETURN DISTINCT r.road_id")
        )
    }
    assert roads
    assert "cami-poyo" in roads


@needs_db
def test_reroute_writes_a_decision_that_supersedes_and_affects_the_citizen():
    reset_scenario()
    graph = get_graph()
    route_to_shelter(graph, 39.4260, -0.4170, citizen_id="evalcitizen0001")
    graph.query(
        """
        MERGE (c:Citizen {id: 'citizen'})
        SET c.lat = 39.4260, c.lon = -0.4170, c.active = true
        WITH c
        MATCH (i:Intersection {osmid: 1005})
        CREATE (c)-[:ROUTED_TO {seq: 0, road_id: 'cami-poyo'}]->(i)
        """
    )
    report_id = create_report(graph, "Camí del Poyo is flooded and not passable", "2026-10-17T08:00:00Z")
    close_road(graph, "cami-poyo", by="Scout", at="2026-10-17T08:00:00Z")
    link_report_closes(graph, report_id, "cami-poyo")
    result = reroute_affected(graph, "cami-poyo")
    assert result["citizens"]
    assert result["decisions"]
    decision_id = result["decisions"][0]
    links = rows(
        graph.query(
            """
            MATCH (d:Decision {id: $id})
            OPTIONAL MATCH (d)-[:SUPERSEDES]->(prev:Decision)
            OPTIONAL MATCH (d)-[:BASED_ON]->(rep:Report)
            OPTIONAL MATCH (d)-[:AFFECTS]->(c:Citizen)
            OPTIONAL MATCH (d)-[:REROUTED]->(c2:Citizen)
            RETURN d.old_eta_s, d.new_eta_s, d.delta_s, d.road_id, prev.id, prev.travel_s,
                   prev.result_summary, rep.id, c.id, c2.id
            """,
            {"id": decision_id},
        )
    )
    old_eta, new_eta, delta_s, road_id, previous, previous_travel, previous_summary, report, citizen, rerouted = links[0]
    assert road_id == "cami-poyo"
    assert int(new_eta) >= int(old_eta)
    assert int(delta_s) == int(new_eta) - int(old_eta)
    assert int(previous_travel) == int(old_eta)
    assert f"({int(old_eta)}s)" in (previous_summary or "")
    if int(new_eta) != int(old_eta):
        assert f"({int(new_eta)}s)" not in (previous_summary or "")
    assert previous
    assert report == report_id
    assert citizen == "evalcitizen0001"
    assert rerouted == "evalcitizen0001"
    assert all(item.get("id") != "citizen" for item in result["citizens"])
    still = rows(
        graph.query(
            """
            MATCH (:Citizen {id: 'evalcitizen0001'})-[r:ROUTED_TO {road_id: 'cami-poyo'}]->()
            RETURN count(r)
            """
        )
    )
    assert int(still[0][0]) == 0


@needs_db
def test_shelter_wcc_marks_a_district_that_cannot_reach_a_shelter():
    reset_scenario()
    graph = get_graph()
    ensure_shelter_citizen_model(graph)
    before = shelter_reachability(graph)
    paiporta = next(area for area in before["areas"] if area["name"] == "Paiporta")
    assert paiporta["cut_off"] is False
    close_district_exits(graph, "Paiporta")
    after = shelter_reachability(graph)
    paiporta = next(area for area in after["areas"] if area["name"] == "Paiporta")
    assert paiporta["cut_off"] is True
    assert after["algo"] == "algo.WCC"


@needs_db
def test_report_on_a_non_valencia_graph_uses_real_time_and_all_three_agents():
    cfg = Settings.from_env().for_graph("fl-mini-ee57")
    graph = get_graph(cfg)
    try:
        graph.delete()
    except Exception:
        pass
    graph = get_graph(cfg)
    _load_mini(graph)
    state = file_report(
        "Flooded road at 19.16630, 72.85260. The road is under water and not passable.",
        settings=cfg,
    )
    assert state["analyst"].get("decisions")
    agents = {name for (name,) in rows(graph.query("MATCH (a:Agent) RETURN a.name"))}
    assert {"Scout", "Dispatcher", "Analyst"} <= agents
    close = next(item for item in state["decisions"] if item["kind"] == "close")
    assert close["planner"] == "keyword"
    assert close["at"].startswith("2026-")
    blob = f"{close['explanation']} {close.get('summary')}"
    assert "DANA" not in blob
    assert "Valencia" not in blob
    assert "2024-10-29" not in blob
    route = next(item for item in state["decisions"] if item["kind"] == "route")
    assert "Aarey Road → Aarey Road" not in (route["summary"] or "")
    assert "Aarey Road -> Aarey Road" not in (route["summary"] or "")
    algos = {item["algo"] for item in state["decisions"]}
    assert "algo.WCC" in algos
    assert "algo.maxFlow" in algos
    handoff = rows(
        graph.query(
            """
            MATCH (:Task)-[h:HANDOFF]->(:Task {kind: 'assess'})
            RETURN h.reason
            """
        )
    )
    assert handoff


def _load_mini(graph) -> None:
    nodes = [
        (1, "Aarey Road", 19.1663, 72.8526),
        (2, "Aarey Road", 19.1670, 72.8530),
        (3, "Goregaon shelter", 19.1680, 72.8540),
        # Spur whose midpoint sits on the reported point, so the closure
        # does not cut the unit (node 1) off from the hospital (node 3).
        (5, "Flooded Lane", 19.16631, 72.85261),
    ]
    for osmid, name, lat, lon in nodes:
        graph.query(
            """
            CREATE (:Intersection {osmid: $osmid, name: $name, lat: $lat, lon: $lon, district: 'Goregaon', role: 'junction'})
            """,
            {"osmid": osmid, "name": name, "lat": lat, "lon": lon},
        )
    edges = [
        ("aarey-1", 1, 2, 30),
        ("aarey-2", 2, 3, 40),
        ("flooded-lane", 1, 5, 15),
    ]
    for road_id, src, dst, travel in edges:
        name = "Flooded Lane" if road_id == "flooded-lane" else "Aarey Road"
        for left, right in ((src, dst), (dst, src)):
            graph.query(
                """
                MATCH (a:Intersection {osmid: $src}), (b:Intersection {osmid: $dst})
                CREATE (a)-[:ROAD {
                  road_id: $road_id, name: $name, osm_way: $road_id,
                  length_m: 80, speed_kph: 30, base_travel_s: $travel, travel_s: $travel,
                  base_capacity: 100, capacity: 100, status: 'open'
                }]->(b)
                CREATE (a)-[:PASSABLE {road_id: $road_id}]->(b)
                """,
                {"src": left, "dst": right, "road_id": road_id, "travel": travel, "name": name},
            )
    graph.query(
        """
        MATCH (i:Intersection {osmid: 3})
        CREATE (:Hospital {id: 'goregaon-general', name: 'Goregaon General', beds_total: 20, beds_free: 8, er_open: true, lat: i.lat, lon: i.lon})-[:ACCESS {dist_m: 0}]->(i)
        CREATE (:Shelter {id: 'goregaon-school', name: 'Goregaon school', capacity: 200, occupancy: 10, open: true, lat: i.lat, lon: i.lon})-[:ACCESS {dist_m: 0}]->(i)
        """
    )
    graph.query(
        """
        MATCH (i:Intersection {osmid: 1})
        CREATE (:Unit {callsign: 'M-1', status: 'transport', lat: i.lat, lon: i.lon})-[:ACCESS {dist_m: 0}]->(i)
        """
    )
    graph.query(
        """
        CREATE (:District {name: 'Goregaon', population: 1000, zone_id: 'area', population_source: 'unknown', cut_off: false})
        """
    )
    for osmid in (1, 2, 3, 5):
        graph.query(
            """
            MATCH (i:Intersection {osmid: $osmid}), (d:District {name: 'Goregaon'})
            CREATE (i)-[:IN_DISTRICT]->(d)
            """,
            {"osmid": osmid},
        )
    for name, role in (("Scout", "closures"), ("Dispatcher", "routing")):
        graph.query(
            "CREATE (:Agent {name: $name, role: $role, heartbeat_at: '2026-10-17T08:00:00Z'})",
            {"name": name, "role": role},
        )
    graph.query(
        """
        MERGE (m:Meta {key: 'network'})
        SET m.source = 'osm', m.attribution = 'test', m.district_method = 'area'
        """
    )
    ensure_cch(graph)


class _Rows:
    def __init__(self, result_set):
        self.result_set = result_set


def test_sync_keeps_the_old_decision_title_when_the_path_changes():
    from types import SimpleNamespace

    from floodline.stubs import _sync_citizen_decision

    class Graph:
        def __init__(self, row):
            self.row = row
            self.writes = []

        def query(self, text, params=None):
            self.writes.append(text)
            if "RETURN d.path_osmids" in text:
                return _Rows([self.row])
            return _Rows([])

    changed = Graph(('["1", "2", "3"]', False, 132))
    found = SimpleNamespace(names=["Link Road"], travel_s=175, algo="cch", osmids=[9, 8, 7])
    kept = _sync_citizen_decision(
        changed,
        citizen_id="c1",
        found=found,
        destination={"name": "Hall"},
        at="t",
        existing_id="dec-132",
    )
    assert kept == "dec-132"
    assert not any("SET d.explanation" in text for text in changed.writes)

    superseded = Graph(("[1, 2, 3]", True, 132))
    same = SimpleNamespace(names=["Aarey Road"], travel_s=175, algo="cch", osmids=[1, 2, 3])
    _sync_citizen_decision(
        superseded,
        citizen_id="c1",
        found=same,
        destination={"name": "Hall"},
        at="t",
        existing_id="dec-132",
    )
    assert not any("SET d.explanation" in text for text in superseded.writes)

    fresh = Graph(("[1, 2, 3]", False, 132))
    _sync_citizen_decision(
        fresh,
        citizen_id="c1",
        found=same,
        destination={"name": "Hall"},
        at="t",
        existing_id="dec-132",
    )
    assert any("d.result_summary = $summary" in text for text in fresh.writes)


def test_reroute_decision_payload_stores_delta_from_132_to_175():
    from floodline.stubs import _decision_travel, _write_reroute_decision, reroute_delta

    assert reroute_delta(132, 175) == 43

    class Graph:
        def __init__(self, travel):
            self.travel = travel

        def query(self, text, params=None):
            if "RETURN d.travel_s" in text:
                return _Rows([(self.travel,)])
            return _Rows([])

    assert _decision_travel(Graph(132), "dec-old", 175) == 132
    assert _decision_travel(Graph(None), "dec-old", 175) == 175

    class Writer:
        def __init__(self):
            self.params = []

        def query(self, text, params=None):
            self.params.append(params or {})
            if "RETURN m.seq" in text:
                return _Rows([(4,)])
            return _Rows([])

    writer = Writer()
    _write_reroute_decision(
        writer,
        citizen_id="c1",
        road_id="aarey",
        road_name="Aarey Road",
        old_eta=132,
        new_eta=175,
        found_algo="cch",
        names=["Link Road"],
        osmids=[1, 2],
        previous_id="dec-132",
        report_id="report-1",
        destination={"name": "Hall"},
    )
    created = next(params for params in writer.params if params.get("delta") == 43)
    assert created["old_eta"] == 132
    assert created["new_eta"] == 175
    assert "Delta +43 s" in created["explanation"]
    assert "(175s)" in created["summary"]
    assert not any("prev.result_summary" in text for params in writer.params for text in (str(params),))
