"""Tool wrappers the agents call. The graph does the work; these functions only package it."""

from __future__ import annotations

import json
import math
import uuid

from floodline import cypher
from floodline.config import T_CLOSE
from floodline.db import rows
from floodline.network import PLACES, haversine_m
from floodline.routing import Route, route


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def next_seq(graph) -> int:
    result = graph.query("MERGE (m:Meta {key: 'clock'}) RETURN m.seq AS seq")
    current = rows(result)[0][0]
    seq = int(current or 0) + 1
    graph.query("MATCH (m:Meta {key: 'clock'}) SET m.seq = $seq", {"seq": seq})
    return seq


def snap_to_road(graph, lat: float, lon: float, radius_m: float = 8000) -> dict:
    """Nearest intersection. Uses the geo index when FalkorDB accepts point(), else haversine."""
    try:
        result = graph.query(
            cypher.SNAP_GEO,
            {"lat": lat, "lon": lon, "radius_m": radius_m},
        )
        found = rows(result)
        if found:
            osmid, name, dist_m = found[0]
            return {
                "osmid": int(osmid),
                "name": name,
                "dist_m": int(round(float(dist_m))),
                "method": "geo-index",
            }
    except Exception:
        pass

    scanned = _snap_scan(graph, lat, lon, radius_m)
    if scanned is not None:
        return scanned

    best = None
    best_dist = math.inf
    for place in PLACES:
        dist = haversine_m(lat, lon, place.lat, place.lon)
        if dist < best_dist:
            best = place
            best_dist = dist
    if best is None or best_dist > radius_m:
        raise LookupError(f"No intersection within {radius_m:.0f} m of ({lat}, {lon})")
    return {
        "osmid": best.osmid,
        "name": best.name,
        "dist_m": int(best_dist),
        "method": "haversine",
    }


def _snap_scan(graph, lat: float, lon: float, radius_m: float) -> dict | None:
    try:
        found = rows(graph.query("MATCH (i:Intersection) RETURN i.osmid, i.name, i.lat, i.lon"))
    except Exception:
        return None
    best = None
    best_dist = math.inf
    for osmid, name, node_lat, node_lon in found:
        if node_lat is None or node_lon is None:
            continue
        dist = haversine_m(lat, lon, float(node_lat), float(node_lon))
        if dist < best_dist:
            best = (int(osmid), name, dist)
            best_dist = dist
    if best is None or best_dist > radius_m:
        return None
    return {
        "osmid": best[0],
        "name": best[1],
        "dist_m": int(best_dist),
        "method": "haversine",
    }


def reopen_road(graph, road_id: str) -> None:
    """Undo a weight-only closure and put the PASSABLE mirrors back."""
    graph.query(
        """
        MATCH (a)-[r:ROAD {road_id: $road_id}]->(b)
        SET r.travel_s = r.base_travel_s,
            r.capacity = coalesce(r.base_capacity, r.capacity),
            r.status = 'open'
        REMOVE r.closed_at, r.closed_by
        WITH a, b, r
        MERGE (a)-[:PASSABLE {road_id: r.road_id}]->(b)
        """,
        {"road_id": road_id},
    )


def close_road(graph, road_id: str, by: str = "Scout", at: str = T_CLOSE) -> dict:
    """Weight-only closure, then drop the PASSABLE mirrors for that segment."""
    result = graph.query(cypher.CLOSE_ROAD, {"road_id": road_id, "at": at, "by": by})
    found = rows(result)
    closed_edges = int(found[0][0]) if found else 0
    name = found[0][1] if found else None
    if closed_edges == 0:
        raise LookupError(f"No ROAD edges with road_id {road_id}")
    graph.query(cypher.DELETE_PASSABLE, {"road_id": road_id})
    return {"road_id": road_id, "name": name, "closed_edges": closed_edges, "at": at}


def close_nearest_road(graph, lat: float, lon: float, by: str = "Scout", at: str = T_CLOSE) -> dict:
    """Close the open road whose midpoint is closest to a reported point."""
    snapped = snap_to_road(graph, lat, lon)
    result = graph.query(
        """
        MATCH (a:Intersection)-[r:ROAD {status: 'open'}]->(b:Intersection)
        RETURN r.road_id AS road_id, r.name AS name,
               a.lat AS alat, a.lon AS alon, b.lat AS blat, b.lon AS blon
        """
    )
    best_id = None
    best_name = None
    best_dist = math.inf
    seen = set()
    for road_id, name, alat, alon, blat, blon in rows(result):
        if road_id in seen:
            continue
        seen.add(road_id)
        mid_lat = (float(alat) + float(blat)) / 2
        mid_lon = (float(alon) + float(blon)) / 2
        dist = haversine_m(lat, lon, mid_lat, mid_lon)
        if dist < best_dist:
            best_dist = dist
            best_id = road_id
            best_name = name
    if best_id is None:
        raise LookupError("No open road to close")
    closed = close_road(graph, best_id, by=by, at=at)
    closed["snapped"] = snapped
    closed["midpoint_m"] = int(best_dist)
    closed["name"] = best_name
    return closed


def best_hospital(graph, origin_osmid: int) -> dict:
    """Route to every open ER and pick the smallest ETA. Tie-break: more free beds."""
    result = graph.query(
        """
        MATCH (h:Hospital)-[:ACCESS]->(i:Intersection)
        WHERE h.er_open = true AND h.beds_free > 0
        RETURN h.id AS id, h.name AS name, h.beds_free AS beds_free, i.osmid AS osmid
        """
    )
    candidates = []
    errors = []
    for hospital_id, name, beds_free, osmid in rows(result):
        try:
            found = route(graph, origin_osmid, int(osmid))
        except Exception as exc:
            errors.append(f"{hospital_id}: {exc}")
            continue
        candidates.append(
            {
                "id": hospital_id,
                "name": name,
                "beds_free": int(beds_free),
                "osmid": int(osmid),
                "travel_s": found.travel_s,
                "algo": found.algo,
            }
        )
    if not candidates:
        raise LookupError("No reachable hospital with free beds. " + "; ".join(errors))
    candidates.sort(key=lambda item: (item["travel_s"], -item["beds_free"]))
    return candidates[0]


def explain(graph, decision_id: str) -> dict:
    result = graph.query(cypher.EXPLAIN_DECISION, {"id": decision_id})
    found = rows(result)
    if not found:
        raise LookupError(f"No decision {decision_id}")
    decision_id, kind, explanation, algo, at, previous_id, agent, evidence_ids = found[0]
    if isinstance(evidence_ids, str):
        evidence_ids = json.loads(evidence_ids)
    return {
        "id": decision_id,
        "kind": kind,
        "explanation": explanation,
        "algo": algo,
        "at": at,
        "previous_id": previous_id,
        "agent": agent,
        "evidence_ids": list(evidence_ids or []),
        "narration": explanation,
        "narrator": "stub",
    }


def _link_used_edges(graph, decision_id: str, osmids: list[int]) -> None:
    for seq, osmid in enumerate(osmids):
        graph.query(
            """
            MATCH (d:Decision {id: $id}), (n:Intersection {osmid: $osmid})
            CREATE (d)-[:USED_EDGE {seq: $seq}]->(n)
            """,
            {"id": decision_id, "osmid": osmid, "seq": seq},
        )


def unit_state(graph) -> dict | None:
    """The ambulance the agents are routing. Read from the graph, not from a constant."""
    found = rows(
        graph.query(
            """
            MATCH (u:Unit)-[:ACCESS]->(i:Intersection)
            RETURN u.callsign AS callsign, u.status AS status, i.osmid AS osmid
            ORDER BY u.callsign
            LIMIT 1
            """
        )
    )
    if not found:
        return None
    callsign, status, osmid = found[0]
    return {"callsign": callsign, "status": status, "osmid": int(osmid)}


def write_route_decision(
    graph,
    found: Route,
    *,
    at: str,
    explanation: str,
    previous_id: str | None = None,
    evidence_ids: list[str] | None = None,
    agent: str = "Dispatcher",
    callsign: str = "A-7",
) -> str:
    decision_id = _id("dec")
    seq = next_seq(graph)
    graph.query(
        """
        CREATE (:Decision {
          id: $id,
          seq: $seq,
          at: $at,
          kind: 'route',
          algo: $algo,
          explanation: $explanation,
          result_summary: $summary,
          superseded: false,
          travel_s: $travel_s,
          path_osmids: $osmids,
          path_names: $names
        })
        """,
        {
            "id": decision_id,
            "seq": seq,
            "at": at,
            "algo": found.algo,
            "explanation": explanation,
            "summary": " → ".join(found.names) + f" ({found.travel_s}s)",
            "travel_s": found.travel_s,
            "osmids": json.dumps(found.osmids),
            "names": json.dumps(found.names),
        },
    )
    graph.query(
        """
        MATCH (a:Agent {name: $agent}), (d:Decision {id: $id})
        CREATE (a)-[:MADE]->(d)
        WITH d
        MATCH (u:Unit {callsign: $callsign})
        CREATE (d)-[:AFFECTS]->(u)
        """,
        {"agent": agent, "id": decision_id, "callsign": callsign},
    )
    _link_used_edges(graph, decision_id, found.osmids)
    if previous_id:
        graph.query(
            """
            MATCH (d:Decision {id: $id}), (prev:Decision {id: $prev})
            CREATE (d)-[:SUPERSEDES]->(prev)
            SET prev.superseded = true
            """,
            {"id": decision_id, "prev": previous_id},
        )
    for evidence_id in evidence_ids or []:
        graph.query(
            """
            MATCH (d:Decision {id: $id}), (e {id: $evidence})
            CREATE (d)-[:BASED_ON]->(e)
            """,
            {"id": decision_id, "evidence": evidence_id},
        )
    return decision_id


def write_close_decision(
    graph,
    *,
    road_id: str,
    road_name: str,
    report_id: str,
    at: str,
    explanation: str,
) -> str:
    decision_id = _id("dec")
    seq = next_seq(graph)
    graph.query(
        """
        CREATE (:Decision {
          id: $id,
          seq: $seq,
          at: $at,
          kind: 'close',
          algo: 'property-update',
          explanation: $explanation,
          result_summary: $summary,
          superseded: false,
          road_id: $road_id
        })
        """,
        {
            "id": decision_id,
            "seq": seq,
            "at": at,
            "explanation": explanation,
            "summary": f"Closed {road_name}",
            "road_id": road_id,
        },
    )
    graph.query(
        """
        MATCH (a:Agent {name: 'Scout'}), (d:Decision {id: $id}), (r:Report {id: $report})
        CREATE (a)-[:MADE]->(d)
        CREATE (d)-[:BASED_ON]->(r)
        """,
        {"id": decision_id, "report": report_id},
    )
    return decision_id


def create_report(graph, text: str, at: str, confidence: float = 0.9) -> str:
    report_id = _id("report")
    graph.query(
        """
        CREATE (:Report {
          id: $id,
          text: $text,
          received_at: $at,
          channel: 'citizen',
          confidence: $confidence
        })
        """,
        {"id": report_id, "text": text, "at": at, "confidence": confidence},
    )
    return report_id


def create_task(graph, **fields) -> str:
    task_id = _id("task")
    seq = next_seq(graph)
    graph.query(
        """
        CREATE (:Task {
          id: $id,
          seq: $seq,
          kind: $kind,
          status: $status,
          priority: $priority,
          created_at: $created_at,
          road_id: $road_id,
          road_name: $road_name,
          from_osmid: $from_osmid,
          to_osmid: $to_osmid,
          report_id: $report_id,
          close_decision_id: $close_decision_id
        })
        """,
        {
            "id": task_id,
            "seq": seq,
            "kind": fields["kind"],
            "status": fields.get("status", "open"),
            "priority": fields.get("priority", 1),
            "created_at": fields["created_at"],
            "road_id": fields.get("road_id", ""),
            "road_name": fields.get("road_name", ""),
            "from_osmid": fields.get("from_osmid", 0),
            "to_osmid": fields.get("to_osmid", 0),
            "report_id": fields.get("report_id", ""),
            "close_decision_id": fields.get("close_decision_id", ""),
        },
    )
    return task_id


def handoff(graph, source_task: str, target_task: str, reason: str) -> None:
    graph.query(
        """
        MATCH (a:Task {id: $source}), (b:Task {id: $target})
        CREATE (a)-[:HANDOFF {reason: $reason}]->(b)
        """,
        {"source": source_task, "target": target_task, "reason": reason},
    )


def claim_task(graph, agent: str, kind: str, now: str) -> dict | None:
    result = graph.query(cypher.CLAIM_TASK, {"agent": agent, "kind": kind, "now": now})
    found = rows(result)
    if not found:
        return None
    task_id, road_id, road_name, from_osmid, to_osmid, report_id, close_decision_id = found[0]
    return {
        "id": task_id,
        "road_id": road_id,
        "road_name": road_name,
        "from_osmid": int(from_osmid),
        "to_osmid": int(to_osmid),
        "report_id": report_id,
        "close_decision_id": close_decision_id,
    }


def finish_task(graph, task_id: str, status: str = "done") -> None:
    graph.query(
        "MATCH (t:Task {id: $id}) SET t.status = $status",
        {"id": task_id, "status": status},
    )


def active_route_decision(graph) -> dict | None:
    result = graph.query(
        """
        MATCH (d:Decision {kind: 'route', superseded: false})
        RETURN d.id AS id, d.travel_s AS travel_s, d.path_osmids AS osmids,
               d.path_names AS names, d.algo AS algo, d.at AS at, d.explanation AS explanation
        ORDER BY d.seq DESC
        LIMIT 1
        """
    )
    found = rows(result)
    if not found:
        return None
    decision_id, travel_s, osmids, names, algo, at, explanation = found[0]
    if isinstance(osmids, str):
        osmids = json.loads(osmids)
    if isinstance(names, str):
        names = json.loads(names)
    return {
        "id": decision_id,
        "travel_s": int(travel_s),
        "osmids": [int(v) for v in osmids],
        "names": list(names),
        "algo": algo,
        "at": at,
        "explanation": explanation,
    }
