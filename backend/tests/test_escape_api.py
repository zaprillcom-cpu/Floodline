"""Admin token, SSE formatting, and graph-operation helpers. None of these need FalkorDB."""

from pathlib import Path

from fastapi.testclient import TestClient

from floodline.agents.llm_planner import plan_with_llm
from floodline.agents.planner import plan
from floodline.api import app
from floodline.escape import _convex_hull, closure_kind
from floodline.events import collect_events, decision_event, query_label, travel_delta
from floodline.navigation import build_steps
from floodline.stubs import geocode_report, reroute_affected, route_to_shelter, snap_point

ROOT = Path(__file__).resolve().parents[1]


def test_admin_requires_a_configured_token(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    response = TestClient(app).post("/api/admin/load-valencia")
    assert response.status_code == 503
    assert "ADMIN_TOKEN" in response.json()["detail"]


def test_admin_rejects_a_wrong_token(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "correct-token")
    response = TestClient(app).post(
        "/api/admin/load-valencia",
        headers={"Authorization": "Bearer nope"},
    )
    assert response.status_code == 401


def test_admin_load_returns_valencia_counts(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "correct-token")

    def fake_load(settings, refresh=False):
        assert refresh is False
        return {
            "origin": "extract",
            "version": "4.22.0",
            "loaded": {"intersections": 2526, "road_edges": 4742, "passable": 4742},
        }

    monkeypatch.setattr("floodline.api.load_osm_graph", fake_load)
    response = TestClient(app).post(
        "/api/admin/load-valencia",
        headers={"X-Admin-Token": "correct-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["intersections"] == 2526
    assert body["road_edges"] == 4742
    assert body["origin"] == "extract"
    assert "correct-token" not in response.text


def test_route_requires_coordinates():
    response = TestClient(app).get("/api/route")
    assert response.status_code == 422


def test_stream_once_emits_events_and_a_heartbeat(monkeypatch):
    def fake_pull(since, settings=None):
        if since >= 4:
            return []
        return [
            {
                "type": "decision",
                "id": "dec-1",
                "seq": 4,
                "agent": "Scout",
                "kind": "close",
                "title": "closed",
                "body": "road closed",
                "query": "SET ROAD",
            }
        ]

    monkeypatch.setattr("floodline.api._pull_events", fake_pull)
    response = TestClient(app).get("/api/stream?once=true")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: decision" in response.text
    assert "event: heartbeat" in response.text
    assert "Scout" in response.text


def test_unnamed_edges_become_a_continue_step():
    hops = [
        {
            "lat1": 39.43,
            "lon1": -0.42,
            "lat2": 39.431,
            "lon2": -0.42,
            "name": "residential",
            "length_m": 80,
            "road_id": "a",
        },
        {
            "lat1": 39.431,
            "lon1": -0.42,
            "lat2": 39.432,
            "lon2": -0.42,
            "name": "",
            "length_m": 40,
            "road_id": "b",
        },
    ]
    steps = build_steps(hops)
    assert len(steps) == 1
    assert steps[0]["name"] == "unnamed road"
    assert "unnamed road" in steps[0]["instruction"]
    assert "residential" not in steps[0]["instruction"]
    assert steps[0]["distance_m"] == 120


def test_a_bend_names_the_next_street():
    hops = [
        {
            "lat1": 39.43,
            "lon1": -0.42,
            "lat2": 39.431,
            "lon2": -0.42,
            "name": "Carrer Major",
            "length_m": 100,
            "road_id": "a",
        },
        {
            "lat1": 39.431,
            "lon1": -0.42,
            "lat2": 39.431,
            "lon2": -0.4185,
            "name": "CV-36",
            "length_m": 120,
            "road_id": "b",
        },
    ]
    steps = build_steps(hops)
    assert steps[0]["instruction"] == "Head north on Carrer Major"
    assert steps[1]["instruction"] == "Turn right onto CV-36"
    assert steps[1]["from_m"] == 100


def test_generic_hops_merge_into_the_neighbouring_named_step():
    hops = [
        {
            "lat1": 39.43,
            "lon1": -0.42,
            "lat2": 39.431,
            "lon2": -0.42,
            "name": "Station Road",
            "length_m": 100,
            "road_id": "a",
        },
        {
            "lat1": 39.431,
            "lon1": -0.42,
            "lat2": 39.4314,
            "lon2": -0.42,
            "name": "residential",
            "length_m": 40,
            "road_id": "b",
        },
        {
            "lat1": 39.4314,
            "lon1": -0.42,
            "lat2": 39.4314,
            "lon2": -0.4185,
            "name": "Link Road",
            "length_m": 80,
            "road_id": "c",
        },
    ]
    steps = build_steps(hops)
    assert [step["name"] for step in steps] == ["Station Road", "Link Road"]
    assert steps[0]["distance_m"] == 140
    assert steps[1]["distance_m"] == 80
    assert all("residential" not in step["instruction"] for step in steps)

    leading = build_steps(
        [
            {
                "lat1": 39.43,
                "lon1": -0.42,
                "lat2": 39.4304,
                "lon2": -0.42,
                "name": "service",
                "length_m": 30,
                "road_id": "s",
            },
            {
                "lat1": 39.4304,
                "lon1": -0.42,
                "lat2": 39.431,
                "lon2": -0.42,
                "name": "Aarey Road",
                "length_m": 70,
                "road_id": "a",
            },
        ]
    )
    assert len(leading) == 1
    assert leading[0]["name"] == "Aarey Road"
    assert leading[0]["distance_m"] == 100
    assert "service" not in leading[0]["instruction"]


def test_llm_falls_back_without_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    text = "The Paiporta bridge on V-31 is under water"
    assert plan_with_llm(text) == plan(text)
    assert plan_with_llm(text).planner == "keyword"
    geocoded = geocode_report("CV-36 flooded near Paiporta")
    assert geocoded["edges"] == []
    assert geocoded["query"] == "CV-36"
    assert "Paiporta" in geocoded["landmark"]


def test_invalid_model_output_uses_the_keyword_planner():
    def fake(_messages, _tools):
        return {"content": "nope", "tool_calls": [{"name": "explode", "arguments": {"x": 1}}]}

    call = plan_with_llm("Road flooded at 19.1663, 72.8526 and not passable", complete=fake)
    assert call.planner == "keyword"
    assert call.tool == "close_nearest"
    assert "schema" in call.reasoning.lower()


def test_valid_model_tool_call_keeps_the_llm_reasoning():
    def fake(_messages, _tools):
        return {
            "content": "The report names CV-36, so that edge should close.",
            "tool_calls": [
                {
                    "name": "close_road",
                    "arguments": {
                        "road_id": "cv-36",
                        "road_name": "CV-36",
                        "reason": "CV-36 is the named road. Nearby names were not in the report.",
                    },
                }
            ],
        }

    call = plan_with_llm("CV-36 flooded near Paiporta", complete=fake)
    assert call.planner == "llm"
    assert call.tool == "close_road"
    assert call.args["road_id"] == "cv-36"
    assert "CV-36" in call.reasoning
    assert call.args["reason"]


def test_reroute_fanout_needs_a_graph_of_citizens():
    result = reroute_affected(_EmptyGraph(), "osm-1-2")
    assert result["citizens"] == []
    assert result["decisions"] == []
    assert result["road_id"] == "osm-1-2"


def test_graph_operations_dropped_the_sahil_marker():
    stubs = (ROOT / "floodline" / "stubs.py").read_text(encoding="utf-8")
    planner = (ROOT / "floodline" / "agents" / "llm_planner.py").read_text(encoding="utf-8")
    for name in (
        "def snap_point",
        "def geocode_report",
        "def reroute_affected",
        "def shelter_reachability",
        "def ensure_shelter_citizen_model",
        "def route_to_shelter",
    ):
        assert name in stubs
    assert "TODO(Sahil)" not in stubs
    assert "TODO(Sahil)" not in planner
    assert "def plan_with_llm" in planner


def test_decision_event_names_the_cch_query():
    event = decision_event(
        ("dec-1", 2, "2024-10-29T14:02:00Z", "route", "cch", "why", "320 s", 320, None, "Dispatcher")
    )
    assert event["agent"] == "Dispatcher"
    assert event["type"] == "decision"
    assert "db.idx.cch.query" in event["query"]
    assert "db.idx.cch.query" in query_label("cch", "route")
    assert event["delta_s"] is None
    assert event["duplicate_of"] is None


def test_decision_event_delta_uses_superseded_travel_time():
    assert travel_delta(132, 175, None) == 43
    assert travel_delta(132, 175, 43) == 43
    event = decision_event(
        (
            "dec-2",
            3,
            "t",
            "citizen_route",
            "cch",
            "Previous ETA 132 s. New ETA 175 s. Delta +43 s.",
            "Link Road (175s)",
            175,
            "aarey",
            "Dispatcher",
            "keyword",
            "closed",
            132,
            175,
            None,
            None,
        )
    )
    assert event["old_eta_s"] == 132
    assert event["new_eta_s"] == 175
    assert event["delta_s"] == 43
    titled = decision_event(
        (
            "dec-3",
            4,
            "t",
            "duplicate",
            "none",
            "already filed",
            "Already closed Aarey Road",
            None,
            None,
            "Scout",
            "keyword",
            "same road",
            None,
            None,
            None,
            "report-old",
        )
    )
    assert titled["title"] == "Duplicate of earlier report — no new work"
    assert titled["duplicate_of"] == "report-old"


class _EmptyGraph:
    def query(self, _text, _params=None):
        return _Result([])


class _Result:
    def __init__(self, rows):
        self.result_set = rows


class _Graph:
    def __init__(self):
        self.calls = 0

    def query(self, text, params=None):
        self.calls += 1
        since = (params or {}).get("since", 0)
        if "Task" in text:
            if since >= 5:
                return _Result([])
            return _Result([("task-1", 5, "reassess", "open", "cv-36", "CV-36", "t", None)])
        if since >= 4:
            return _Result([])
        return _Result(
            [("dec-1", 4, "t", "close", "", "closed CV-36", "closed", None, "cv-36", "Scout")]
        )


def test_collect_events_orders_tasks_before_later_decisions():
    events = collect_events(_Graph(), 0)
    kinds = [item["type"] for item in events]
    assert kinds == ["decision", "task"]
    assert events[1]["agent"] == "Dispatcher"
    assert events[1]["seq"] == 5


def test_convex_hull_closes_the_ring():
    ring = _convex_hull([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.2, 0.2)])
    assert ring[0] == ring[-1]
    assert len(ring) == 4


def test_closure_kind_names_the_report():
    assert closure_kind("Waterlogging on Shraddhanand Road") == "Waterlogging"
    assert closure_kind("The road is under water") == "Flooded road"
    assert closure_kind("") == "Closure"


def test_reopen_endpoint_writes_through_the_helper(monkeypatch):
    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "reopened": ["shraddhanand"], "decision_id": "dec-reopen"}

    monkeypatch.setattr("floodline.api.reopen_roads", fake)
    response = TestClient(app).post(
        "/api/road/reopen",
        json={"road_id": "shraddhanand", "graph": "fl-p1915-p07285"},
    )
    assert response.status_code == 200
    assert response.json()["decision_id"] == "dec-reopen"
    assert seen["road_id"] == "shraddhanand"
    assert seen["clear_test"] is False

    cleared = TestClient(app).post("/api/road/reopen", json={"clear_test": True, "graph": "valencia"})
    assert cleared.status_code == 200
    assert seen["clear_test"] is True
