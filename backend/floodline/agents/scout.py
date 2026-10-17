"""Scout turns a citizen report into a closure and a handoff task."""

from __future__ import annotations

from floodline.agents.llm_planner import plan_with_llm
from floodline.config import utc_now
from floodline.db import rows
from floodline.network import UNIT_CALLSIGN, UNIT_OSMID
from floodline.stubs import geocode_report
from floodline.tools import (
    best_hospital,
    close_nearest_road,
    close_road,
    create_report,
    create_task,
    handoff,
    link_report_closes,
    link_task_report,
    unit_state,
    write_close_decision,
)


def _valencia_story(graph) -> bool:
    """The scripted corridor mentions DANA. A loaded city tile does not."""
    found = rows(
        graph.query(
            """
            OPTIONAL MATCH (m:Meta {key: 'network'})
            WITH coalesce(m.source, '') AS source
            OPTIONAL MATCH (d:District)
            RETURN source, collect(coalesce(d.name, '')) AS names
            """
        )
    )
    if not found:
        return False
    source, names = found[0]
    if str(source or "") == "synthetic":
        return True
    return any("paiporta" in str(name).lower() for name in (names or []))


def _close_from_plan(graph, text: str, call, at: str) -> dict:
    if call.tool == "close_nearest" and "lat" in call.args and "lon" in call.args:
        return close_nearest_road(graph, call.args["lat"], call.args["lon"], by="Scout", at=at)
    if call.tool == "close_road" and call.args.get("road_id"):
        try:
            return close_road(graph, call.args["road_id"], by="Scout", at=at)
        except LookupError:
            pass
    query = call.args.get("query") or call.args.get("road_name") or text
    geo = geocode_report(query, graph, lat=call.args.get("lat"), lon=call.args.get("lon"))
    if not geo["edges"] and query != text:
        geo = geocode_report(text, graph, lat=call.args.get("lat"), lon=call.args.get("lon"))
    if geo["edges"] and (call.tool != "noop" or _mentions_closure(text)):
        return close_road(graph, geo["edges"][0]["road_id"], by="Scout", at=at)
    if call.tool == "noop":
        raise ValueError(call.rationale)
    raise ValueError(call.rationale or "No road matched the report")


def _mentions_closure(text: str) -> bool:
    lowered = (text or "").lower()
    hints = (
        "closed",
        "flood",
        "water",
        "impassable",
        "not passable",
        "blocked",
        "landslide",
        "earthquake",
        "fire",
        "collapse",
        "tree",
    )
    return any(hint in lowered for hint in hints)


def handle_report(graph, text: str, at: str | None = None) -> dict:
    at = at or utc_now()
    call = plan_with_llm(text, graph=graph)
    if call.tool == "noop" and not _mentions_closure(text):
        raise ValueError(call.rationale)

    report_id = create_report(graph, text, at)
    severity = str(call.args.get("severity") or call.args.get("level") or "")
    if severity:
        graph.query(
            "MATCH (r:Report {id: $id}) SET r.severity = $severity",
            {"id": report_id, "severity": severity},
        )
    closed = _close_from_plan(graph, text, call, at)
    link_report_closes(graph, report_id, closed["road_id"])
    explanation = (
        f"Scout closed {closed['name']} after a citizen report. "
        f"Both directions now have travel_s = 1000000000 and capacity = 0. "
        f"The PASSABLE mirrors for this segment were deleted so connectivity "
        f"queries will not treat it as open. The ROAD edges stay in place, so a "
        f"CCH index only has to re-customize weights. "
        f"Planner: {call.planner}."
    )
    if call.reasoning:
        explanation += f" {call.reasoning}"
    if _valencia_story(graph):
        explanation += (
            " This is the reconstructed Valencia corridor exercise, "
            "not a claim about the real DANA timeline."
        )
    decision_id = write_close_decision(
        graph,
        road_id=closed["road_id"],
        road_name=closed["name"],
        report_id=report_id,
        at=at,
        explanation=explanation,
        planner=call.planner,
        reasoning=call.reasoning,
        severity=severity,
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
        planner=call.planner,
        reasoning=call.reasoning,
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
        planner=call.planner,
        reasoning=call.reasoning,
    )
    link_task_report(graph, scout_task, report_id)
    link_task_report(graph, reassess, report_id)
    handoff(graph, scout_task, reassess, "road closed; recompute the active route")
    return {
        "tool": call.tool,
        "rationale": call.rationale,
        "planner": call.planner,
        "reasoning": call.reasoning,
        "report_id": report_id,
        "decision_id": decision_id,
        "task_id": reassess,
        "closed": closed,
        "hospital": hospital,
        "origin_osmid": origin,
        "callsign": callsign,
        "explanation": explanation,
    }
