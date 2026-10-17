"""Offline street regions cropped from Geofabrik. Overpass is not required inside them.

Each SQLite file is a drive network plus shelter and hospital points for one
region. A citizen load reads only the rows inside a 2–3 km tile, so the API
process does not pull the whole metro into memory.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from floodline.network import haversine_m
from floodline.osm_network import BACKEND_ROOT, SHELTER_CAP

REGION_DIR = BACKEND_ROOT / "data" / "regions"
OFFLINE_RADIUS_M = 2500


@dataclass(frozen=True)
class Region:
    id: str
    place: str
    west: float
    south: float
    east: float
    north: float
    center_lat: float
    center_lon: float
    path: Path
    callsign: str

    def contains(self, lat: float, lon: float) -> bool:
        return self.south <= lat <= self.north and self.west <= lon <= self.east


REGIONS: tuple[Region, ...] = (
    Region(
        "mumbai",
        "Mumbai",
        72.77,
        18.89,
        73.00,
        19.30,
        19.1663,
        72.8526,
        REGION_DIR / "mumbai.sqlite",
        "M-1",
    ),
    Region(
        "manhattan",
        "New York",
        -74.03,
        40.698,
        -73.91,
        40.882,
        40.758,
        -73.9855,
        REGION_DIR / "manhattan.sqlite",
        "NY-1",
    ),
    Region(
        "valencia",
        "Valencia",
        -0.50,
        39.38,
        -0.28,
        39.55,
        39.4294,
        -0.4183,
        REGION_DIR / "valencia.sqlite",
        "V-1",
    ),
)


def region_containing(lat: float, lon: float) -> Region | None:
    for region in REGIONS:
        if region.contains(lat, lon) and region.path.is_file():
            return region
    return None


def region_by_slug(slug: str) -> Region | None:
    """City picker slugs: mumbai, nyc, valencia."""
    wanted = {"nyc": "manhattan", "new-york": "manhattan"}.get(slug, slug)
    for region in REGIONS:
        if region.id == wanted or region.id == slug:
            return region
    return None


def _connect(region: Region) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{region.path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def _keep_component(node_ids: set[int], edges: list[sqlite3.Row], nearest: int) -> set[int]:
    parent = {node: node for node in node_ids}

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for edge in edges:
        src, dst = int(edge["src"]), int(edge["dst"])
        if src in parent and dst in parent:
            union(src, dst)
    if nearest not in parent:
        nearest = next(iter(parent))
    root = find(nearest)
    kept = {node for node in parent if find(node) == root}
    if len(kept) >= 8:
        return kept
    sizes: dict[int, int] = {}
    for node in parent:
        sizes[find(node)] = sizes.get(find(node), 0) + 1
    biggest = max(sizes, key=sizes.get)
    return {node for node in parent if find(node) == biggest}


def _radius_bbox(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    dlat = radius_m / 111_320
    cos_lat = math.cos(math.radians(lat))
    dlon = radius_m / (111_320 * cos_lat) if abs(cos_lat) > 0.01 else dlat
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def places_in_radius(lat: float, lon: float, radius_m: float) -> list[dict]:
    """Schools, hospitals, and shelters from the offline region, without loading streets."""
    region = region_containing(lat, lon)
    if region is None:
        return []
    west, south, east, north = _radius_bbox(lat, lon, radius_m)
    try:
        db = _connect(region)
    except Exception:
        return []
    try:
        place_rows = db.execute(
            """
            SELECT id, name, kind, role, lat, lon FROM places
            WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
            """,
            (south, north, west, east),
        ).fetchall()
    except Exception:
        return []
    finally:
        db.close()
    places = []
    for row in place_rows:
        if row["lat"] is None or row["lon"] is None or not row["name"]:
            continue
        places.append(
            {
                "id": row["id"],
                "name": row["name"],
                "kind": row["kind"] or row["role"] or "place",
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
            }
        )
    return places


def crop_tile(region: Region, lat: float, lon: float, radius_m: float) -> tuple[dict, list[dict], list[dict]]:
    """Nodes, edges, shelters, and hospitals inside ``radius_m`` of the point."""
    west, south, east, north = _radius_bbox(lat, lon, radius_m)
    db = _connect(region)
    try:
        node_rows = db.execute(
            """
            SELECT osmid, lat, lon, name FROM nodes
            WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
            """,
            (south, north, west, east),
        ).fetchall()
        if not node_rows:
            raise RuntimeError(f"No bundled streets within {int(radius_m)} m")
        edge_rows = db.execute(
            """
            SELECT e.src, e.dst, e.road_id, e.name, e.osm_way, e.highway,
                   e.length_m, e.speed_kph, e.travel_s, e.capacity
            FROM edges e
            JOIN nodes a ON a.osmid = e.src
            JOIN nodes b ON b.osmid = e.dst
            WHERE a.lat BETWEEN ? AND ? AND a.lon BETWEEN ? AND ?
              AND b.lat BETWEEN ? AND ? AND b.lon BETWEEN ? AND ?
            """,
            (south, north, west, east, south, north, west, east),
        ).fetchall()
        place_rows = db.execute(
            """
            SELECT id, name, kind, role, lat, lon FROM places
            WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
            """,
            (south, north, west, east),
        ).fetchall()
    finally:
        db.close()

    if not edge_rows:
        raise RuntimeError(f"No bundled streets within {int(radius_m)} m")

    by_id = {int(row["osmid"]): row for row in node_rows}
    nearest = min(by_id, key=lambda osmid: haversine_m(lat, lon, by_id[osmid]["lat"], by_id[osmid]["lon"]))
    keep = _keep_component(set(by_id), edge_rows, nearest)
    nodes = []
    for osmid in sorted(keep):
        row = by_id.get(osmid)
        if row is None:
            continue
        nodes.append(
            {
                "osmid": osmid,
                "name": row["name"],
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "district": region.place,
                "role": "junction",
            }
        )
    edges = []
    for row in edge_rows:
        src, dst = int(row["src"]), int(row["dst"])
        if src not in keep or dst not in keep:
            continue
        edges.append(
            {
                "src": src,
                "dst": dst,
                "road_id": row["road_id"],
                "name": row["name"],
                "osm_way": row["osm_way"],
                "highway": row["highway"],
                "length_m": int(row["length_m"]),
                "speed_kph": float(row["speed_kph"]),
                "travel_s": int(row["travel_s"]),
                "capacity": int(row["capacity"]),
            }
        )
    if not edges:
        raise RuntimeError(f"No bundled streets within {int(radius_m)} m")

    shelters = []
    hospitals = []
    for row in place_rows:
        item = {
            "id": row["id"],
            "name": row["name"],
            "kind": row["kind"],
            "lat": float(row["lat"]),
            "lon": float(row["lon"]),
            "dist_m": int(haversine_m(lat, lon, float(row["lat"]), float(row["lon"]))),
        }
        if row["role"] == "hospital":
            hospitals.append(item)
        else:
            shelters.append(item)
    shelters.sort(key=lambda item: item["dist_m"])
    hospitals.sort(key=lambda item: item["dist_m"])
    prepared = {
        "source": "osm",
        "attribution": "© OpenStreetMap contributors",
        "license": "ODbL-1.0",
        "tool": "geofabrik-sqlite",
        "network_type": "drive",
        "bbox": {"west": west, "south": south, "east": east, "north": north},
        "district_method": "area",
        "districts": [
            {
                "name": region.place,
                "population": 0,
                "zone_id": "area",
                "population_source": "unknown",
            }
        ],
        "nodes": nodes,
        "edges": edges,
        "center": {"lat": lat, "lon": lon},
        "place": region.place,
        "region": region.id,
    }
    return prepared, shelters[:SHELTER_CAP], hospitals[:4]
