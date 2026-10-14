"""Load the synthetic Valencia network into FalkorDB."""

from __future__ import annotations

from floodline import cypher
from floodline.config import Settings
from floodline.db import connect, rows
from floodline.network import (
    DISTRICTS,
    HOSPITALS,
    IN_DISTRICT,
    PLACES,
    ROADS,
    SHELTERS,
    UNIT_CALLSIGN,
    UNIT_OSMID,
    place,
    road_geometry,
)
from floodline.routing import ensure_cch, probe_routers


def _safe(graph, query: str, params: dict | None = None) -> dict:
    try:
        graph.query(query, params or {})
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def reset_and_load(settings: Settings | None = None, probe_src: int | None = None, probe_dst: int | None = None) -> dict:
    cfg = settings or Settings.from_env()
    db = connect(cfg)
    graph = db.select_graph(cfg.graph)
    try:
        graph.delete()
    except Exception:
        pass
    graph = db.select_graph(cfg.graph)

    for node in PLACES:
        graph.query(
            """
            CREATE (:Intersection {
              osmid: $osmid,
              name: $name,
              lat: $lat,
              lon: $lon,
              district: $district,
              role: $role
            })
            """,
            {
                "osmid": node.osmid,
                "name": node.name,
                "lat": node.lat,
                "lon": node.lon,
                "district": node.district,
                "role": node.role,
            },
        )

    geo_errors = []
    for node in PLACES:
        try:
            graph.query(
                """
                MATCH (i:Intersection {osmid: $osmid})
                SET i.loc = point({latitude: $lat, longitude: $lon})
                """,
                {"osmid": node.osmid, "lat": node.lat, "lon": node.lon},
            )
        except Exception as exc:
            geo_errors.append(str(exc))
            break

    for road in ROADS:
        geom = road_geometry(road)
        for src, dst in ((road.a, road.b), (road.b, road.a)):
            graph.query(
                """
                MATCH (a:Intersection {osmid: $src}), (b:Intersection {osmid: $dst})
                CREATE (a)-[:ROAD {
                  road_id: $road_id,
                  name: $name,
                  osm_way: $osm_way,
                  length_m: $length_m,
                  speed_kph: $speed_kph,
                  base_travel_s: $travel_s,
                  travel_s: $travel_s,
                  capacity: $capacity,
                  status: 'open'
                }]->(b)
                CREATE (a)-[:PASSABLE {road_id: $road_id}]->(b)
                """,
                {
                    "src": src,
                    "dst": dst,
                    "road_id": road.road_id,
                    "name": road.name,
                    "osm_way": f"synthetic/{road.road_id}",
                    "length_m": geom["length_m"],
                    "speed_kph": geom["speed_kph"],
                    "travel_s": road.travel_s,
                    "capacity": road.capacity,
                },
            )

    for hospital in HOSPITALS:
        graph.query(
            """
            MATCH (i:Intersection {osmid: $osmid})
            CREATE (h:Hospital {
              id: $id,
              name: $name,
              beds_total: $beds_total,
              beds_free: $beds_free,
              er_open: $er_open,
              lat: i.lat,
              lon: i.lon
            })-[:ACCESS {dist_m: 0}]->(i)
            """,
            {
                "osmid": hospital.osmid,
                "id": hospital.id,
                "name": hospital.name,
                "beds_total": hospital.beds_total,
                "beds_free": hospital.beds_free,
                "er_open": hospital.er_open,
            },
        )

    for shelter in SHELTERS:
        graph.query(
            """
            MATCH (i:Intersection {osmid: $osmid})
            CREATE (:Shelter {
              id: $id,
              name: $name,
              capacity: $capacity,
              occupancy: $occupancy
            })-[:ACCESS {dist_m: 0}]->(i)
            """,
            {
                "osmid": shelter.osmid,
                "id": shelter.id,
                "name": shelter.name,
                "capacity": shelter.capacity,
                "occupancy": shelter.occupancy,
            },
        )

    for district in DISTRICTS:
        graph.query(
            """
            CREATE (:District {
              name: $name,
              population: $population,
              zone_id: $zone_id,
              population_source: 'synthetic'
            })
            """,
            district,
        )
    for osmid, district_name in IN_DISTRICT.items():
        graph.query(
            """
            MATCH (i:Intersection {osmid: $osmid}), (d:District {name: $name})
            CREATE (i)-[:IN_DISTRICT]->(d)
            """,
            {"osmid": osmid, "name": district_name},
        )

    origin = place(UNIT_OSMID)
    graph.query(
        """
        MATCH (i:Intersection {osmid: $osmid})
        CREATE (:Unit {
          callsign: $callsign,
          status: 'transport',
          lat: $lat,
          lon: $lon
        })-[:ACCESS {dist_m: 0}]->(i)
        """,
        {
            "osmid": UNIT_OSMID,
            "callsign": UNIT_CALLSIGN,
            "lat": origin.lat,
            "lon": origin.lon,
        },
    )

    for name, role in (("Scout", "closures"), ("Dispatcher", "routing")):
        graph.query(
            """
            CREATE (:Agent {name: $name, role: $role, heartbeat_at: $at})
            """,
            {"name": name, "role": role, "at": "2024-10-29T14:00:00Z"},
        )

    indexes = {
        "osmid": _safe(graph, cypher.CREATE_OSMID_INDEX),
        "loc": _safe(graph, cypher.CREATE_LOC_INDEX) if not geo_errors else {"ok": False, "error": geo_errors[0]},
        "hospital": _safe(graph, cypher.CREATE_HOSPITAL_INDEX),
        "task": _safe(graph, cypher.CREATE_TASK_INDEX),
        "decision": _safe(graph, cypher.CREATE_DECISION_INDEX),
        "road_status": _safe(graph, cypher.CREATE_ROAD_STATUS_INDEX),
    }
    cch = ensure_cch(graph)

    src = probe_src if probe_src is not None else UNIT_OSMID
    dst = probe_dst if probe_dst is not None else HOSPITALS[0].osmid
    routers = probe_routers(graph, src, dst)

    intersections = int(rows(graph.query("MATCH (i:Intersection) RETURN count(i)"))[0][0])
    road_edges = int(rows(graph.query("MATCH ()-[r:ROAD]->() RETURN count(r)"))[0][0])
    passable = int(rows(graph.query("MATCH ()-[p:PASSABLE]->() RETURN count(p)"))[0][0])
    return {
        "graph": cfg.graph,
        "intersections": int(intersections),
        "road_edges": int(road_edges),
        "passable": int(passable),
        "indexes": indexes,
        "cch": cch,
        "routers": routers,
        "geo_points": not geo_errors,
    }
