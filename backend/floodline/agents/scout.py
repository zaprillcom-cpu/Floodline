"""Scout turns a citizen report into one graph action.

The planner chooses the tool. This module does not close a road unless that
tool is ``close_road`` or the keyword coordinate tool ``close_nearest``.
"""

from __future__ import annotations

from floodline.agents.llm_planner import plan_with_llm
from floodline.config import utc_now
from floodline.db import rows
from floodline.network import UNIT_CALLSIGN, UNIT_OSMID
from floodline.stubs import geocode_report, road_name_matches
from floodline.tools import (
    best_hospital,
    nearest_open_road,
    close_road,
    create_report,
    create_task,
    handoff,
    link_duplicate,
    link_report_closes,
    link_task_report,
    reopen_road,
    unit_state,
    write_close_decision,
    write_notice_decision,
    write_reopen_decision,
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


def _candidates(graph, text: str, lat, lon) -> list[dict]:
    geo = geocode_report(text, graph, lat=lat, lon=lon)
    return list(geo.get("edges") or [])


def _pick_edge(call, candidates: list[dict]) -> dict | None:
    """Use the road the planner named when it is one of the geocoded candidates."""
    wanted = str(call.args.get("road_id") or "").strip()
    by_id = {edge["road_id"]: edge for edge in candidates}
    if wanted and wanted in by_id:
        return by_id[wanted]
    name = str(call.args.get("road_name") or call.args.get("query") or "").strip()
    if name:
        named = [edge for edge in candidates if road_name_matches(edge.get("name") or "", name)]
        if named:
            return named[0]
    if call.planner == "keyword" and wanted:
        return {"road_id": wanted, "name": name or wanted}
    return None


def _road_status(graph, road_id: str) -> str:
    found = rows(
        graph.query(
            "MATCH ()-[r:ROAD {road_id: $road_id}]->() RETURN r.status LIMIT 1",
            {"road_id": road_id},
        )
    )
    if not found or not found[0][0]:
        return ""
    return str(found[0][0])


def _prior_report(graph, road_id: str) -> str | None:
    found = rows(
        graph.query(
            """
            MATCH (rep:Report)-[:CLOSES {road_id: $road_id}]->()
            RETURN rep.id
            ORDER BY rep.received_at DESC
            LIMIT 1
            """,
            {"road_id": road_id},
        )
    )
    return found[0][0] if found else None


def _decline(graph, text: str, call, at: str, *, kind: str) -> dict:
    status = "rejected" if kind == "reject" else "needs_location"
    report_id = create_report(graph, text, at, status=status)
    reason = call.reasoning or call.rationale or "No road was changed."
    summary = "Report rejected" if kind == "reject" else "Need a location"
    decision_id = write_notice_decision(
        graph,
        report_id=report_id,
        at=at,
        kind=kind,
        summary=summary,
        explanation=f"Scout did not change a road. Planner: {call.planner}. {reason}",
        planner=call.planner,
        reasoning=reason,
    )
    return {
        "action": kind,
        "tool": call.tool,
        "rationale": call.rationale,
        "planner": call.planner,
        "reasoning": reason,
        "report_id": report_id,
        "decision_id": decision_id,
        "task_id": "",
        "closed": None,
        "rejected": True,
        "duplicate_of": None,
        "message": reason,
        "explanation": reason,
    }


def _duplicate(graph, text: str, call, at: str, edge: dict, prior_id: str | None) -> dict:
    report_id = create_report(graph, text, at, status="duplicate")
    if prior_id:
        link_duplicate(graph, report_id, prior_id)
    reason = call.reasoning or call.rationale or "This road is already closed."
    name = edge.get("name") or edge["road_id"]
    decision_id = write_notice_decision(
        graph,
        report_id=report_id,
        at=at,
        kind="duplicate",
        summary=f"Already closed {name}",
        explanation=(
            f"Scout did not close {name} again. The report is linked to the earlier one. "
            f"Planner: {call.planner}. {reason}"
        ),
        planner=call.planner,
        reasoning=reason,
    )
    return {
        "action": "duplicate",
        "tool": call.tool,
        "rationale": call.rationale,
        "planner": call.planner,
        "reasoning": reason,
        "report_id": report_id,
        "decision_id": decision_id,
        "task_id": "",
        "closed": {"road_id": edge["road_id"], "name": name},
        "rejected": False,
        "duplicate_of": prior_id,
        "message": f"{name} is already closed.",
        "explanation": reason,
    }


def _tasks_for_closure(graph, *, at, road_id, road_name, report_id, decision_id, planner, reasoning, origin, hospital_osmid):
    scout_task = create_task(
        graph,
        kind="ingest_report",
        status="done",
        priority=1,
        created_at=at,
        road_id=road_id,
        road_name=road_name,
        from_osmid=origin,
        to_osmid=hospital_osmid,
        report_id=report_id,
        close_decision_id=decision_id,
        planner=planner,
        reasoning=reasoning,
    )
    reassess = create_task(
        graph,
        kind="reassess",
        status="open",
        priority=5,
        created_at=at,
        road_id=road_id,
        road_name=road_name,
        from_osmid=origin,
        to_osmid=hospital_osmid,
        report_id=report_id,
        close_decision_id=decision_id,
        planner=planner,
        reasoning=reasoning,
    )
    link_task_report(graph, scout_task, report_id)
    link_task_report(graph, reassess, report_id)
    handoff(graph, scout_task, reassess, "road closed; recompute the active route")
    return reassess


def _close_edge(graph, text, call, at, edge: dict) -> dict:
    road_id = edge["road_id"]
    if _road_status(graph, road_id) == "closed":
        return _duplicate(graph, text, call, at, edge, _prior_report(graph, road_id))
    report_id = create_report(graph, text, at, status="open")
    closed = close_road(graph, road_id, by="Scout", at=at)
    if edge.get("name"):
        closed["name"] = edge["name"]
    link_report_closes(graph, report_id, closed["road_id"])
    reason = call.reasoning or call.rationale or ""
    explanation = (
        f"Scout closed {closed['name']} after a citizen report. "
        f"Both directions now have travel_s = 1000000000 and capacity = 0. "
        f"The PASSABLE mirrors for this segment were deleted so connectivity "
        f"queries will not treat it as open. The ROAD edges stay in place, so a "
        f"CCH index only has to re-customize weights. "
        f"Planner: {call.planner}."
    )
    if reason:
        explanation += f" {reason}"
    if _valencia_story(graph):
        explanation += (
            " This is the reconstructed Valencia corridor exercise, "
            "not a claim about the real DANA timeline."
        )
    severity = str(call.args.get("severity") or "")
    decision_id = write_close_decision(
        graph,
        road_id=closed["road_id"],
        road_name=closed["name"],
        report_id=report_id,
        at=at,
        explanation=explanation,
        planner=call.planner,
        reasoning=reason,
        severity=severity,
    )
    unit = unit_state(graph)
    origin = unit["osmid"] if unit else UNIT_OSMID
    callsign = unit["callsign"] if unit else UNIT_CALLSIGN
    hospital = best_hospital(graph, origin)
    reassess = _tasks_for_closure(
        graph,
        at=at,
        road_id=closed["road_id"],
        road_name=closed["name"],
        report_id=report_id,
        decision_id=decision_id,
        planner=call.planner,
        reasoning=reason,
        origin=origin,
        hospital_osmid=hospital["osmid"],
    )
    return {
        "action": "close",
        "tool": call.tool,
        "rationale": call.rationale,
        "planner": call.planner,
        "reasoning": reason,
        "report_id": report_id,
        "decision_id": decision_id,
        "task_id": reassess,
        "closed": closed,
        "hospital": hospital,
        "origin_osmid": origin,
        "callsign": callsign,
        "rejected": False,
        "duplicate_of": None,
        "message": f"Closed {closed['name']}.",
        "explanation": explanation,
    }


def _reopen_edge(graph, text, call, at, edge: dict) -> dict:
    road_id = edge["road_id"]
    report_id = create_report(graph, text, at, status="open")
    reopen_road(graph, road_id)
    name = edge.get("name") or road_id
    reason = call.reasoning or call.rationale or ""
    explanation = (
        f"Scout reopened {name} after a citizen report. "
        f"travel_s was restored from base_travel_s and the PASSABLE mirrors were recreated. "
        f"Planner: {call.planner}."
    )
    if reason:
        explanation += f" {reason}"
    decision_id = write_reopen_decision(
        graph,
        road_id=road_id,
        road_name=name,
        at=at,
        explanation=explanation,
        planner=call.planner,
        reasoning=reason,
    )
    graph.query(
        """
        MATCH (d:Decision {id: $id}), (r:Report {id: $report})
        MERGE (d)-[:BASED_ON]->(r)
        """,
        {"id": decision_id, "report": report_id},
    )
    unit = unit_state(graph)
    origin = unit["osmid"] if unit else UNIT_OSMID
    hospital = best_hospital(graph, origin)
    reassess = create_task(
        graph,
        kind="reassess",
        status="open",
        priority=5,
        created_at=at,
        road_id=road_id,
        road_name=name,
        from_osmid=origin,
        to_osmid=hospital["osmid"],
        report_id=report_id,
        close_decision_id=decision_id,
        planner=call.planner,
        reasoning=reason,
    )
    link_task_report(graph, reassess, report_id)
    return {
        "action": "reopen",
        "tool": call.tool,
        "rationale": call.rationale,
        "planner": call.planner,
        "reasoning": reason,
        "report_id": report_id,
        "decision_id": decision_id,
        "task_id": reassess,
        "closed": None,
        "reopened": {"road_id": road_id, "name": name},
        "rejected": False,
        "duplicate_of": None,
        "message": f"Reopened {name}.",
        "explanation": explanation,
    }


def handle_report(
    graph,
    text: str,
    at: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    citizen_id: str | None = None,
) -> dict:
    at = at or utc_now()
    call = plan_with_llm(text, graph=graph, lat=lat, lon=lon)
    tool = call.tool
    if tool in {"reject"}:
        return _decline(graph, text, call, at, kind="reject")
    if tool in {"noop", "request_clarification"}:
        return _decline(graph, text, call, at, kind="request_clarification")
    if tool == "close_nearest" and "lat" in call.args and "lon" in call.args:
        chosen = nearest_open_road(graph, float(call.args["lat"]), float(call.args["lon"]))
        return _close_edge(graph, text, call, at, {"road_id": chosen["road_id"], "name": chosen.get("name")})
    candidates = _candidates(graph, text, lat, lon)
    edge = _pick_edge(call, candidates)
    if tool == "reopen_road":
        if edge is None:
            return _decline(graph, text, call, at, kind="request_clarification")
        return _reopen_edge(graph, text, call, at, edge)
    if tool == "close_road":
        if edge is None:
            return _decline(graph, text, call, at, kind="request_clarification")
        return _close_edge(graph, text, call, at, edge)
    return _decline(graph, text, call, at, kind="request_clarification")
