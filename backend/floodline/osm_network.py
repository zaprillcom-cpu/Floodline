"""Valencia flood-corridor drive network, prepared from OpenStreetMap.

The bbox is the L'Horta Sud towns that flooded in October 2024 (Paiporta,
Alfafar, Sedaví, Benetússer) plus Hospital Doctor Peset. It is about 5.0 km
east-west by 4.8 km north-south: large enough for a real detour, small enough
to load on a laptop. District membership is the nearest town centroid, not an
official boundary. Populations, beds, shelters, and the ambulance are synthetic.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from floodline.network import haversine_m

# west, south, east, north. osmnx 2.x graph_from_bbox takes this order.
BBOX = (-0.430, 39.415, -0.372, 39.458)

# Railway's service root is `backend/`, so the extract has to live here
# (backend/data/osm), not at the repository root.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
EXTRACT_PATH = BACKEND_ROOT / "data" / "osm" / "valencia_corridor.json"
CACHE_DIR = BACKEND_ROOT / "data" / "cache"

# veh/h for one direction. Defaults by OSM highway class, not measured counts.
HIGHWAY_CAPACITY = {
    "motorway": 2000,
    "motorway_link": 1200,
    "trunk": 1600,
    "trunk_link": 1000,
    "primary": 1200,
    "primary_link": 800,
    "secondary": 900,
    "secondary_link": 600,
    "tertiary": 600,
    "tertiary_link": 400,
    "unclassified": 450,
    "residential": 350,
    "living_street": 200,
    "service": 150,
    "busway": 400,
    "road": 400,
}
DEFAULT_CAPACITY = 400
FALLBACK_SPEED_KPH = 30.0


@dataclass(frozen=True)
class DistrictSpec:
    name: str
    lat: float
    lon: float
    population: int
    zone_id: str


@dataclass(frozen=True)
class FacilitySpec:
    kind: str
    id: str
    name: str
    lat: float
    lon: float
    beds_total: int = 0
    beds_free: int = 0
    er_open: bool = True
    capacity: int = 0
    occupancy: int = 0
    callsign: str = ""


DISTRICTS: tuple[DistrictSpec, ...] = (
    DistrictSpec("Paiporta", 39.4294, -0.4183, 27000, "horta-sud"),
    DistrictSpec("Alfafar", 39.4225, -0.3908, 21000, "horta-sud"),
    DistrictSpec("Sedaví", 39.4269, -0.3848, 11000, "horta-sud"),
    DistrictSpec("Benetússer", 39.4218, -0.3969, 15000, "horta-sud"),
    DistrictSpec("Valencia", 39.4518, -0.3967, 40000, "valencia"),
)

# Coordinates are the real sites. Bed counts, shelter capacity, and A-7 are synthetic.
FACILITIES: tuple[FacilitySpec, ...] = (
    FacilitySpec(
        "hospital",
        "peset",
        "Hospital Doctor Peset",
        39.4518,
        -0.3967,
        beds_total=40,
        beds_free=12,
    ),
    FacilitySpec(
        "hospital",
        "lafe",
        "Hospital La Fe",
        39.4446,
        -0.3758,
        beds_total=80,
        beds_free=30,
    ),
    FacilitySpec(
        "shelter",
        "alfafar-sports",
        "Alfafar shelter (synthetic)",
        39.4225,
        -0.3908,
        capacity=800,
        occupancy=120,
    ),
    FacilitySpec(
        "shelter",
        "benetusser-hall",
        "Benetússer shelter (synthetic)",
        39.4218,
        -0.3969,
        capacity=500,
        occupancy=40,
    ),
    FacilitySpec("unit", "a7", "A-7", 39.4255, -0.4175, callsign="A-7"),
)


def bbox_dict() -> dict:
    west, south, east, north = BBOX
    return {"west": west, "south": south, "east": east, "north": north}


def assign_district(lat: float, lon: float) -> str:
    best = min(DISTRICTS, key=lambda spec: haversine_m(lat, lon, spec.lat, spec.lon))
    return best.name


def capacity_for(highway) -> int:
    classes = highway if isinstance(highway, (list, tuple)) else [highway]
    scores = []
    for item in classes:
        if item is None:
            continue
        scores.append(HIGHWAY_CAPACITY.get(str(item), DEFAULT_CAPACITY))
    return max(scores) if scores else DEFAULT_CAPACITY


def _text(value, limit: int = 120) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        parts = [_text(item, limit) for item in value]
        value = " / ".join(part for part in parts if part)
    text = str(value).replace("\n", " ").strip()
    return text[:limit]


def _way_token(value) -> str:
    if isinstance(value, (list, tuple)):
        parts = [str(item) for item in value if item is not None]
        return "-".join(parts) if parts else "0"
    if value is None:
        return "0"
    return str(value)


def _travel_s(data: dict) -> int:
    if data.get("travel_time") is not None:
        return max(1, int(round(float(data["travel_time"]))))
    length = float(data.get("length") or 0)
    speed = float(data.get("speed_kph") or FALLBACK_SPEED_KPH)
    if speed <= 0:
        return 1
    return max(1, int(round(length / (speed / 3.6))))


def prepare_drive_graph(graph) -> dict:
    """Turn an OSMnx drive graph into plain node and edge records.

    Parallel edges between the same ordered pair collapse to the fastest one.
    Capacity is summed. A two-way pair that shares an OSM way id gets one
    road_id, so closing that id closes both directions the way the synthetic
    loader does.
    """
    merged: dict[tuple[int, int], dict] = {}
    for src, dst, key, data in graph.edges(keys=True, data=True):
        src_id, dst_id = int(src), int(dst)
        if src_id == dst_id:
            continue
        highway = data.get("highway")
        name = _text(data.get("name")) or _text(highway) or "unnamed road"
        record = {
            "src": src_id,
            "dst": dst_id,
            "key": int(key) if isinstance(key, int) else 0,
            "name": name,
            "osm_way": _way_token(data.get("osmid")),
            "way": _way_token(data.get("osmid")),
            "highway": _text(highway[0] if isinstance(highway, (list, tuple)) and highway else highway, 40)
            or "road",
            "length_m": max(1, int(round(float(data.get("length") or 1)))),
            "speed_kph": round(float(data.get("speed_kph") or FALLBACK_SPEED_KPH), 1),
            "travel_s": _travel_s(data),
            "capacity": capacity_for(highway),
        }
        previous = merged.get((src_id, dst_id))
        if previous is None or record["travel_s"] < previous["travel_s"]:
            if previous is not None:
                record["capacity"] += previous["capacity"]
            merged[(src_id, dst_id)] = record
        else:
            previous["capacity"] += record["capacity"]

    for (src_id, dst_id), record in merged.items():
        reverse = merged.get((dst_id, src_id))
        if reverse is not None and reverse["way"] == record["way"]:
            left, right = (src_id, dst_id) if src_id < dst_id else (dst_id, src_id)
            record["road_id"] = f"osm-{left}-{right}-{record['way']}"
        else:
            record["road_id"] = f"osm-{src_id}-{dst_id}-{record['way']}-{record['key']}"

    name_counts: dict[int, Counter] = {}
    for (src_id, dst_id), record in merged.items():
        name_counts.setdefault(src_id, Counter())[record["name"]] += 1
        name_counts.setdefault(dst_id, Counter())[record["name"]] += 1

    used = {node for pair in merged for node in pair}
    nodes = []
    for osmid, data in graph.nodes(data=True):
        node_id = int(osmid)
        if node_id not in used:
            continue
        lat = round(float(data["y"]), 7)
        lon = round(float(data["x"]), 7)
        street = name_counts.get(node_id, Counter()).most_common(1)
        nodes.append(
            {
                "osmid": node_id,
                "name": street[0][0] if street else f"n{node_id}",
                "lat": lat,
                "lon": lon,
                "district": assign_district(lat, lon),
                "role": "junction",
            }
        )
    nodes.sort(key=lambda node: node["osmid"])

    edges = []
    for record in merged.values():
        edges.append(
            {
                "src": record["src"],
                "dst": record["dst"],
                "road_id": record["road_id"],
                "name": record["name"],
                "osm_way": record["osm_way"],
                "highway": record["highway"],
                "length_m": record["length_m"],
                "speed_kph": record["speed_kph"],
                "travel_s": record["travel_s"],
                "capacity": int(record["capacity"]),
            }
        )
    edges.sort(key=lambda edge: (edge["road_id"], edge["src"], edge["dst"]))

    present = {node["district"] for node in nodes}
    districts = [
        {
            "name": spec.name,
            "population": spec.population,
            "zone_id": spec.zone_id,
            "population_source": "synthetic",
        }
        for spec in DISTRICTS
        if spec.name in present
    ]
    return {
        "source": "osm",
        "attribution": "© OpenStreetMap contributors",
        "license": "ODbL-1.0",
        "tool": "osmnx",
        "network_type": "drive",
        "bbox": bbox_dict(),
        "district_method": "nearest-centroid",
        "nodes": nodes,
        "edges": edges,
        "districts": districts,
    }


def snap_facility(nodes: list[dict], spec: FacilitySpec) -> dict:
    best = min(nodes, key=lambda node: haversine_m(spec.lat, spec.lon, node["lat"], node["lon"]))
    dist_m = haversine_m(spec.lat, spec.lon, best["lat"], best["lon"])
    return {"spec": spec, "osmid": best["osmid"], "dist_m": dist_m, "node_name": best["name"]}


def apply_facility_labels(prepared: dict, snaps: list[dict]) -> None:
    """Name hospital and shelter intersections after the facility, for route text."""
    by_id = {node["osmid"]: node for node in prepared["nodes"]}
    for snap in snaps:
        node = by_id[snap["osmid"]]
        kind = snap["spec"].kind
        if kind == "hospital":
            node["role"] = "hospital"
            node["name"] = snap["spec"].name
        elif kind == "shelter" and node["role"] == "junction":
            node["role"] = "shelter"
            node["name"] = snap["spec"].name
        elif kind == "unit" and node["role"] == "junction":
            node["role"] = "depot"


def write_extract(prepared: dict, path: Path = EXTRACT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prepared, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def read_extract(path: Path = EXTRACT_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_drive_graph(refresh: bool = False):
    """Download the bbox with OSMnx, or reuse the on-disk extract.

    Returns (prepared_dict, origin) where origin is 'extract' or 'overpass'.
    """
    if EXTRACT_PATH.is_file() and not refresh:
        return read_extract(), "extract"

    import osmnx as ox

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(CACHE_DIR)
    ox.settings.requests_timeout = 180
    graph = ox.graph_from_bbox(BBOX, network_type="drive", simplify=True, retain_all=False)
    graph = ox.add_edge_speeds(graph, fallback=FALLBACK_SPEED_KPH)
    graph = ox.add_edge_travel_times(graph)
    for _src, _dst, _key, data in graph.edges(keys=True, data=True):
        data.pop("geometry", None)
    prepared = prepare_drive_graph(graph)
    try:
        prepared["osmnx_version"] = ox.__version__
    except Exception:
        prepared["osmnx_version"] = ""
    write_extract(prepared)
    return prepared, "overpass"
