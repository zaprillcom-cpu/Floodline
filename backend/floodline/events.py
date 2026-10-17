"""Task, Decision, and Route events for the live agent feed.

Reads nodes the agents already write. Does not plan or reroute.
"""

from __future__ import annotations

import json

from floodline.db import rows
from floodline.navigation import dedupe_route_title

_DECISIONS = """
MATCH (d:Decision)
WHERE coalesce(d.seq, 0) > $since
OPTIONAL MATCH (a:Agent)-[:MADE]->(d)
RETURN d.id, d.seq, d.at, d.kind, d.algo, d.explanation, d.result_summary,
       d.travel_s, d.road_id, a.name, d.planner, d.reasoning
ORDER BY d.seq
"""

_TASKS = """
MATCH (t:Task)
WHERE coalesce(t.seq, 0) > $since
OPTIONAL MATCH (a:Agent)-[:CLAIMED]->(t)
RETURN t.id, t.seq, t.kind, t.status, t.road_id, t.road_name, t.created_at, a.name,
       t.planner, t.reasoning
ORDER BY t.seq
"""

_QUERY_BY_ALGO = {
    "cch": "db.idx.cch.query({ relTypes: ['ROAD'], weightProp: 'travel_s' })",
    "astar": "algo.AStar({ weightProp: 'travel_s', heuristicScale: 0.03 })",
    "sppaths": "algo.SPpaths({ relTypes: ['ROAD'], weightProp: 'travel_s', pathCount: 1 })",
    "algo.WCC": "algo.WCC on PASSABLE",
    "algo.maxFlow": "algo.maxFlow on ROAD.capacity",
}


def query_label(algo: str | None, kind: str | None) -> str:
    if algo and algo in _QUERY_BY_ALGO:
        return _QUERY_BY_ALGO[algo]
    if kind == "close":
        return "SET ROAD.travel_s = 1000000000, ROAD.capacity = 0, ROAD.status = 'closed'; DELETE PASSABLE"
    if kind == "reopen":
        return "SET ROAD.travel_s = ROAD.base_travel_s, ROAD.status = 'open'; MERGE PASSABLE"
    if kind == "route":
        return _QUERY_BY_ALGO["cch"]
    if kind in ("ingest_report", "reassess"):
        return "MATCH (t:Task {status:'open'}) SET t.status = 'claimed'  // HANDOFF"
    if kind == "assess":
        return "algo.WCC on PASSABLE; algo.maxFlow on ROAD.capacity"
    if kind == "citizen_route":
        return _QUERY_BY_ALGO["cch"]
    return ""


def _agent(name: str | None, kind: str | None) -> str:
    if name:
        return str(name)
    if kind in ("close", "reopen", "ingest_report", "reject", "request_clarification", "duplicate"):
        return "Scout"
    if kind in ("route", "reassess", "citizen_route"):
        return "Dispatcher"
    if kind == "assess":
        return "Analyst"
    return "Analyst"


def decision_event(row: tuple) -> dict:
    row = tuple(row)
    decision_id, seq, at, kind, algo, explanation, summary, travel_s, road_id, agent = row[:10]
    planner = row[10] if len(row) > 10 else ""
    reasoning = row[11] if len(row) > 11 else ""
    kind_s = str(kind or "")
    return {
        "type": "decision",
        "id": decision_id,
        "seq": int(seq or 0),
        "at": at,
        "agent": _agent(agent, kind_s),
        "kind": kind_s,
        "algo": algo or "",
        "title": dedupe_route_title(summary or kind_s or "Decision"),
        "body": explanation or "",
        "query": query_label(algo, kind_s),
        "travel_s": None if travel_s is None else int(travel_s),
        "road_id": road_id or None,
        "planner": planner or "",
        "reasoning": reasoning or "",
    }


def route_event(decision: dict) -> dict:
    return {
        **decision,
        "type": "route",
        "title": decision.get("title") or "Route",
    }


def task_event(row: tuple) -> dict:
    row = tuple(row)
    task_id, seq, kind, status, road_id, road_name, created_at, agent = row[:8]
    planner = row[8] if len(row) > 8 else ""
    reasoning = row[9] if len(row) > 9 else ""
    kind_s = str(kind or "")
    label = road_name or road_id or ""
    return {
        "type": "task",
        "id": task_id,
        "seq": int(seq or 0),
        "at": created_at,
        "agent": _agent(agent, kind_s),
        "kind": kind_s,
        "status": status or "",
        "algo": "",
        "title": f"{kind_s} · {status}",
        "body": label,
        "query": query_label(None, kind_s),
        "travel_s": None,
        "road_id": road_id or None,
        "planner": planner or "",
        "reasoning": reasoning or "",
    }


def collect_events(graph, since: int) -> list[dict]:
    """Events with seq greater than ``since``, oldest first."""
    events: list[dict] = []
    for row in rows(graph.query(_DECISIONS, {"since": int(since)})):
        decision = decision_event(row)
        events.append(decision)
        if decision["kind"] == "route":
            events.append(route_event(decision))
    for row in rows(graph.query(_TASKS, {"since": int(since)})):
        events.append(task_event(row))
    events.sort(key=lambda item: (item["seq"], 0 if item["type"] == "task" else 1 if item["type"] == "decision" else 2))
    return events


def format_sse(event: str, data: dict) -> str:
    payload = json.dumps(data, separators=(",", ":"), default=str)
    return f"event: {event}\ndata: {payload}\n\n"
