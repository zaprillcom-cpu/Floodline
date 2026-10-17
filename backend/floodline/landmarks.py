"""Landmark nodes for malls, stations, hospitals, and schools on a loaded tile.

Each landmark is snapped to the nearest intersection and stored as
``(:Landmark)-[:NEAR]->(:Intersection)``. Road geocoding uses that link when a
report says "near Oberoi Mall" or "near Goregaon station".
"""

from __future__ import annotations

from floodline.network import haversine_m
from floodline.osm_network import OVERPASS_MIRRORS, USER_AGENT, call_with_deadline

_POI_TIMEOUT_S = 3.0
_SNAP_M = 700

_GENERIC = {
    "hospital",
    "school",
    "shelter",
    "mall",
    "station",
    "community centre",
    "community center",
    "social facility",
    "sports centre",
    "sports center",
}


def landmark_records(*groups: list[dict]) -> list[dict]:
    """Named places already on the tile (schools, hospitals) become landmarks."""
    records = []
    seen = set()
    for group in groups:
        for item in group or []:
            name = str(item.get("name") or "").strip()
            if not name or name.lower() in _GENERIC:
                continue
            if item.get("lat") is None or item.get("lon") is None:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            records.append(
                {
                    "id": str(item.get("id") or f"lm-{len(records)}")[:80],
                    "name": name[:120],
                    "kind": str(item.get("kind") or item.get("role") or "place")[:40],
                    "lat": float(item["lat"]),
                    "lon": float(item["lon"]),
                }
            )
    return records


def poi_overpass_query(bbox: dict, timeout_s: int = 8) -> str:
    south, west, north, east = bbox["south"], bbox["west"], bbox["north"], bbox["east"]
    box = f"({south},{west},{north},{east})"
    return (
        f"[out:json][timeout:{int(timeout_s)}];"
        "("
        f'node["name"]["shop"="mall"]{box};'
        f'way["name"]["shop"="mall"]{box};'
        f'node["name"]["amenity"="marketplace"]{box};'
        f'node["name"]["railway"~"station|halt"]{box};'
        f'node["name"]["public_transport"="station"]{box};'
        f'node["name"]["amenity"="hospital"]{box};'
        f'node["name"]["amenity"="school"]{box};'
        f'way["name"]["amenity"="school"]{box};'
        ");"
        "out center;"
    )


def _kind(tags: dict) -> str:
    if tags.get("shop") == "mall" or tags.get("amenity") == "marketplace":
        return "mall"
    if tags.get("railway") or tags.get("public_transport") == "station":
        return "station"
    if tags.get("amenity") == "hospital":
        return "hospital"
    if tags.get("amenity") == "school":
        return "school"
    return "place"


def parse_poi_elements(payload: dict) -> list[dict]:
    records = []
    for element in payload.get("elements") or []:
        tags = element.get("tags") or {}
        name = str(tags.get("name") or "").strip()
        if not name:
            continue
        lat = element.get("lat")
        lon = element.get("lon")
        if lat is None or lon is None:
            center = element.get("center") or {}
            lat, lon = center.get("lat"), center.get("lon")
        if lat is None or lon is None:
            continue
        records.append(
            {
                "id": f"osm-{element.get('type', 'n')}-{element.get('id')}",
                "name": name[:120],
                "kind": _kind(tags),
                "lat": float(lat),
                "lon": float(lon),
            }
        )
    return records


def fetch_poi_landmarks(bbox: dict | None, *, post=None) -> list[dict]:
    """One short Overpass call. Failure returns an empty list so a tile still loads."""
    if not bbox or not all(key in bbox for key in ("south", "west", "north", "east")):
        return []
    query = poi_overpass_query(bbox)

    def run():
        sender = post
        if sender is None:
            import requests

            def sender(url, data, timeout):
                response = requests.post(
                    url.rstrip("/") + "/interpreter",
                    data={"data": data},
                    timeout=timeout,
                    headers={"User-Agent": USER_AGENT},
                )
                response.raise_for_status()
                return response.json()

        last = None
        for url in OVERPASS_MIRRORS[:1]:
            try:
                return sender(url, query, _POI_TIMEOUT_S)
            except Exception as exc:
                last = exc
        if last:
            raise last
        return {}

    try:
        payload = call_with_deadline(run, _POI_TIMEOUT_S + 0.5)
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    return parse_poi_elements(payload)


def index_landmarks(graph, landmarks: list[dict], nodes: list[dict]) -> int:
    """Snap each landmark to the nearest intersection and write NEAR. Idempotent."""
    if not landmarks or not nodes:
        return 0
    written = 0
    for item in landmarks:
        nearest = None
        best = _SNAP_M + 1
        for node in nodes:
            if node.get("lat") is None or node.get("lon") is None:
                continue
            gap = haversine_m(float(item["lat"]), float(item["lon"]), float(node["lat"]), float(node["lon"]))
            if gap < best:
                best = gap
                nearest = node
        if nearest is None or best > _SNAP_M:
            continue
        graph.query(
            """
            MATCH (i:Intersection {osmid: $osmid})
            MERGE (l:Landmark {id: $id})
            SET l.name = $name,
                l.kind = $kind,
                l.lat = $lat,
                l.lon = $lon
            MERGE (l)-[n:NEAR]->(i)
            SET n.dist_m = $dist
            """,
            {
                "osmid": int(nearest["osmid"]),
                "id": item["id"],
                "name": item["name"],
                "kind": item["kind"],
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
                "dist": int(best),
            },
        )
        written += 1
    return written
