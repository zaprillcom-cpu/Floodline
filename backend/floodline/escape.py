"""Citizen-facing payloads: a route line, closures, and cut-off shades.

Routing stays in FalkorDB through the existing CCH → A* → SPpaths chain.
Shelter selection itself is the Sahil stub in ``stubs.route_to_shelter``.
"""

from __future__ import annotations

from floodline.config import Settings
from floodline.db import get_graph, rows
from floodline.navigation import build_steps, line_coordinates, path_hops
from floodline.network import haversine_m
from floodline.osm_network import bbox_dict, parse_bbox
from floodline.routing import route
from floodline.stubs import reroute_affected, route_to_shelter, shelter_reachability, snap_point


def _loaded(graph) -> bool:
    found = rows(graph.query("MATCH (i:Intersection) RETURN count(i) AS n"))
    return bool(found and int(found[0][0]) > 0)


def _place_rows(graph) -> list[dict]:
    found = []
    for sid, name, kind, capacity, occupancy, slat, slon, osmid in rows(
        graph.query(
            """
            MATCH (s:Shelter)-[:ACCESS]->(i:Intersection)
            RETURN s.id, s.name, s.kind, s.capacity, s.occupancy, s.lat, s.lon, i.osmid
            """
        )
    ):
        found.append(
            {
                "id": sid,
                "name": name,
                "role": "shelter",
                "kind": kind or "shelter",
                "osmid": int(osmid),
                "lat": float(slat or 0),
                "lon": float(slon or 0),
                "capacity": int(capacity or 0),
                "occupancy": int(occupancy or 0),
            }
        )
    for hid, name, beds_free, beds_total, hlat, hlon, osmid in rows(
        graph.query(
            """
            MATCH (h:Hospital)-[:ACCESS]->(i:Intersection)
            RETURN h.id, h.name, h.beds_free, h.beds_total, h.lat, h.lon, i.osmid
            """
        )
    ):
        total = int(beds_total or 0)
        free = int(beds_free or 0)
        found.append(
            {
                "id": hid,
                "name": name,
                "role": "hospital",
                "kind": "hospital",
                "osmid": int(osmid),
                "lat": float(hlat or 0),
                "lon": float(hlon or 0),
                "capacity": total,
                "occupancy": max(0, total - free),
            }
        )
    return found


def _as_choice(place: dict, lat: float, lon: float) -> dict:
    return {
        "id": place["id"],
        "name": place["name"],
        "role": place["role"],
        "kind": place["kind"] or place["role"],
        "lat": place["lat"],
        "lon": place["lon"],
        "osmid": place["osmid"],
        "distance_m": int(haversine_m(lat, lon, place["lat"], place["lon"])),
        "capacity": place["capacity"],
        "occupancy": place["occupancy"],
    }


def _route_to_place(graph, lat: float, lon: float, place: dict) -> dict:
    snapped = snap_point(graph, lat, lon)
    found = route(graph, int(snapped["osmid"]), int(place["osmid"]))
    hops = path_hops(graph, found.osmids)
    return {
        "stub": "route_to_shelter",
        "owner": "Sahil",
        "fallback": "chosen",
        "origin": snapped,
        "destination": {
            "kind": place["role"],
            "subtype": place["kind"],
            "id": place["id"],
            "name": place["name"],
            "osmid": place["osmid"],
            "lat": place["lat"],
            "lon": place["lon"],
            "capacity": place["capacity"],
            "occupancy": place["occupancy"],
            "open": True,
            "metric": "beds" if place["role"] == "hospital" else "people",
        },
        "algo": found.algo,
        "eta_s": found.travel_s,
        "hops": hops,
        "steps": build_steps(hops),
    }


def route_payload(
    lat: float,
    lon: float,
    settings: Settings | None = None,
    dest_id: str | None = None,
) -> dict:
    graph = get_graph(settings)
    if not _loaded(graph):
        raise LookupError("Graph is not loaded")
    places = _place_rows(graph)
    chosen_place = next((place for place in places if str(place["id"]) == str(dest_id)), None) if dest_id else None
    if dest_id and chosen_place is None:
        raise LookupError("That destination is not on this map")
    if chosen_place is not None:
        raw = _route_to_place(graph, lat, lon, chosen_place)
    else:
        raw = route_to_shelter(graph, lat, lon)
        subtype = ""
        for place in places:
            if str(place["id"]) == str(raw["destination"].get("id")):
                subtype = place["kind"] or ""
                break
        raw["destination"] = {**raw["destination"], "subtype": subtype or raw["destination"].get("kind")}
    coordinates = line_coordinates(raw["hops"])
    if len(coordinates) < 2:
        origin = raw["origin"]
        dest = raw["destination"]
        if origin.get("lon") is not None and dest.get("lon") is not None:
            coordinates = [
                [round(float(origin["lon"]), 7), round(float(origin["lat"]), 7)],
                [round(float(dest["lon"]), 7), round(float(dest["lat"]), 7)],
            ]
    here = [round(float(lon), 7), round(float(lat), 7)]
    if coordinates and (abs(coordinates[0][0] - here[0]) > 1e-5 or abs(coordinates[0][1] - here[1]) > 1e-5):
        coordinates = [here, *coordinates]
    steps = list(raw["steps"])
    lead = int(raw["origin"].get("dist_m") or 0)
    if lead > 25 and steps:
        for step in steps:
            step["from_m"] = int(step["from_m"]) + lead
        steps.insert(
            0,
            {
                "instruction": "Start from your position",
                "name": "Your position",
                "distance_m": lead,
                "from_m": 0,
                "bearing": steps[0]["bearing"] if steps else 0,
                "road_ids": [],
                "arrive": False,
            },
        )
    travelled = steps[-1]["from_m"] + steps[-1]["distance_m"] if steps else 0
    dest_name = raw["destination"]["name"]
    steps.append(
        {
            "instruction": f"Arrive at {dest_name}",
            "name": dest_name,
            "distance_m": 0,
            "from_m": travelled,
            "bearing": steps[-1]["bearing"] if steps else 0,
            "road_ids": [],
            "arrive": True,
        }
    )
    distance_m = sum(int(step["distance_m"]) for step in steps)
    alternatives = sorted((_as_choice(place, lat, lon) for place in places), key=lambda item: item["distance_m"])[:5]
    return {
        "ok": True,
        "stub": raw["stub"],
        "owner": raw["owner"],
        "fallback": raw["fallback"],
        "origin": raw["origin"],
        "destination": raw["destination"],
        "algo": raw["algo"],
        "eta_s": raw["eta_s"],
        "distance_m": distance_m,
        "alternatives": alternatives,
        "geojson": {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coordinates},
            "properties": {"algo": raw["algo"], "eta_s": raw["eta_s"]},
        },
        "steps": steps,
    }


def closure_kind(text: str) -> str:
    """Label a citizen report for the closed-road popup."""
    lowered = (text or "").lower()
    for label, hints in (
        ("Waterlogging", ("waterlogging",)),
        ("Earthquake damage", ("earthquake",)),
        ("Landslide", ("landslide",)),
        ("Fallen tree", ("fallen tree", "tree")),
        ("Building collapse", ("collapse",)),
        ("Power line down", ("power line",)),
        ("Fire", ("fire",)),
        ("Flooded road", ("flood", "under water", "water")),
        ("Road blocked", ("blocked", "closed")),
    ):
        if any(hint in lowered for hint in hints):
            return label
    return "Closure"


def closed_roads(graph) -> list[dict]:
    found = rows(
        graph.query(
            """
            MATCH (a:Intersection)-[r:ROAD {status: 'closed'}]->(b:Intersection)
            OPTIONAL MATCH (d:Decision {kind: 'close', road_id: r.road_id})
            OPTIONAL MATCH (agent:Agent)-[:MADE]->(d)
            OPTIONAL MATCH (d)-[:BASED_ON]->(rep:Report)
            RETURN r.road_id, r.name, r.closed_at, r.closed_by,
                   a.lon, a.lat, b.lon, b.lat,
                   d.id, d.seq, d.at, d.superseded, agent.name, rep.text
            """
        )
    )
    grouped: dict[str, dict] = {}
    for (
        road_id,
        name,
        closed_at,
        closed_by,
        alon,
        alat,
        blon,
        blat,
        decision_id,
        seq,
        at,
        superseded,
        agent,
        report,
    ) in found:
        if superseded:
            decision_id = None
        item = grouped.get(road_id)
        if item is None:
            item = {
                "road_id": road_id,
                "name": name or road_id,
                "coordinates": [
                    [round(float(alon), 7), round(float(alat), 7)],
                    [round(float(blon), 7), round(float(blat), 7)],
                ],
                "closed_at": closed_at or "",
                "closed_by": closed_by or "",
                "decision_id": "",
                "at": closed_at or "",
                "agent": closed_by or "",
                "report": "",
                "closure_type": "Closure",
                "_seq": -1,
            }
            grouped[road_id] = item
        decision_seq = int(seq or 0)
        if decision_id and decision_seq >= int(item["_seq"]):
            text = report or ""
            item["_seq"] = decision_seq
            item["decision_id"] = decision_id
            item["at"] = at or item["at"]
            item["agent"] = agent or item["agent"] or "Scout"
            item["report"] = text
            item["closure_type"] = closure_kind(text)
    for item in grouped.values():
        item.pop("_seq", None)
        if not item["closure_type"] or item["closure_type"] == "Closure":
            item["closure_type"] = closure_kind(item.get("report") or "")
        if not item["agent"]:
            item["agent"] = item.get("closed_by") or "Scout"
    return list(grouped.values())


def _pins(graph) -> list[dict]:
    pins = []
    for sid, name, capacity, occupancy, slat, slon, osmid, ilat, ilon in rows(
        graph.query(
            """
            MATCH (s:Shelter)-[:ACCESS]->(i:Intersection)
            RETURN s.id, s.name, s.capacity, s.occupancy, s.lat, s.lon, i.osmid, i.lat, i.lon
            """
        )
    ):
        pins.append(
            {
                "kind": "shelter",
                "id": sid,
                "name": name,
                "osmid": int(osmid),
                "lat": float(slat if slat is not None else ilat),
                "lon": float(slon if slon is not None else ilon),
                "capacity": int(capacity or 0),
                "occupancy": int(occupancy or 0),
            }
        )
    for hid, name, beds_free, beds_total, osmid, lat, lon in rows(
        graph.query(
            """
            MATCH (h:Hospital)-[:ACCESS]->(i:Intersection)
            RETURN h.id, h.name, h.beds_free, h.beds_total, i.osmid, i.lat, i.lon
            """
        )
    ):
        pins.append(
            {
                "kind": "hospital",
                "id": hid,
                "name": name,
                "osmid": int(osmid),
                "lat": float(lat),
                "lon": float(lon),
                "capacity": int(beds_total or 0),
                "occupancy": max(0, int(beds_total or 0) - int(beds_free or 0)),
            }
        )
    return pins


def _convex_hull(points: list[tuple[float, float]]) -> list[list[float]]:
    """Monotone chain. Points are (lon, lat). Returns a closed ring, or []."""
    unique = sorted(set(points))
    if len(unique) < 3:
        return []

    def cross(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    ring = lower[:-1] + upper[:-1]
    if len(ring) < 3:
        return []
    closed = [[round(lon, 7), round(lat, 7)] for lon, lat in ring]
    closed.append(closed[0])
    return closed


def cutoff_zones(graph) -> dict:
    """Shades for flood vision. The WCC query itself is Sahil's stub."""
    reach = shelter_reachability(graph)
    zones = []
    for area in reach["areas"]:
        if not area.get("cut_off"):
            continue
        found = rows(
            graph.query(
                """
                MATCH (i:Intersection {district: $name})
                RETURN i.lon, i.lat
                """,
                {"name": area["name"]},
            )
        )
        points = [(float(lon), float(lat)) for lon, lat in found if lon is not None and lat is not None]
        ring = _convex_hull(points)
        if not ring:
            continue
        zones.append(
            {
                "name": area["name"],
                "population": area.get("population"),
                "polygon": ring,
            }
        )
    return {
        "stub": reach["stub"],
        "owner": reach["owner"],
        "fallback": reach["fallback"],
        "zones": zones,
    }


def _graph_bbox(graph) -> dict:
    found = rows(graph.query("MATCH (m:Meta {key: 'network'}) RETURN m.bbox"))
    if found:
        parsed = parse_bbox(found[0][0])
        if parsed:
            return parsed
    return bbox_dict()


def overlay_payload(settings: Settings | None = None) -> dict:
    cfg = settings or Settings.from_env()
    graph = get_graph(cfg)
    if not _loaded(graph):
        return {
            "loaded": False,
            "graph": cfg.graph,
            "bbox": bbox_dict(),
            "pins": [],
            "closed": [],
            "counts": None,
            "network": None,
        }
    counts = {
        "intersections": int(rows(graph.query("MATCH (i:Intersection) RETURN count(i)"))[0][0]),
        "road_edges": int(rows(graph.query("MATCH ()-[r:ROAD]->() RETURN count(r)"))[0][0]),
        "passable": int(rows(graph.query("MATCH ()-[p:PASSABLE]->() RETURN count(p)"))[0][0]),
    }
    network_rows = rows(
        graph.query(
            """
            MATCH (m:Meta {key: 'network'})
            RETURN m.source, m.attribution
            """
        )
    )
    network = {"source": "unknown", "attribution": ""}
    if network_rows:
        source, attribution = network_rows[0]
        network = {"source": source or "unknown", "attribution": attribution or ""}
    router_rows = rows(graph.query("MATCH (m:Meta {key: 'router'}) RETURN m.algo"))
    districts = [
        {"name": name, "cut_off": bool(cut_off)}
        for name, cut_off in rows(
            graph.query("MATCH (d:District) RETURN d.name, d.cut_off ORDER BY d.name")
        )
    ]
    return {
        "loaded": True,
        "graph": cfg.graph,
        "bbox": _graph_bbox(graph),
        "counts": counts,
        "network": network,
        "router": router_rows[0][0] if router_rows else None,
        "pins": _pins(graph),
        "closed": closed_roads(graph),
        "districts": districts,
    }


def note_reroute(road_id: str, settings: Settings | None = None) -> dict:
    """Record that citizen fan-out is still Sahil's. Does not write a Decision."""
    graph = get_graph(settings)
    return reroute_affected(graph, road_id)
