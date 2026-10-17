"""Read-only views of the graph for the UI.

``memory_graph`` returns the agent-memory chain (Report, Decision, Task, Agent,
Unit, Shelter, Citizen, ...) as nodes and edges for a force-directed view.
``network_geojson`` returns the loaded road network as GeoJSON with each
intersection's WCC component on PASSABLE, so the map can colour cut-off pieces.
Neither function writes to the graph.
"""

from __future__ import annotations

import json

from floodline import cypher
from floodline.db import rows
from floodline.events import query_label
from floodline.navigation import dedupe_route_title

MEMORY_LABELS = ["Report", "Decision", "Task", "Agent", "Unit", "Shelter", "Hospital", "Citizen", "Incident"]
# Memory edges among those labels. Intersection-facing edges (USED_EDGE, ACCESS, AT, NEAR) are left out:
# they belong to the road network, which the map view shows.
MEMORY_RELS = [
    "MADE",
    "CLAIMED",
    "BASED_ON",
    "SUPERSEDES",
    "AFFECTS",
    "HANDOFF",
    "FOR",
    "REROUTED",
    "CLOSES",
    "DUPLICATE_OF",
]

_DECISIONS_Q = """
MATCH (d:Decision)
RETURN d.id, d.seq, d.kind, d.algo, d.at, d.result_summary, d.explanation,
       d.superseded, d.travel_s, d.road_id, d.planner, d.reasoning
ORDER BY coalesce(d.seq, 0) DESC
LIMIT $limit
"""

_TASKS_Q = """
MATCH (t:Task)
RETURN t.id, t.seq, t.kind, t.status, t.road_name, t.road_id, t.created_at,
       t.report_id, t.close_decision_id
ORDER BY coalesce(t.seq, 0) DESC
LIMIT $limit
"""

_OTHER_Q = """
MATCH (n)
WHERE any(l IN labels(n) WHERE l IN $labels)
RETURN labels(n)[0], coalesce(n.id, n.name, n.callsign), coalesce(n.name, n.callsign, n.text, n.id),
       n.text, n.received_at, n.capacity, n.occupancy, n.status
LIMIT 300
"""

_EDGES_Q = """
MATCH (a)-[r]->(b)
WHERE type(r) IN $rels
  AND any(l IN labels(a) WHERE l IN $labels)
  AND any(l IN labels(b) WHERE l IN $labels)
RETURN coalesce(a.id, a.name, a.callsign), type(r), coalesce(b.id, b.name, b.callsign)
LIMIT 3000
"""


def _clean(props: dict) -> dict:
    return {k: v for k, v in props.items() if v is not None and v != ""}


def memory_graph(graph, limit: int = 60) -> dict:
    """Nodes and edges for the newest ``limit`` Decisions and Tasks plus their neighbours."""
    nodes: dict[str, dict] = {}
    for did, seq, kind, algo, at, summary, explanation, superseded, travel_s, road_id, planner, reasoning in rows(
        graph.query(_DECISIONS_Q, {"limit": int(limit)})
    ):
        nodes[str(did)] = {
            "id": str(did),
            "label": "Decision",
            "title": dedupe_route_title(summary or kind or "Decision"),
            "seq": int(seq or 0),
            "props": _clean(
                {
                    "kind": kind,
                    "algo": algo,
                    "at": at,
                    "explanation": explanation,
                    "superseded": bool(superseded),
                    "travel_s": travel_s,
                    "road_id": road_id,
                    "planner": planner,
                    "reasoning": reasoning,
                }
            ),
            "cypher": query_label(algo, kind),
        }
    for tid, seq, kind, status, road_name, road_id, created_at, report_id, close_id in rows(
        graph.query(_TASKS_Q, {"limit": int(limit)})
    ):
        nodes[str(tid)] = {
            "id": str(tid),
            "label": "Task",
            "title": f"{kind} · {status}",
            "seq": int(seq or 0),
            "props": _clean(
                {
                    "kind": kind,
                    "status": status,
                    "road": road_name or road_id,
                    "created_at": created_at,
                    "report_id": report_id,
                    "close_decision_id": close_id,
                }
            ),
            "cypher": query_label(None, kind),
        }
    others = [l for l in MEMORY_LABELS if l not in ("Decision", "Task")]
    for label, nid, name, text, received_at, capacity, occupancy, status in rows(
        graph.query(_OTHER_Q, {"labels": others})
    ):
        if nid is None:
            continue
        nodes[str(nid)] = {
            "id": str(nid),
            "label": label,
            "title": str(name or nid)[:80],
            "seq": 0,
            "props": _clean(
                {
                    "text": text,
                    "received_at": received_at,
                    "capacity": capacity,
                    "occupancy": occupancy,
                    "status": status,
                }
            ),
            "cypher": "",
        }
    edges = []
    seen = set()
    for src, rel, dst in rows(graph.query(_EDGES_Q, {"rels": MEMORY_RELS, "labels": MEMORY_LABELS})):
        src, dst = str(src), str(dst)
        if src in nodes and dst in nodes and (src, rel, dst) not in seen:
            seen.add((src, rel, dst))
            edges.append({"source": src, "target": dst, "type": rel, "derived": False})
    # Task.report_id is stored as a property until Sahil's (:Task)-[:FOR]->(:Report) lands.
    for node in list(nodes.values()):
        if node["label"] != "Task":
            continue
        rid = node["props"].get("report_id")
        if rid and rid in nodes and (node["id"], "FOR", rid) not in seen:
            edges.append({"source": node["id"], "target": rid, "type": "FOR", "derived": True})
    linked = {e["source"] for e in edges} | {e["target"] for e in edges}
    # Drop isolated shelters/hospitals so the view stays about the incident.
    keep = {
        k: v
        for k, v in nodes.items()
        if k in linked or v["label"] in ("Decision", "Task", "Report", "Agent")
    }
    counts: dict[str, int] = {}
    for node in keep.values():
        counts[node["label"]] = counts.get(node["label"], 0) + 1
    cursor = max([n["seq"] for n in keep.values()] or [0])
    return {"nodes": list(keep.values()), "edges": edges, "counts": counts, "cursor": cursor}


_NODES_GEO_Q = """
MATCH (i:Intersection)
RETURN i.osmid, i.lon, i.lat
"""

_EDGES_GEO_Q = """
MATCH (a:Intersection)-[r:ROAD]->(b:Intersection)
RETURN a.osmid, b.osmid, r.road_id, r.name, r.status, r.highway
LIMIT $limit
"""

_CITIZEN_ROUTE_Q = """
MATCH (c:Citizen)
WHERE c.id <> 'citizen' AND c.path_osmids IS NOT NULL AND c.path_osmids <> ''
RETURN c.path_osmids
ORDER BY c.updated_at DESC
LIMIT 1
"""

_ROUTE_Q = """
MATCH (d:Decision)
WHERE d.kind IN ['citizen_route', 'route'] AND coalesce(d.superseded, false) = false
RETURN d.path_osmids
ORDER BY CASE d.kind WHEN 'citizen_route' THEN 0 ELSE 1 END, coalesce(d.seq, 0) DESC
LIMIT 1
"""


def _parse_osmids(raw) -> list[int]:
    if not raw:
        return []
    values = json.loads(raw) if isinstance(raw, str) else raw
    return [int(item) for item in values]


def _active_route_osmids(graph) -> list[int]:
    """The green highlight is the citizen's route, not the ambulance's."""
    found = rows(graph.query(_CITIZEN_ROUTE_Q))
    if found and found[0][0]:
        return _parse_osmids(found[0][0])
    found = rows(graph.query(_ROUTE_Q))
    if found and found[0][0]:
        return _parse_osmids(found[0][0])
    return []


def summarise_components(components: dict[int, int]) -> tuple[int | None, dict[int, int]]:
    sizes: dict[int, int] = {}
    for comp in components.values():
        sizes[comp] = sizes.get(comp, 0) + 1
    main = max(sizes, key=lambda c: sizes[c]) if sizes else None
    return main, sizes


def network_geojson(graph, max_edges: int = 30000, route_osmids: list[int] | None = None) -> dict:
    """Intersections as points and ROAD edges as lines, tagged with WCC component.

    ``cut_off`` is true for intersections outside the largest PASSABLE component.
    Two-way roads are stored as two ROAD edges; only one line is emitted per pair.
    """
    coords = {
        int(osmid): (round(float(lon), 6), round(float(lat), 6))
        for osmid, lon, lat in rows(graph.query(_NODES_GEO_Q))
        if lon is not None and lat is not None
    }
    components = {int(o): int(c) for o, c in rows(graph.query(cypher.WCC_PASSABLE))}
    main, sizes = summarise_components(components)
    if route_osmids is None:
        route_osmids = _active_route_osmids(graph)
    route_pairs = {
        frozenset((a, b)) for a, b in zip(route_osmids, route_osmids[1:])
    }
    features = []
    done: dict[frozenset, dict] = {}
    closed = 0
    edge_rows = rows(graph.query(_EDGES_GEO_Q, {"limit": int(max_edges)}))
    for src, dst, road_id, name, status, highway in edge_rows:
        src, dst = int(src), int(dst)
        if src not in coords or dst not in coords:
            continue
        key = frozenset((src, dst))
        is_closed = status == "closed"
        if key in done:
            if is_closed:
                done[key]["properties"]["status"] = "closed"
            continue
        comp = components.get(src)
        feature = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [coords[src], coords[dst]]},
            "properties": {
                "kind": "edge",
                "road_id": road_id or "",
                "name": name or "",
                "status": "closed" if is_closed else "open",
                "on_route": key in route_pairs,
                "cut_off": comp is not None and comp != main,
            },
        }
        done[key] = feature
        features.append(feature)
    for feature in features:
        if feature["properties"]["status"] == "closed":
            closed += 1
    cut_nodes = 0
    for osmid, (lon, lat) in coords.items():
        comp = components.get(osmid)
        cut = comp is not None and comp != main
        cut_nodes += int(cut)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"kind": "node", "osmid": osmid, "component": comp, "cut_off": cut},
            }
        )
    return {
        "type": "FeatureCollection",
        "features": features,
        "stats": {
            "intersections": len(coords),
            "edges": len(done),
            "closed_edges": closed,
            "components": len(sizes),
            "cut_off_nodes": cut_nodes,
            "route_hops": max(0, len(route_osmids) - 1),
            "truncated": len(edge_rows) >= max_edges,
        },
    }
