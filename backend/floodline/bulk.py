"""Batch-load intersection and road records into FalkorDB."""

from __future__ import annotations

import json

from floodline import cypher
from floodline.routing import ensure_cch

_NODE_QUERY = """
UNWIND $rows AS row
CREATE (:Intersection {
  osmid: row.osmid,
  name: row.name,
  lat: row.lat,
  lon: row.lon,
  district: row.district,
  role: row.role
})
"""

_LOC_QUERY = """
UNWIND $rows AS row
MATCH (i:Intersection {osmid: row.osmid})
SET i.loc = point({latitude: row.lat, longitude: row.lon})
"""

_EDGE_QUERY = """
UNWIND $rows AS row
MATCH (a:Intersection {osmid: row.src}), (b:Intersection {osmid: row.dst})
CREATE (a)-[:ROAD {
  road_id: row.road_id,
  name: row.name,
  osm_way: row.osm_way,
  highway: row.highway,
  length_m: row.length_m,
  speed_kph: row.speed_kph,
  base_travel_s: row.travel_s,
  travel_s: row.travel_s,
  base_capacity: row.capacity,
  capacity: row.capacity,
  status: 'open'
}]->(b)
CREATE (a)-[:PASSABLE {road_id: row.road_id}]->(b)
"""

_DISTRICT_QUERY = """
UNWIND $rows AS row
CREATE (:District {
  name: row.name,
  population: row.population,
  zone_id: row.zone_id,
  population_source: row.population_source,
  cut_off: false
})
"""

_IN_DISTRICT_QUERY = """
UNWIND $rows AS row
MATCH (i:Intersection {osmid: row.osmid}), (d:District {name: row.district})
CREATE (i)-[:IN_DISTRICT]->(d)
"""


def _batches(rows: list, size: int):
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _safe(graph, query: str) -> dict:
    try:
        graph.query(query)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def load_records(graph, prepared: dict, *, batch_size: int = 200) -> dict:
    """Insert nodes, ROAD and PASSABLE edges, districts, indexes, and the CCH index.

    The caller deletes the graph first. Counts are read back so a dropped row fails the load.
    """
    nodes = prepared["nodes"]
    edges = prepared["edges"]
    districts = prepared["districts"]
    for chunk in _batches(nodes, batch_size):
        graph.query(_NODE_QUERY, {"rows": chunk})
    geo_error = None
    for chunk in _batches(nodes, batch_size):
        try:
            graph.query(
                _LOC_QUERY,
                {"rows": [{"osmid": node["osmid"], "lat": node["lat"], "lon": node["lon"]} for node in chunk]},
            )
        except Exception as exc:
            geo_error = str(exc)
            break
    for chunk in _batches(edges, batch_size):
        graph.query(_EDGE_QUERY, {"rows": chunk})
    if districts:
        graph.query(_DISTRICT_QUERY, {"rows": districts})
        membership = [{"osmid": node["osmid"], "district": node["district"]} for node in nodes]
        for chunk in _batches(membership, batch_size):
            graph.query(_IN_DISTRICT_QUERY, {"rows": chunk})

    from floodline.db import rows

    intersections = int(rows(graph.query("MATCH (i:Intersection) RETURN count(i)"))[0][0])
    road_edges = int(rows(graph.query("MATCH ()-[r:ROAD]->() RETURN count(r)"))[0][0])
    passable = int(rows(graph.query("MATCH ()-[p:PASSABLE]->() RETURN count(p)"))[0][0])
    if intersections != len(nodes) or road_edges != len(edges) or passable != len(edges):
        raise RuntimeError(
            "Bulk load count mismatch: "
            f"intersections {intersections}/{len(nodes)}, "
            f"roads {road_edges}/{len(edges)}, "
            f"passable {passable}/{len(edges)}"
        )

    indexes = {
        "osmid": _safe(graph, cypher.CREATE_OSMID_INDEX),
        "loc": _safe(graph, cypher.CREATE_LOC_INDEX) if geo_error is None else {"ok": False, "error": geo_error},
        "hospital": _safe(graph, cypher.CREATE_HOSPITAL_INDEX),
        "task": _safe(graph, cypher.CREATE_TASK_INDEX),
        "decision": _safe(graph, cypher.CREATE_DECISION_INDEX),
        "road_status": _safe(graph, cypher.CREATE_ROAD_STATUS_INDEX),
    }
    cch = ensure_cch(graph)
    graph.query(
        """
        MERGE (m:Meta {key: 'network'})
        SET m.source = $source,
            m.bbox = $bbox,
            m.attribution = $attribution,
            m.district_method = $district_method
        """,
        {
            "source": prepared.get("source", "osm"),
            "bbox": json.dumps(prepared.get("bbox") or {}),
            "attribution": prepared.get("attribution", ""),
            "district_method": prepared.get("district_method", ""),
        },
    )
    return {
        "intersections": intersections,
        "road_edges": road_edges,
        "passable": passable,
        "districts": len(districts),
        "indexes": indexes,
        "cch": cch,
        "geo_points": geo_error is None,
    }


def attach_facilities(graph, snaps: list[dict]) -> None:
    """Create Hospital, Shelter, and Unit nodes on intersections chosen by the caller."""
    for snap in snaps:
        spec = snap["spec"]
        osmid = snap["osmid"]
        if spec.kind == "hospital":
            graph.query(
                """
                MATCH (i:Intersection {osmid: $osmid})
                CREATE (:Hospital {
                  id: $id,
                  name: $name,
                  beds_total: $beds_total,
                  beds_free: $beds_free,
                  er_open: $er_open,
                  lat: i.lat,
                  lon: i.lon
                })-[:ACCESS {dist_m: $dist_m}]->(i)
                """,
                {
                    "osmid": osmid,
                    "id": spec.id,
                    "name": spec.name,
                    "beds_total": spec.beds_total,
                    "beds_free": spec.beds_free,
                    "er_open": spec.er_open,
                    "dist_m": int(snap["dist_m"]),
                },
            )
        elif spec.kind == "shelter":
            graph.query(
                """
                MATCH (i:Intersection {osmid: $osmid})
                CREATE (:Shelter {
                  id: $id,
                  name: $name,
                  capacity: $capacity,
                  occupancy: $occupancy,
                  lat: i.lat,
                  lon: i.lon
                })-[:ACCESS {dist_m: $dist_m}]->(i)
                """,
                {
                    "osmid": osmid,
                    "id": spec.id,
                    "name": spec.name,
                    "capacity": spec.capacity,
                    "occupancy": spec.occupancy,
                    "dist_m": int(snap["dist_m"]),
                },
            )
        elif spec.kind == "unit":
            graph.query(
                """
                MATCH (i:Intersection {osmid: $osmid})
                CREATE (:Unit {
                  callsign: $callsign,
                  status: 'transport',
                  lat: i.lat,
                  lon: i.lon
                })-[:ACCESS {dist_m: $dist_m}]->(i)
                """,
                {
                    "osmid": osmid,
                    "callsign": spec.callsign or spec.name,
                    "dist_m": int(snap["dist_m"]),
                },
            )
