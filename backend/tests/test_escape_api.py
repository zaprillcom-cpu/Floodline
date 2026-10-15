"""Admin token, SSE formatting, and Sahil stubs. None of these need FalkorDB."""

from pathlib import Path

from fastapi.testclient import TestClient

from floodline.agents.llm_planner import plan_with_llm
from floodline.agents.planner import plan
from floodline.api import app
from floodline.escape import _convex_hull
from floodline.events import collect_events, decision_event, query_label
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
    assert steps[0]["instruction"] == "Continue 120 m"
    assert steps[0]["distance_m"] == 120
    assert steps[0]["name"] == ""


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


def test_llm_and_geocode_fall_back_to_the_keyword_planner():
    text = "The Paiporta bridge on V-31 is under water"
    assert plan_with_llm(text) == plan(text)
    geocoded = geocode_report("CV-36 flooded near Paiporta")
    assert geocoded["stub"] == "geocode_report"
    assert geocoded["owner"] == "Sahil"
    assert geocoded["edges"] == []
    assert geocoded["narrator"] == "stub"
    assert geocoded["tool"] == "noop"


def test_reroute_fanout_does_not_invent_citizens():
    result = reroute_affected(None, "osm-1-2")
    assert result["citizens"] == []
    assert result["decisions"] == []
    assert result["road_id"] == "osm-1-2"
    assert "TODO(Sahil)" in (reroute_affected.__doc__ or "")
    assert "TODO(Sahil)" in (route_to_shelter.__doc__ or "")
    assert "TODO(Sahil)" in (snap_point.__doc__ or "")


def test_stub_sources_keep_the_sahil_marker():
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
    assert stubs.count("TODO(Sahil)") >= 6
    assert "def plan_with_llm" in planner
    assert "TODO(Sahil)" in planner


def test_decision_event_names_the_cch_query():
    event = decision_event(
        ("dec-1", 2, "2024-10-29T14:02:00Z", "route", "cch", "why", "320 s", 320, None, "Dispatcher")
    )
    assert event["agent"] == "Dispatcher"
    assert event["type"] == "decision"
    assert "db.idx.cch.query" in event["query"]
    assert "db.idx.cch.query" in query_label("cch", "route")


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
