"""Scout turns a citizen report into a closure and a handoff task."""

from __future__ import annotations

from floodline.agents.planner import plan
from floodline.config import T_CLOSE
from floodline.network import UNIT_CALLSIGN, UNIT_OSMID
from floodline.tools import (
    best_hospital,
    close_nearest_road,
    close_road,
    create_report,
    create_task,
    handoff,
    unit_state,
    write_close_decision,
)


def handle_report(graph, text: str, at: str = T_CLOSE) -> dict:
    call = plan(text)
    if call.tool == "noop":
        raise ValueError(call.rationale)

    report_id = create_report(graph, text, at)
    if call.tool == "close_road":
        closed = close_road(graph, call.args["road_id"], by="Scout", at=at)
    elif call.tool == "close_nearest":
        closed = close_nearest_road(
            graph, call.args["lat"], call.args["lon"], by="Scout", at=at
        )
    else:
        raise ValueError(f"Scout cannot run tool {call.tool}")

    explanation = (
        f"Scout closed {closed['name']} after a citizen report. "
        f"Both directions now have travel_s = 1000000000 and capacity = 0. "
        f"The PASSABLE mirrors for this segment were deleted so connectivity "
        f"queries will not treat it as open. The ROAD edges stay in place, so a "
        f"CCH index only has to re-customize weights. "
        f"Clock {at} is reconstructed, not a claim about the real DANA timeline."
    )
    decision_id = write_close_decision(
        graph,
        road_id=closed["road_id"],
        road_name=closed["name"],
        report_id=report_id,
        at=at,
        explanation=explanation,
    )
    unit = unit_state(graph)
    origin = unit["osmid"] if unit else UNIT_OSMID
    callsign = unit["callsign"] if unit else UNIT_CALLSIGN
    hospital = best_hospital(graph, origin)
    scout_task = create_task(
        graph,
        kind="ingest_report",
        status="done",
        priority=1,
        created_at=at,
        road_id=closed["road_id"],
        road_name=closed["name"],
        from_osmid=origin,
        to_osmid=hospital["osmid"],
        report_id=report_id,
        close_decision_id=decision_id,
    )
    reassess = create_task(
        graph,
        kind="reassess",
        status="open",
        priority=5,
        created_at=at,
        road_id=closed["road_id"],
        road_name=closed["name"],
        from_osmid=origin,
        to_osmid=hospital["osmid"],
        report_id=report_id,
        close_decision_id=decision_id,
    )
    handoff(graph, scout_task, reassess, "road closed; recompute the active route")
    return {
        "tool": call.tool,
        "rationale": call.rationale,
        "report_id": report_id,
        "decision_id": decision_id,
        "task_id": reassess,
        "closed": closed,
        "hospital": hospital,
        "origin_osmid": origin,
        "callsign": callsign,
        "explanation": explanation,
    }
