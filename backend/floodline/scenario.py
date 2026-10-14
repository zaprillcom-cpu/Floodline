"""One closure, one reroute. Shared by the CLI demo and the API."""

from __future__ import annotations

import json

from floodline.agents.dispatcher import reroute
from floodline.agents.scout import handle_report
from floodline.config import T_CLOSE, T_ROUTE, Settings
from floodline.db import get_graph, rows
from floodline.ingest import reset_and_load
from floodline.network import DEMO_REPORT, UNIT_CALLSIGN, UNIT_OSMID
from floodline.routing import route
from floodline.tools import best_hospital, write_route_decision


def _list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return json.loads(value)
    return list(value)


def seed_opening_route(graph) -> dict:
    hospital = best_hospital(graph, UNIT_OSMID)
    found = route(graph, UNIT_OSMID, hospital["osmid"])
    explanation = (
        f"Unit {UNIT_CALLSIGN} is transporting from its current intersection "
        f"to {hospital['name']}, the nearest open ER "
        f"({hospital['travel_s']} s, {hospital['beds_free']} beds free). "
        f"Path: {' → '.join(found.names)}."
    )
    decision_id = write_route_decision(
        graph,
        found,
        at=T_ROUTE,
        explanation=explanation,
    )
    return {
        "decision_id": decision_id,
        "hospital": hospital,
        "route": found.as_dict(),
        "explanation": explanation,
    }


def reset_scenario(settings: Settings | None = None) -> dict:
    cfg = settings or Settings.from_env()
    loaded = reset_and_load(cfg)
    graph = get_graph(cfg)
    opening = seed_opening_route(graph)
    state = snapshot(cfg)
    state["load"] = loaded
    state["opening"] = opening
    return state


def file_report(text: str = DEMO_REPORT, settings: Settings | None = None) -> dict:
    cfg = settings or Settings.from_env()
    graph = get_graph(cfg)
    scout = handle_report(graph, text, at=T_CLOSE)
    dispatcher = reroute(graph, at=T_CLOSE)
    state = snapshot(cfg)
    state["scout"] = scout
    state["dispatcher"] = dispatcher
    return state


def snapshot(settings: Settings | None = None) -> dict:
    cfg = settings or Settings.from_env()
    graph = get_graph(cfg)
    count = rows(graph.query("MATCH (i:Intersection) RETURN count(i)"))
    if not count or int(count[0][0]) == 0:
        return {"loaded": False, "graph": cfg.graph}

    nodes = [
        {
            "osmid": int(osmid),
            "name": name,
            "lat": float(lat),
            "lon": float(lon),
            "district": district,
            "role": role,
        }
        for osmid, name, lat, lon, district, role in rows(
            graph.query(
                """
                MATCH (i:Intersection)
                RETURN i.osmid, i.name, i.lat, i.lon, i.district, i.role
                ORDER BY i.osmid
                """
            )
        )
    ]
    edges = [
        {
            "road_id": road_id,
            "name": name,
            "from": int(src),
            "to": int(dst),
            "travel_s": int(travel_s),
            "base_travel_s": int(base_travel_s),
            "capacity": int(capacity),
            "status": status,
        }
        for src, dst, road_id, name, travel_s, base_travel_s, capacity, status in rows(
            graph.query(
                """
                MATCH (a:Intersection)-[r:ROAD]->(b:Intersection)
                RETURN a.osmid, b.osmid, r.road_id, r.name, r.travel_s,
                       r.base_travel_s, r.capacity, r.status
                ORDER BY r.road_id, a.osmid
                """
            )
        )
    ]
    passable = int(
        rows(graph.query("MATCH ()-[p:PASSABLE]->() RETURN count(p)"))[0][0]
    )
    hospitals = [
        {
            "id": hospital_id,
            "name": name,
            "beds_free": int(beds_free),
            "beds_total": int(beds_total),
            "er_open": bool(er_open),
            "osmid": int(osmid),
        }
        for hospital_id, name, beds_free, beds_total, er_open, osmid in rows(
            graph.query(
                """
                MATCH (h:Hospital)-[:ACCESS]->(i:Intersection)
                RETURN h.id, h.name, h.beds_free, h.beds_total, h.er_open, i.osmid
                ORDER BY h.id
                """
            )
        )
    ]
    districts = []
    for name, population, cut_off, component_id in rows(
        graph.query(
            """
            MATCH (d:District)
            RETURN d.name, d.population, d.cut_off, d.component_id
            ORDER BY d.name
            """
        )
    ):
        districts.append(
            {
                "name": name,
                "population": int(population or 0),
                "cut_off": bool(cut_off),
                "component_id": None if component_id in (None, -1) else int(component_id),
            }
        )

    unit_rows = rows(
        graph.query(
            """
            MATCH (u:Unit)-[:ACCESS]->(i:Intersection)
            RETURN u.callsign, u.status, i.osmid
            """
        )
    )
    unit = None
    if unit_rows:
        callsign, status, osmid = unit_rows[0]
        unit = {"callsign": callsign, "status": status, "osmid": int(osmid)}

    decisions = []
    for (
        decision_id,
        seq,
        at,
        kind,
        algo,
        explanation,
        summary,
        superseded,
        travel_s,
        osmids,
        names,
        road_id,
        previous_id,
        agent,
    ) in rows(
        graph.query(
            """
            MATCH (d:Decision)
            OPTIONAL MATCH (d)-[:SUPERSEDES]->(prev:Decision)
            OPTIONAL MATCH (a:Agent)-[:MADE]->(d)
            RETURN d.id, d.seq, d.at, d.kind, d.algo, d.explanation,
                   d.result_summary, d.superseded, d.travel_s, d.path_osmids,
                   d.path_names, d.road_id, prev.id, a.name
            ORDER BY d.seq
            """
        )
    ):
        decisions.append(
            {
                "id": decision_id,
                "seq": int(seq),
                "at": at,
                "kind": kind,
                "algo": algo,
                "explanation": explanation,
                "summary": summary,
                "superseded": bool(superseded),
                "travel_s": None if travel_s is None else int(travel_s),
                "osmids": [int(v) for v in _list(osmids)],
                "names": [str(v) for v in _list(names)],
                "road_id": road_id or None,
                "previous_id": previous_id,
                "agent": agent,
            }
        )

    routes = _routes_from_decisions(decisions)
    router_rows = rows(
        graph.query(
            "MATCH (m:Meta {key: 'router'}) RETURN m.algo, m.available"
        )
    )
    router = {"chosen": None, "available": {}}
    if router_rows:
        chosen, available = router_rows[0]
        if isinstance(available, str):
            available = json.loads(available)
        router = {"chosen": chosen, "available": available or {}}

    network_rows = rows(
        graph.query(
            """
            MATCH (m:Meta {key: 'network'})
            RETURN m.source, m.bbox, m.attribution, m.district_method
            """
        )
    )
    network = {"source": "unknown", "bbox": None, "attribution": "", "district_method": ""}
    if network_rows:
        source, bbox, attribution, district_method = network_rows[0]
        if isinstance(bbox, str) and bbox:
            try:
                bbox = json.loads(bbox)
            except json.JSONDecodeError:
                bbox = None
        elif not bbox:
            bbox = None
        network = {
            "source": source or "unknown",
            "bbox": bbox,
            "attribution": attribution or "",
            "district_method": district_method or "",
        }

    closed = sorted({edge["road_id"] for edge in edges if edge["status"] == "closed"})
    narrative = {
        "closure": next(
            (item["explanation"] for item in decisions if item["kind"] == "close"),
            None,
        ),
        "route": None,
    }
    active = routes["after"] or routes["before"]
    if active:
        match = next(item for item in decisions if item["id"] == active["decision_id"])
        narrative["route"] = match["explanation"]

    return {
        "loaded": True,
        "graph": cfg.graph,
        "clock": {
            "opening": T_ROUTE,
            "closure": T_CLOSE,
            "label": "reconstructed",
        },
        "router": router,
        "network": network,
        "counts": {
            "intersections": len(nodes),
            "road_edges": len(edges),
            "passable": passable,
        },
        "nodes": nodes,
        "edges": edges,
        "hospitals": hospitals,
        "districts": districts,
        "unit": unit,
        "closed_roads": closed,
        "routes": routes,
        "decisions": decisions,
        "narrative": narrative,
    }


def _routes_from_decisions(decisions: list[dict]) -> dict:
    route_decisions = [item for item in decisions if item["kind"] == "route"]
    active = [item for item in route_decisions if not item["superseded"]]
    current = active[-1] if active else None

    def pack(item: dict) -> dict:
        return {
            "decision_id": item["id"],
            "algo": item["algo"],
            "travel_s": item["travel_s"],
            "osmids": item["osmids"],
            "names": item["names"],
            "at": item["at"],
        }

    if current and current.get("previous_id"):
        previous = next(
            item for item in route_decisions if item["id"] == current["previous_id"]
        )
        return {"before": pack(previous), "after": pack(current)}
    if current:
        return {"before": pack(current), "after": None}
    return {"before": None, "after": None}


def assert_reroute(state: dict) -> None:
    """Raise AssertionError unless the closure produced a different, longer, open path."""
    if not state.get("loaded"):
        raise AssertionError("Graph is empty")
    before = state["routes"]["before"]
    after = state["routes"]["after"]
    if not before or not after:
        raise AssertionError(f"Expected a before and after route, got {state['routes']}")
    if before["osmids"] == after["osmids"]:
        raise AssertionError(f"Route did not change: {before['names']}")
    if after["travel_s"] <= before["travel_s"]:
        raise AssertionError(
            f"New ETA {after['travel_s']} is not longer than {before['travel_s']}"
        )
    closed = set(state["closed_roads"])
    if "v31-paiporta-bridge" not in closed:
        raise AssertionError(f"Bridge was not closed: {closed}")
    bridge_hops = set(zip(before["osmids"], before["osmids"][1:]))
    if (1005, 1004) not in bridge_hops and (1004, 1005) not in bridge_hops:
        raise AssertionError(f"Opening route did not use the bridge: {before['osmids']}")
    after_hops = set(zip(after["osmids"], after["osmids"][1:]))
    if (1005, 1004) in after_hops or (1004, 1005) in after_hops:
        raise AssertionError(f"New route still crosses the bridge: {after['osmids']}")
    directed = [edge for edge in state["edges"] if edge["road_id"] == "v31-paiporta-bridge"]
    if any(edge["status"] != "closed" or edge["capacity"] != 0 for edge in directed):
        raise AssertionError(f"Bridge edges were not fully closed: {directed}")
    if state["counts"]["passable"] != state["counts"]["road_edges"] - len(directed):
        raise AssertionError(
            "PASSABLE count should drop by one per closed directed edge: "
            f"passable={state['counts']['passable']} roads={state['counts']['road_edges']}"
        )
