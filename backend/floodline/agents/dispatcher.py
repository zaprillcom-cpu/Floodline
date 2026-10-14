"""Dispatcher claims a reassess task and writes a new route decision."""

from __future__ import annotations

from floodline.config import T_CLOSE
from floodline.routing import route
from floodline.tools import (
    active_route_decision,
    claim_task,
    explain,
    finish_task,
    unit_state,
    write_route_decision,
)


def _fmt(seconds: int) -> str:
    minutes, remainder = divmod(int(seconds), 60)
    if minutes:
        return f"{minutes} min {remainder} s ({seconds} s)"
    return f"{remainder} s"


def _algo_label(algo: str) -> str:
    return {
        "cch": "db.idx.cch.query on the ROAD.travel_s CCH index",
        "astar": "algo.AStar (haversine heuristic, heuristicScale 0.03)",
        "sppaths": "algo.SPpaths (Dijkstra fallback)",
    }.get(algo, algo)


def reroute(graph, at: str = T_CLOSE) -> dict:
    task = claim_task(graph, "Dispatcher", "reassess", at)
    if task is None:
        return {"claimed": False}

    previous = active_route_decision(graph)
    found = route(graph, task["from_osmid"], task["to_osmid"])
    unit = unit_state(graph)
    callsign = unit["callsign"] if unit else "A-7"
    before_text = "no previous route"
    delta = ""
    if previous:
        before_text = (
            f"{_fmt(previous['travel_s'])} via {' → '.join(previous['names'])}"
        )
        delta = f" Delta {found.travel_s - previous['travel_s']:+d} s."
    explanation = (
        f"Unit {callsign} rerouted to the nearest open hospital because "
        f"{task['road_name']} closed at {at}. "
        f"Previous ETA {before_text}. "
        f"New ETA {_fmt(found.travel_s)} via {' → '.join(found.names)}."
        f"{delta} "
        f"Algorithm: {_algo_label(found.algo)}. "
        f"The closed segment stays in the graph with travel_s = 1e9; "
        f"paths at or above 1e8 seconds are discarded as unreachable."
    )
    evidence = [item for item in (task["close_decision_id"], task["report_id"]) if item]
    decision_id = write_route_decision(
        graph,
        found,
        at=at,
        explanation=explanation,
        previous_id=previous["id"] if previous else None,
        evidence_ids=evidence,
        callsign=callsign,
    )
    finish_task(graph, task["id"], "done")
    return {
        "claimed": True,
        "task_id": task["id"],
        "decision_id": decision_id,
        "route": found.as_dict(),
        "previous": previous,
        "explanation": explanation,
        "evidence": explain(graph, decision_id),
    }
