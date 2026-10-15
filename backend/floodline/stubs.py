"""Sahil-owned graph operations. Signatures and fallbacks only.

Do not fill these in from the AI-assist slice. Each function is marked
``TODO(Sahil)`` and falls back to the existing router or the keyword planner
so the citizen UI still draws a route and files a report.

Sahil owns:
- Shelter / Citizen model (``NEAR``, ``AT``, ``CLOSES``) and indexes
- ``route_to_shelter`` (snap → top-k open shelters → CCH)
- reroute fan-out onto Decision nodes
- snap and shelter-reachability WCC Cypher
- the LLM planner (``agents/llm_planner.py``)
- report geocoding onto ROAD edges
"""

from __future__ import annotations

from floodline.agents.llm_planner import plan_with_llm
from floodline.db import rows
from floodline.navigation import build_steps, path_hops
from floodline.routing import NoRoute, route
from floodline.tools import best_hospital, snap_to_road


def snap_point(graph, lat: float, lon: float) -> dict:
    """TODO(Sahil): snap a citizen point to the nearest Intersection.

    The query Sahil adds is a point-index nearest neighbour, not a full scan,
    and it should write ``(:Citizen)-[:AT]->(:Intersection)``.

    Fallback: ``tools.snap_to_road``, which already exists.
    """
    # TODO(Sahil)
    snapped = snap_to_road(graph, lat, lon)
    return {**snapped, "lat": lat, "lon": lon, "stub": "snap_point", "owner": "Sahil"}


def geocode_report(text: str) -> dict:
    """TODO(Sahil): street name or landmark → ROAD edges (name match + distance).

    Example that must work once this is real: "CV-36 flooded near Paiporta"
    closes the CV-36 edges, not a random nearby road.

    Fallback: the keyword planner. ``edges`` stays empty until Sahil fills this in.
    """
    # TODO(Sahil)
    call = plan_with_llm(text)
    return {
        "stub": "geocode_report",
        "owner": "Sahil",
        "narrator": "stub",
        "tool": call.tool,
        "args": call.args,
        "rationale": call.rationale,
        "edges": [],
    }


def reroute_affected(graph, road_id: str) -> dict:
    """TODO(Sahil): citizens whose active route uses this edge → new route → Decision.

    The Decision node should store ``reason``, the old and new ETA, and the
    closed edge id, and ``(:Decision)-[:REROUTED]->(:Citizen)``.

    Fallback: no citizen fan-out. ``file_report`` still reroutes the ambulance
    through the existing Dispatcher. The UI recomputes its own line with
    ``GET /api/route`` from the simulated position.
    """
    # TODO(Sahil)
    del graph  # the fallback does not query
    return {
        "stub": "reroute_affected",
        "owner": "Sahil",
        "road_id": road_id,
        "citizens": [],
        "decisions": [],
    }


def shelter_reachability(graph) -> dict:
    """TODO(Sahil): which areas cannot reach an open shelter, via WCC.

    Fallback: the existing district cut-off (``analysis.cut_off_areas``, WCC
    on PASSABLE toward hospitals). Flood vision shades those districts. This
    does not add a shelter-WCC query.
    """
    # TODO(Sahil)
    from floodline.analysis import cut_off_areas

    areas = cut_off_areas(graph)
    return {
        "stub": "shelter_reachability",
        "owner": "Sahil",
        "fallback": "cut_off_areas",
        "areas": areas,
    }


def ensure_shelter_citizen_model(graph) -> dict:
    """TODO(Sahil): create the v2 labels, relationships, and indexes.

    ``(:Shelter)-[:NEAR {dist_m}]->(:Intersection)``
    ``(:Citizen)-[:AT]->(:Intersection)``
    ``(:Report)-[:CLOSES]->`` the ROAD edges it closes
    ``(:Task)-[:FOR]->(:Report)``
    ``(:Decision)-[:REROUTED]->(:Citizen)``

    Fallback: read-only counts. Does not write the new model. Shelters that
    the current loader already attached with ``ACCESS`` stay as they are.
    """
    # TODO(Sahil)
    counts = {}
    for label in ("Shelter", "Citizen", "Report", "Intersection"):
        found = rows(graph.query(f"MATCH (n:{label}) RETURN count(n) AS n"))
        counts[label] = int(found[0][0]) if found else 0
    near = rows(graph.query("MATCH ()-[r:NEAR]->() RETURN count(r) AS n"))
    at = rows(graph.query("MATCH ()-[r:AT]->() RETURN count(r) AS n"))
    return {
        "stub": "ensure_shelter_citizen_model",
        "owner": "Sahil",
        "written": False,
        "counts": counts,
        "near": int(near[0][0]) if near else 0,
        "at": int(at[0][0]) if at else 0,
    }


def _shelter_rows(graph) -> list[dict]:
    found = rows(
        graph.query(
            """
            MATCH (s:Shelter)-[:ACCESS]->(i:Intersection)
            RETURN s.id, s.name, s.capacity, s.occupancy, s.lat, s.lon, i.osmid, i.lat, i.lon
            """
        )
    )
    shelters = []
    for sid, name, capacity, occupancy, slat, slon, osmid, ilat, ilon in found:
        shelters.append(
            {
                "kind": "shelter",
                "id": sid,
                "name": name,
                "osmid": int(osmid),
                "lat": float(slat if slat is not None else ilat),
                "lon": float(slon if slon is not None else ilon),
                "capacity": int(capacity or 0),
                "occupancy": int(occupancy or 0),
                "open": True,
                "metric": "people",
            }
        )
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


def route_to_shelter(graph, lat: float, lon: float) -> dict:
    """TODO(Sahil): snap → top-k open shelters by NEAR → CCH → lowest travel_s.

    ``GET /api/route`` should stay under 300 ms once this query is real.

    Fallback: snap with ``snap_point``, then ``routing.route`` (CCH, then A*,
    then SPpaths) to each existing Shelter that has an ACCESS edge. If no
    shelter is reachable, use ``best_hospital`` so the map still gets a line.
    """
    # TODO(Sahil): replace the body below with the NEAR + CCH shelter query.
    snapped = snap_point(graph, lat, lon)
    origin = int(snapped["osmid"])
    chosen = None
    found = None
    for shelter in _shelter_rows(graph):
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
            hospital = {
                **hospital,
                "lat": hlat,
                "lon": hlon,
                "beds_total": int(hospital.get("beds_free") or 0),
            }
        chosen = _as_destination(hospital, found.algo)

    hops = path_hops(graph, found.osmids)
    steps = build_steps(hops)
    return {
        "stub": "route_to_shelter",
        "owner": "Sahil",
        "fallback": "routing.route",
        "origin": snapped,
        "destination": chosen,
        "algo": found.algo,
        "eta_s": found.travel_s,
        "osmids": found.osmids,
        "names": found.names,
        "hops": hops,
        "steps": steps,
    }
