"""Citizen-facing payloads: a route line, closures, and cut-off shades.

Routing stays in FalkorDB through the existing CCH → A* → SPpaths chain.
Shelter selection itself is the Sahil stub in ``stubs.route_to_shelter``.
"""

from __future__ import annotations

from floodline.config import Settings
from floodline.db import get_graph, rows
from floodline.navigation import line_coordinates
from floodline.osm_network import bbox_dict, parse_bbox
from floodline.stubs import reroute_affected, route_to_shelter, shelter_reachability


def _loaded(graph) -> bool:
    found = rows(graph.query("MATCH (i:Intersection) RETURN count(i) AS n"))
    return bool(found and int(found[0][0]) > 0)


def route_payload(lat: float, lon: float, settings: Settings | None = None) -> dict:
    graph = get_graph(settings)
    if not _loaded(graph):
        raise LookupError("Graph is not loaded")
    raw = route_to_shelter(graph, lat, lon)
    coordinates = line_coordinates(raw["hops"])
    if len(coordinates) < 2:
        origin = raw["origin"]
        dest = raw["destination"]
        if origin.get("lon") is not None and dest.get("lon") is not None:
            coordinates = [
                [round(float(origin["lon"]), 7), round(float(origin["lat"]), 7)],
                [round(float(dest["lon"]), 7), round(float(dest["lat"]), 7)],
            ]
    steps = list(raw["steps"])
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
    return {
        "ok": True,
        "stub": raw["stub"],
        "owner": raw["owner"],
        "fallback": raw["fallback"],
        "origin": raw["origin"],
        "destination": raw["destination"],
        "algo": raw["algo"],
        "eta_s": raw["eta_s"],
        "geojson": {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coordinates},
            "properties": {"algo": raw["algo"], "eta_s": raw["eta_s"]},
        },
        "steps": steps,
    }


def closed_roads(graph) -> list[dict]:
    found = rows(
        graph.query(
            """
            MATCH (a:Intersection)-[r:ROAD {status: 'closed'}]->(b:Intersection)
            RETURN r.road_id, r.name, a.lon, a.lat, b.lon, b.lat
            """
        )
    )
    grouped: dict[str, dict] = {}
    for road_id, name, alon, alat, blon, blat in found:
        if road_id in grouped:
            continue
        grouped[road_id] = {
            "road_id": road_id,
            "name": name or road_id,
            "coordinates": [
                [round(float(alon), 7), round(float(alat), 7)],
                [round(float(blon), 7), round(float(blat), 7)],
            ],
        }
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
