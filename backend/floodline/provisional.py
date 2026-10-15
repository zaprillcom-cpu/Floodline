"""Provisional Shelter nodes for an area the citizen just loaded.

This is the existing ``ACCESS`` facility pattern, not Sahil's
``(:Shelter)-[:NEAR]->(:Intersection)`` model. ``ensure_shelter_citizen_model``
calls it only as a fallback when that model was not written.
"""

from __future__ import annotations


def attach_provisional_shelters(graph, candidates: list[dict]) -> dict:
    """MERGE Shelter nodes onto intersections the caller already snapped.

    ``candidates`` items need ``id``, ``name``, ``osmid``, ``lat``, ``lon``,
    and ``dist_m``. Capacity is a placeholder so the map can show a pin.
    """
    attached = 0
    for item in candidates:
        osmid = item.get("osmid")
        if osmid is None:
            continue
        graph.query(
            """
            MATCH (i:Intersection {osmid: $osmid})
            MERGE (s:Shelter {id: $id})
            SET s.name = $name,
                s.capacity = $capacity,
                s.occupancy = $occupancy,
                s.lat = $lat,
                s.lon = $lon,
                s.kind = $kind,
                s.provisional = true
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
        attached += 1
    return {"attached": attached, "relationship": "ACCESS"}
