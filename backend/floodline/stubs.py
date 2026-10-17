"""Shelter, citizen, geocode, and reachability operations.

FalkorDB does the work: point-index snap, name-match geocoding, NEAR plus
CCH (A* then SPpaths), WCC on PASSABLE, and Decision edges for a reroute.
"""

from __future__ import annotations

import json
import re

from floodline import cypher
from floodline.config import utc_now
from floodline.db import rows
from floodline.navigation import build_steps, collapse_names, path_hops
from floodline.network import haversine_m
from floodline.routing import NoRoute, route
from floodline.tools import best_hospital, next_seq

ACTIVE_CITIZEN = "citizen"
TOP_K = 5

_CODE = re.compile(r"\b([A-Za-z]{1,6}-\d{1,4}[A-Za-z]?)\b")
_LANDMARK = re.compile(
    r"\b(?:near|at|by)\s+([A-Za-z][A-Za-z'’.\-]+(?:\s+[A-Za-z][A-Za-z'’.\-]+){0,2})"
)
_STOP = {
    "about",
    "ahead",
    "blocked",
    "bridge",
    "citizen",
    "closed",
    "collapse",
    "damage",
    "flood",
    "flooded",
    "from",
    "near",
    "over",
    "passable",
    "report",
    "road",
    "street",
    "that",
    "this",
    "under",
    "water",
    "waterlogging",
    "with",
}

_INDEXES = (
    ("shelter", cypher.CREATE_SHELTER_INDEX),
    ("citizen", cypher.CREATE_CITIZEN_INDEX),
    ("report", cypher.CREATE_REPORT_INDEX),
    ("osmid", cypher.CREATE_OSMID_INDEX),
    ("loc", cypher.CREATE_LOC_INDEX),
)


def _count(graph, query: str, params: dict | None = None) -> int:
    found = rows(graph.query(query, params or {}))
    if not found:
        return 0
    return int(found[0][0] or 0)


def _index(graph, query: str) -> bool:
    try:
        graph.query(query)
        return True
    except Exception as exc:
        message = str(exc).lower()
        return "already" in message or "equivalent" in message or "exist" in message


def _open_shelter(open_flag, capacity, occupancy) -> bool:
    if open_flag is False:
        return False
    if capacity is None or occupancy is None:
        return True
    return int(occupancy) < int(capacity)


def parse_report_place(text: str) -> dict:
    """Pull a road code and a landmark out of a free-text report."""
    codes = []
    for match in _CODE.finditer(text or ""):
        code = match.group(1)
        if code not in codes:
            codes.append(code)
    landmark = ""
    found = _LANDMARK.search(text or "")
    if found:
        phrase = found.group(1).strip(" .")
        if not _CODE.fullmatch(phrase):
            landmark = phrase
    words = []
    for raw in re.findall(r"[A-Za-z][A-Za-z'’.\-]{3,}", text or ""):
        token = raw.strip(" .").lower()
        if token in _STOP or token in {code.lower() for code in codes}:
            continue
        if landmark and token in landmark.lower():
            continue
        if token not in words:
            words.append(token)
    query = codes[0] if codes else (words[0] if words else landmark)
    return {"codes": codes, "words": words, "landmark": landmark, "query": query}


def _road_rows(graph, token: str) -> list[dict]:
    found = rows(graph.query(cypher.GEOCODE_ROADS, {"token": token}))
    edges = []
    for road_id, name, alat, alon, blat, blon, adistrict, bdistrict, aname, bname in found:
        edges.append(
            {
                "road_id": road_id,
                "name": name or road_id,
                "alat": float(alat or 0),
                "alon": float(alon or 0),
                "blat": float(blat or 0),
                "blon": float(blon or 0),
                "adistrict": adistrict or "",
                "bdistrict": bdistrict or "",
                "aname": aname or "",
                "bname": bname or "",
            }
        )
    return edges


def _rank_edges(edges: list[dict], parsed: dict, lat: float | None, lon: float | None) -> list[dict]:
    """One row per road_id. A road code outranks a landmark that merely sits in the name."""
    landmark = (parsed.get("landmark") or "").lower()
    codes = [code.lower() for code in parsed.get("codes") or []]
    grouped: dict[str, dict] = {}
    for edge in edges:
        name = (edge.get("name") or "").lower()
        if codes and not any(code in name for code in codes):
            continue
        score = 0
        if codes and any(code in name for code in codes):
            score += 100
        elif not codes:
            score += 40
        blob = " ".join(
            [
                edge.get("adistrict") or "",
                edge.get("bdistrict") or "",
                edge.get("aname") or "",
                edge.get("bname") or "",
                edge.get("name") or "",
            ]
        ).lower()
        if landmark and landmark in blob:
            score += 25
        mid_lat = (edge["alat"] + edge["blat"]) / 2
        mid_lon = (edge["alon"] + edge["blon"]) / 2
        dist_m = None
        if lat is not None and lon is not None:
            dist_m = haversine_m(lat, lon, mid_lat, mid_lon)
        current = grouped.get(edge["road_id"])
        if current is None or score > current["score"] or (
            score == current["score"] and dist_m is not None and (current["dist_m"] is None or dist_m < current["dist_m"])
        ):
            grouped[edge["road_id"]] = {
                "road_id": edge["road_id"],
                "name": edge["name"],
                "dist_m": dist_m,
                "score": score,
            }
    ranked = sorted(
        grouped.values(),
        key=lambda item: (-item["score"], item["dist_m"] if item["dist_m"] is not None else 10**12),
    )
    return ranked[:5]


def ensure_shelter_citizen_model(graph, candidates: list[dict] | None = None) -> dict:
    """Create Shelter and Citizen indexes and ``(:Shelter)-[:NEAR]->(:Intersection)``.

    Idempotent. Existing ACCESS shelters are mirrored onto NEAR so CCH shelter
    routing and max-flow see the same destinations. Candidates from an area
    load are stored as NEAR and ACCESS.
    """
    indexes = {name: _index(graph, query) for name, query in _INDEXES}
    if _count(graph, "MATCH ()-[r:NEAR]->() RETURN count(r) AS n") == 0:
        try:
            graph.query(cypher.BACKFILL_NEAR)
        except Exception:
            pass
    written = 0
    for item in candidates or []:
        osmid = item.get("osmid")
        if osmid is None:
            continue
        graph.query(
            """
            MATCH (i:Intersection {osmid: $osmid})
            MERGE (s:Shelter {id: $id})
            SET s.name = $name,
                s.capacity = coalesce(s.capacity, $capacity),
                s.occupancy = coalesce(s.occupancy, $occupancy),
                s.lat = $lat,
                s.lon = $lon,
                s.kind = $kind,
                s.open = coalesce(s.open, true)
            MERGE (s)-[n:NEAR]->(i)
            SET n.dist_m = $dist_m
            MERGE (s)-[a:ACCESS]->(i)
            SET a.dist_m = $dist_m
            """,
            {
                "osmid": int(osmid),
                "id": str(item.get("id") or f"shelter-{osmid}"),
                "name": str(item.get("name") or "Shelter")[:120],
                "capacity": int(item.get("capacity") or 200),
                "occupancy": int(item.get("occupancy") or 0),
                "lat": float(item.get("lat") or 0),
                "lon": float(item.get("lon") or 0),
                "kind": str(item.get("kind") or "shelter")[:40],
                "dist_m": int(item.get("dist_m") or 0),
            },
        )
        written += 1
    shelters = _count(graph, "MATCH (s:Shelter) RETURN count(s) AS n")
    citizens = _count(graph, "MATCH (c:Citizen) RETURN count(c) AS n")
    near = _count(graph, "MATCH ()-[r:NEAR]->() RETURN count(r) AS n")
    at = _count(graph, "MATCH ()-[r:AT]->() RETURN count(r) AS n")
    return {
        "written": True,
        "indexes": indexes,
        "shelters": shelters,
        "attached": written,
        "counts": {"Shelter": shelters, "Citizen": citizens},
        "near": near,
        "at": at,
        "candidates": len(candidates or []),
    }


def snap_point(graph, lat: float, lon: float, citizen_id: str | None = None) -> dict:
    """Nearest Intersection via the point index, then haversine on node coordinates.

    When ``citizen_id`` is set, ``(:Citizen)-[:AT]->(:Intersection)`` is written.
    """
    snapped = None
    try:
        found = rows(
            graph.query(cypher.SNAP_GEO, {"lat": lat, "lon": lon, "radius_m": 8000})
        )
        if found:
            osmid, name, dist_m = found[0]
            snapped = {
                "osmid": int(osmid),
                "name": name,
                "dist_m": int(round(float(dist_m))),
                "method": "point-index",
            }
    except Exception:
        snapped = None
    if snapped is None:
        best = None
        best_dist = 10**12
        for osmid, name, node_lat, node_lon in rows(
            graph.query("MATCH (i:Intersection) RETURN i.osmid, i.name, i.lat, i.lon")
        ):
            if node_lat is None or node_lon is None:
                continue
            dist = haversine_m(lat, lon, float(node_lat), float(node_lon))
            if dist < best_dist:
                best = (int(osmid), name, dist)
                best_dist = dist
        if best is None:
            raise LookupError(f"No intersection near ({lat}, {lon})")
        snapped = {
            "osmid": best[0],
            "name": best[1],
            "dist_m": int(best[2]),
            "method": "haversine",
        }
    node = rows(
        graph.query(
            "MATCH (i:Intersection {osmid: $osmid}) RETURN i.lat, i.lon",
            {"osmid": snapped["osmid"]},
        )
    )
    if node and node[0][0] is not None:
        snapped["lat"] = float(node[0][0])
        snapped["lon"] = float(node[0][1])
    else:
        snapped["lat"] = lat
        snapped["lon"] = lon
    snapped["query_lat"] = lat
    snapped["query_lon"] = lon
    if citizen_id:
        _attach_at(graph, citizen_id, snapped["osmid"], lat, lon)
    return snapped


def _attach_at(graph, citizen_id: str, osmid: int, lat: float, lon: float) -> None:
    graph.query(
        """
        MERGE (c:Citizen {id: $id})
        SET c.lat = $lat, c.lon = $lon, c.updated_at = $at, c.active = true
        WITH c
        MATCH (c)-[r]->()
        WHERE type(r) = 'AT'
        DELETE r
        """,
        {"id": citizen_id, "lat": lat, "lon": lon, "at": utc_now()},
    )
    graph.query(
        """
        MATCH (c:Citizen {id: $id}), (i:Intersection {osmid: $osmid})
        CREATE (c)-[:AT]->(i)
        """,
        {"id": citizen_id, "osmid": int(osmid)},
    )


def geocode_report(text: str, graph=None, lat: float | None = None, lon: float | None = None) -> dict:
    """Street name or landmark → ROAD edges, by name match then distance.

    "CV-36 flooded near Paiporta" returns the CV-36 edges. The landmark only
    breaks ties. Without a graph the parsed tokens are returned and ``edges``
    stays empty.
    """
    parsed = parse_report_place(text)
    result = {**parsed, "edges": [], "method": "name+distance"}
    if graph is None:
        return result
    tokens = list(parsed["codes"])
    if not tokens:
        tokens = list(parsed["words"])
    if not tokens and parsed["landmark"]:
        tokens = [parsed["landmark"]]
    edges: list[dict] = []
    seen = set()
    for token in tokens:
        for edge in _road_rows(graph, token):
            key = (edge["road_id"], edge["alat"], edge["blat"])
            if key in seen:
                continue
            seen.add(key)
            edges.append(edge)
    result["edges"] = _rank_edges(edges, parsed, lat, lon)
    return result


def _shelter_candidates(graph) -> list[dict]:
    found = rows(graph.query(cypher.TOP_SHELTERS_NEAR, {"k": 12}))
    shelters = []
    for sid, name, capacity, occupancy, slat, slon, kind, open_flag, osmid, ilat, ilon, dist_m in found:
        if not _open_shelter(open_flag, capacity, occupancy):
            continue
        shelters.append(
            {
                "kind": "shelter",
                "id": sid,
                "name": name,
                "osmid": int(osmid),
                "lat": float(slat if slat is not None else ilat or 0),
                "lon": float(slon if slon is not None else ilon or 0),
                "capacity": int(capacity or 0),
                "occupancy": int(occupancy or 0),
                "open": True,
                "metric": "people",
                "subtype": kind or "shelter",
                "dist_m": int(dist_m or 0),
            }
        )
        if len(shelters) >= TOP_K:
            break
    return shelters


def _as_destination(hospital: dict, found_algo: str) -> dict:
    beds_total = int(hospital.get("beds_total") or hospital.get("beds_free") or 0)
    beds_free = int(hospital.get("beds_free") or 0)
    return {
        "kind": "hospital",
        "id": hospital["id"],
        "name": hospital["name"],
        "osmid": int(hospital["osmid"]),
        "lat": hospital.get("lat"),
        "lon": hospital.get("lon"),
        "capacity": beds_total,
        "occupancy": max(0, beds_total - beds_free),
        "open": True,
        "metric": "beds",
        "travel_s": int(hospital["travel_s"]),
        "algo": found_algo or hospital.get("algo"),
    }


def _node_lat_lon(graph, osmid: int) -> tuple[float | None, float | None]:
    found = rows(
        graph.query(
            "MATCH (i:Intersection {osmid: $osmid}) RETURN i.lat, i.lon",
            {"osmid": int(osmid)},
        )
    )
    if not found:
        return None, None
    lat, lon = found[0]
    if lat is None or lon is None:
        return None, None
    return float(lat), float(lon)


def remember_route(
    graph,
    lat: float,
    lon: float,
    origin: dict,
    found,
    hops: list[dict],
    destination: dict,
    *,
    citizen_id: str = ACTIVE_CITIZEN,
    record_decision: bool = True,
) -> str:
    """Create or update the active Citizen, its AT edge, and ROUTED_TO hops."""
    at = utc_now()
    graph.query(
        """
        MERGE (c:Citizen {id: $id})
        SET c.lat = $lat,
            c.lon = $lon,
            c.updated_at = $at,
            c.eta_s = $eta,
            c.algo = $algo,
            c.active = true,
            c.shelter_id = $shelter,
            c.origin_osmid = $osmid,
            c.path_osmids = $osmids
        """,
        {
            "id": citizen_id,
            "lat": lat,
            "lon": lon,
            "at": at,
            "eta": int(found.travel_s),
            "algo": found.algo,
            "shelter": str(destination.get("id") or ""),
            "osmid": int(origin["osmid"]),
            "osmids": json.dumps([int(v) for v in found.osmids]),
        },
    )
    graph.query(
        """
        MATCH (c:Citizen {id: $id})-[r]->()
        WHERE type(r) IN ['AT', 'ROUTED_TO']
        DELETE r
        """,
        {"id": citizen_id},
    )
    graph.query(
        """
        MATCH (c:Citizen {id: $id}), (i:Intersection {osmid: $osmid})
        CREATE (c)-[:AT]->(i)
        """,
        {"id": citizen_id, "osmid": int(origin["osmid"])},
    )
    routed = []
    for seq, hop in enumerate(hops):
        road_id = hop.get("road_id") or ""
        osmid = found.osmids[seq + 1] if seq + 1 < len(found.osmids) else None
        if osmid is None or not road_id:
            continue
        routed.append({"seq": seq, "osmid": int(osmid), "road_id": str(road_id)})
    if routed:
        graph.query(
            """
            UNWIND $hops AS hop
            MATCH (c:Citizen {id: $id}), (i:Intersection {osmid: hop.osmid})
            CREATE (c)-[:ROUTED_TO {seq: hop.seq, road_id: hop.road_id}]->(i)
            """,
            {"id": citizen_id, "hops": routed},
        )
    if record_decision:
        existing = rows(
            graph.query(
                "MATCH (c:Citizen {id: $id}) RETURN c.decision_id",
                {"id": citizen_id},
            )
        )
        current = existing[0][0] if existing and existing[0][0] else None
        decision_id = _sync_citizen_decision(
            graph,
            citizen_id=citizen_id,
            found=found,
            destination=destination,
            at=at,
            existing_id=current,
        )
        graph.query(
            "MATCH (c:Citizen {id: $id}) SET c.decision_id = $decision",
            {"id": citizen_id, "decision": decision_id},
        )
    return citizen_id


def _sync_citizen_decision(graph, *, citizen_id, found, destination, at, existing_id) -> str:
    names = " → ".join(collapse_names(found.names)) or destination.get("name") or "route"
    summary = f"{names} ({found.travel_s}s)"
    explanation = (
        f"Citizen routed to {destination.get('name')} in {found.travel_s} s "
        f"via {names}. Algorithm: {found.algo}."
    )
    if existing_id:
        graph.query(
            """
            MATCH (d:Decision {id: $id})
            SET d.explanation = $explanation,
                d.result_summary = $summary,
                d.travel_s = $travel,
                d.algo = $algo,
                d.path_osmids = $osmids,
                d.path_names = $names
            """,
            {
                "id": existing_id,
                "explanation": explanation,
                "summary": summary,
                "travel": int(found.travel_s),
                "algo": found.algo,
                "osmids": json.dumps([int(v) for v in found.osmids]),
                "names": json.dumps(list(found.names)),
            },
        )
        return existing_id
    from floodline.tools import _id

    decision_id = _id("dec")
    seq = next_seq(graph)
    graph.query(
        """
        CREATE (:Decision {
          id: $id,
          seq: $seq,
          at: $at,
          kind: 'citizen_route',
          algo: $algo,
          explanation: $explanation,
          result_summary: $summary,
          superseded: false,
          travel_s: $travel,
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
            "summary": summary,
            "travel": int(found.travel_s),
            "osmids": json.dumps([int(v) for v in found.osmids]),
            "names": json.dumps(list(found.names)),
        },
    )
    graph.query(
        """
        MERGE (a:Agent {name: 'Dispatcher'})
        SET a.role = coalesce(a.role, 'routing'), a.heartbeat_at = $at
        WITH a
        MATCH (d:Decision {id: $id}), (c:Citizen {id: $citizen})
        MERGE (a)-[:MADE]->(d)
        MERGE (d)-[:AFFECTS]->(c)
        """,
        {"at": at, "id": decision_id, "citizen": citizen_id},
    )
    return decision_id


def route_to_shelter(
    graph,
    lat: float,
    lon: float,
    candidates: list[dict] | None = None,
    *,
    record_decision: bool = True,
    citizen_id: str = ACTIVE_CITIZEN,
) -> dict:
    """Snap, rank open shelters by NEAR, then CCH (A*, then SPpaths). Lowest travel_s wins.

    The active Citizen and its ROUTED_TO edges are created or updated in the graph.
    """
    ensure_shelter_citizen_model(graph, candidates)
    snapped = snap_point(graph, lat, lon)
    origin = int(snapped["osmid"])
    chosen = None
    found = None
    for shelter in _shelter_candidates(graph):
        if shelter["osmid"] == origin:
            continue
        try:
            candidate = route(graph, origin, shelter["osmid"])
        except (NoRoute, LookupError):
            continue
        except Exception:
            continue
        if chosen is None or candidate.travel_s < found.travel_s:
            chosen = {**shelter, "travel_s": candidate.travel_s, "algo": candidate.algo}
            found = candidate

    if found is None:
        hospital = best_hospital(graph, origin)
        found = route(graph, origin, int(hospital["osmid"]))
        hlat, hlon = _node_lat_lon(graph, int(hospital["osmid"]))
        extra = rows(
            graph.query(
                """
                MATCH (h:Hospital {id: $id})
                RETURN h.beds_total, h.beds_free, h.lat, h.lon
                """,
                {"id": hospital["id"]},
            )
        )
        if extra:
            beds_total, beds_free, blat, blon = extra[0]
            hospital = {
                **hospital,
                "beds_total": int(beds_total or 0),
                "beds_free": int(beds_free if beds_free is not None else hospital.get("beds_free") or 0),
                "lat": float(blat if blat is not None else hlat or 0),
                "lon": float(blon if blon is not None else hlon or 0),
            }
        else:
            hospital = {**hospital, "lat": hlat, "lon": hlon, "beds_total": int(hospital.get("beds_free") or 0)}
        chosen = _as_destination(hospital, found.algo)

    hops = path_hops(graph, found.osmids)
    steps = build_steps(hops)
    remember_route(
        graph,
        lat,
        lon,
        snapped,
        found,
        hops,
        chosen,
        citizen_id=citizen_id,
        record_decision=record_decision,
    )
    return {
        "origin": snapped,
        "destination": chosen,
        "algo": found.algo,
        "eta_s": found.travel_s,
        "osmids": found.osmids,
        "names": found.names,
        "hops": hops,
        "steps": steps,
        "citizen_id": citizen_id,
    }


def _report_for_road(graph, road_id: str) -> str | None:
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
    if found:
        return found[0][0]
    latest = rows(
        graph.query(
            """
            MATCH (rep:Report)
            RETURN rep.id
            ORDER BY rep.received_at DESC
            LIMIT 1
            """
        )
    )
    return latest[0][0] if latest else None


def reroute_affected(graph, road_id: str) -> dict:
    """Citizens whose active ROUTED_TO edge is this road get a new shelter route.

    The new Decision SUPERSEDES the previous citizen route, is BASED_ON the
    report that closed the road, and AFFECTS plus REROUTED the citizen.
    """
    affected = rows(graph.query(cypher.CITIZENS_ON_ROAD, {"road_id": road_id}))
    citizens = []
    decisions = []
    report_id = _report_for_road(graph, road_id)
    road_name = road_id
    named = rows(
        graph.query(
            "MATCH ()-[r:ROAD {road_id: $road_id}]->() RETURN r.name LIMIT 1",
            {"road_id": road_id},
        )
    )
    if named and named[0][0]:
        road_name = named[0][0]
    for citizen_id, lat, lon, eta_s, decision_id in affected:
        if lat is None or lon is None:
            continue
        old_eta = int(eta_s or 0)
        try:
            fresh = route_to_shelter(
                graph,
                float(lat),
                float(lon),
                record_decision=False,
                citizen_id=str(citizen_id),
            )
        except Exception as exc:
            citizens.append({"id": citizen_id, "error": str(exc), "old_eta_s": old_eta})
            continue
        new_eta = int(fresh["eta_s"])
        new_id = _write_reroute_decision(
            graph,
            citizen_id=str(citizen_id),
            road_id=road_id,
            road_name=road_name,
            old_eta=old_eta,
            new_eta=new_eta,
            found_algo=fresh["algo"],
            names=fresh["names"],
            osmids=fresh["osmids"],
            previous_id=decision_id,
            report_id=report_id,
            destination=fresh["destination"],
        )
        graph.query(
            "MATCH (c:Citizen {id: $id}) SET c.decision_id = $decision, c.eta_s = $eta",
            {"id": citizen_id, "decision": new_id, "eta": new_eta},
        )
        citizens.append(
            {
                "id": citizen_id,
                "old_eta_s": old_eta,
                "new_eta_s": new_eta,
                "decision_id": new_id,
                "shelter_id": fresh["destination"].get("id"),
            }
        )
        decisions.append(new_id)
    return {"road_id": road_id, "citizens": citizens, "decisions": decisions, "report_id": report_id}


def _write_reroute_decision(
    graph,
    *,
    citizen_id,
    road_id,
    road_name,
    old_eta,
    new_eta,
    found_algo,
    names,
    osmids,
    previous_id,
    report_id,
    destination,
) -> str:
    from floodline.tools import _id

    at = utc_now()
    decision_id = _id("dec")
    seq = next_seq(graph)
    via = " → ".join(collapse_names(names)) or destination.get("name") or "route"
    reason = f"Active route used closed edge {road_name}"
    explanation = (
        f"Citizen rerouted because {road_name} closed. {reason}. "
        f"Previous ETA {old_eta} s. New ETA {new_eta} s via {via}. "
        f"Algorithm: {found_algo}."
    )
    summary = f"{via} ({new_eta}s)"
    graph.query(
        """
        CREATE (:Decision {
          id: $id,
          seq: $seq,
          at: $at,
          kind: 'citizen_route',
          algo: $algo,
          explanation: $explanation,
          result_summary: $summary,
          superseded: false,
          travel_s: $travel,
          path_osmids: $osmids,
          path_names: $names,
          road_id: $road_id,
          reason: $reason,
          old_eta_s: $old_eta,
          new_eta_s: $new_eta
        })
        """,
        {
            "id": decision_id,
            "seq": seq,
            "at": at,
            "algo": found_algo,
            "explanation": explanation,
            "summary": summary,
            "travel": int(new_eta),
            "osmids": json.dumps([int(v) for v in osmids]),
            "names": json.dumps(list(names)),
            "road_id": road_id,
            "reason": reason,
            "old_eta": int(old_eta),
            "new_eta": int(new_eta),
        },
    )
    graph.query(
        """
        MERGE (a:Agent {name: 'Dispatcher'})
        SET a.heartbeat_at = $at
        WITH a
        MATCH (d:Decision {id: $id}), (c:Citizen {id: $citizen})
        MERGE (a)-[:MADE]->(d)
        MERGE (d)-[:AFFECTS]->(c)
        MERGE (d)-[:REROUTED]->(c)
        """,
        {"at": at, "id": decision_id, "citizen": citizen_id},
    )
    if previous_id:
        graph.query(
            """
            MATCH (d:Decision {id: $id}), (prev:Decision {id: $prev})
            MERGE (d)-[:SUPERSEDES]->(prev)
            SET prev.superseded = true
            """,
            {"id": decision_id, "prev": previous_id},
        )
    if report_id:
        graph.query(
            """
            MATCH (d:Decision {id: $id}), (rep:Report {id: $report})
            MERGE (d)-[:BASED_ON]->(rep)
            """,
            {"id": decision_id, "report": report_id},
        )
    return decision_id


def shelter_reachability(graph) -> dict:
    """Areas that cannot reach an open shelter, from ``algo.WCC`` on PASSABLE.

    A district is cut off when none of its intersections share a component with
    an open shelter. Mixed districts also return the isolated component so the
    map can shade that pocket.
    """
    components = {
        int(osmid): int(component_id)
        for osmid, component_id in rows(graph.query(cypher.WCC_PASSABLE))
        if osmid is not None
    }
    shelter_components = set()
    for osmid, open_flag, capacity, occupancy in rows(
        graph.query(
            """
            MATCH (s:Shelter)-[rel]->(i:Intersection)
            WHERE type(rel) IN ['ACCESS', 'NEAR']
            RETURN DISTINCT i.osmid, s.open, s.capacity, s.occupancy
            """
        )
    ):
        if osmid is None or int(osmid) not in components:
            continue
        if _open_shelter(open_flag, capacity, occupancy):
            shelter_components.add(components[int(osmid)])
    districts = rows(
        graph.query(
            """
            MATCH (d:District)
            OPTIONAL MATCH (i:Intersection)-[:IN_DISTRICT]->(d)
            RETURN d.name, d.population, collect(i.osmid)
            ORDER BY d.name
            """
        )
    )
    areas = []
    at = utc_now()
    if districts:
        for name, population, osmids in districts:
            node_ids = [int(osmid) for osmid in osmids or [] if osmid is not None]
            areas.extend(
                _areas_for_nodes(
                    graph,
                    name,
                    int(population or 0),
                    node_ids,
                    components,
                    shelter_components,
                    at,
                    district=True,
                )
            )
    else:
        grouped: dict[str, list[int]] = {}
        for osmid, district in rows(
            graph.query("MATCH (i:Intersection) RETURN i.osmid, coalesce(i.district, 'area')")
        ):
            grouped.setdefault(str(district or "area"), []).append(int(osmid))
        for name, node_ids in sorted(grouped.items()):
            areas.extend(
                _areas_for_nodes(
                    graph, name, 0, node_ids, components, shelter_components, at, district=False
                )
            )
    return {
        "areas": areas,
        "algo": "algo.WCC",
        "shelter_components": sorted(shelter_components),
    }


def _areas_for_nodes(graph, name, population, node_ids, components, shelter_components, at, *, district: bool) -> list[dict]:
    member_components = {components[osmid] for osmid in node_ids if osmid in components}
    reachable = bool(member_components & shelter_components)
    cut_off = bool(node_ids) and not reachable
    component_id = None
    if member_components:
        component_id = max(
            member_components,
            key=lambda cid: sum(1 for osmid in node_ids if components.get(osmid) == cid),
        )
    if district:
        graph.query(
            """
            MATCH (d:District {name: $name})
            SET d.cut_off = $cut_off,
                d.component_id = $component_id,
                d.cut_off_since = $since
            """,
            {
                "name": name,
                "cut_off": cut_off,
                "component_id": -1 if component_id is None else component_id,
                "since": at if cut_off else "",
            },
        )
    areas = [
        {
            "name": name,
            "population": population,
            "cut_off": cut_off,
            "component_id": component_id,
            "intersections": len(node_ids),
        }
    ]
    if cut_off or not member_components:
        return areas
    for cid in sorted(member_components):
        if cid in shelter_components:
            continue
        members = [osmid for osmid in node_ids if components.get(osmid) == cid]
        if not members:
            continue
        areas.append(
            {
                "name": f"{name} isolated",
                "population": 0,
                "cut_off": True,
                "component_id": cid,
                "intersections": len(members),
                "osmids": members,
            }
        )
    return areas
