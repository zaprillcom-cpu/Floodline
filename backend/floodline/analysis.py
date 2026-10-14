"""Cut-off detection and evacuation capacity.

WCC runs on PASSABLE, because that procedure filters by relationship type and
cannot see ROAD.status. maxFlow runs on ROAD.capacity. A closure sets capacity
to 0 and deletes PASSABLE, so both answers move together without deleting ROAD.
"""

from __future__ import annotations

from floodline import cypher
from floodline.config import T_CLOSE
from floodline.db import rows
from floodline.tools import close_road


def cut_off_areas(graph, at: str = T_CLOSE) -> list[dict]:
    """Mark each district cut off when none of its intersections can reach a hospital on PASSABLE."""
    components = {
        int(osmid): int(component_id)
        for osmid, component_id in rows(graph.query(cypher.WCC_PASSABLE))
    }
    hospital_components = {
        components[int(osmid)]
        for (osmid,) in rows(
            graph.query(
                """
                MATCH (:Hospital)-[:ACCESS]->(i:Intersection)
                RETURN i.osmid
                """
            )
        )
        if int(osmid) in components
    }
    districts = []
    for name, population, osmids in rows(
        graph.query(
            """
            MATCH (d:District)
            OPTIONAL MATCH (i:Intersection)-[:IN_DISTRICT]->(d)
            RETURN d.name, d.population, collect(i.osmid)
            ORDER BY d.name
            """
        )
    ):
        node_ids = [int(osmid) for osmid in osmids or [] if osmid is not None]
        member_components = {components[osmid] for osmid in node_ids if osmid in components}
        reachable = bool(member_components & hospital_components)
        cut_off = bool(node_ids) and not reachable
        component_id = None
        if member_components:
            component_id = max(
                member_components,
                key=lambda cid: sum(1 for osmid in node_ids if components.get(osmid) == cid),
            )
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
        districts.append(
            {
                "name": name,
                "population": int(population or 0),
                "cut_off": cut_off,
                "component_id": component_id,
                "intersections": len(node_ids),
            }
        )
    return districts


def _edge_flow(edge, flow) -> dict:
    props = dict(edge.properties or {})
    capacity = int(props.get("capacity") or 0)
    amount = float(flow)
    return {
        "road_id": props.get("road_id"),
        "name": props.get("name") or props.get("road_id"),
        "flow": int(round(amount)),
        "capacity": capacity,
        "saturated": capacity > 0 and amount >= capacity - 1e-6,
    }


def evac_capacity(graph, district: str) -> dict:
    """Max vehicles per hour from a district's intersections to shelter intersections."""
    found = rows(graph.query(cypher.MAX_FLOW, {"district": district}))
    if not found:
        raise LookupError(f"No max-flow result for {district}")
    max_flow, edges, edge_flows = found[0]
    carrying = []
    for edge, flow in zip(edges or [], edge_flows or []):
        if flow is None or float(flow) <= 0:
            continue
        carrying.append(_edge_flow(edge, flow))
    carrying.sort(key=lambda item: (item["saturated"], item["flow"]), reverse=True)
    critical = carrying[0] if carrying else None
    return {
        "district": district,
        "max_flow": int(round(float(max_flow or 0))),
        "critical_edge": critical,
        "saturated": [item for item in carrying if item["saturated"]],
        "edges_used": len(carrying),
    }


def close_district_exits(graph, district: str, by: str = "Analyst", at: str = T_CLOSE) -> list[dict]:
    """Close every open road with one end inside the district and one end outside."""
    found = rows(graph.query(cypher.DISTRICT_EXITS, {"district": district}))
    closed = []
    seen = set()
    for road_id, name in found:
        if road_id in seen:
            continue
        seen.add(road_id)
        result = close_road(graph, road_id, by=by, at=at)
        result["name"] = name or result.get("name")
        closed.append(result)
    return closed


def prove_cutoff(graph, district: str = "Paiporta", at: str = T_CLOSE) -> dict:
    """Close the roads leaving a district and report the WCC and max-flow change."""
    before_areas = cut_off_areas(graph, at=at)
    before_evac = evac_capacity(graph, district)
    closed = close_district_exits(graph, district, at=at)
    after_areas = cut_off_areas(graph, at=at)
    after_evac = evac_capacity(graph, district)
    return {
        "district": district,
        "before": {
            "areas": before_areas,
            "evac": before_evac,
        },
        "closed": closed,
        "after": {
            "areas": after_areas,
            "evac": after_evac,
        },
    }


def assert_paiporta_cutoff(result: dict) -> None:
    """The synthetic Paiporta corridor: one component splits, and flow falls to zero."""
    def district(areas):
        return next(item for item in areas if item["name"] == "Paiporta")

    before = district(result["before"]["areas"])
    after = district(result["after"]["areas"])
    before_flow = result["before"]["evac"]["max_flow"]
    after_flow = result["after"]["evac"]["max_flow"]
    critical = result["before"]["evac"]["critical_edge"]
    if before["cut_off"]:
        raise AssertionError("Paiporta was already cut off")
    if not after["cut_off"]:
        raise AssertionError(f"Paiporta did not flip to cut off: {after}")
    if after_flow >= before_flow:
        raise AssertionError(f"maxFlow did not drop: {before_flow} -> {after_flow}")
    if before_flow != 1200 or after_flow != 0:
        raise AssertionError(f"Expected maxFlow 1200 -> 0, got {before_flow} -> {after_flow}")
    if not critical or critical["name"] != "V-31 Paiporta bridge" or not critical["saturated"]:
        raise AssertionError(f"Expected saturated V-31 bridge as the critical edge, got {critical}")
    closed_ids = {item["road_id"] for item in result["closed"]}
    expected = {"cv-400", "cami-poyo", "massanassa-paiporta"}
    if closed_ids != expected:
        raise AssertionError(f"Corridor exits were {closed_ids}, expected {expected}")
