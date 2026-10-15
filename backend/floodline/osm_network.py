"""Valencia flood-corridor drive network, prepared from OpenStreetMap.

The bbox is the L'Horta Sud towns that flooded in October 2024 (Paiporta,
Alfafar, Sedaví, Benetússer) plus Hospital Doctor Peset. It is about 5.0 km
east-west by 4.8 km north-south: large enough for a real detour, small enough
to load on a laptop. District membership is the nearest town centroid, not an
official boundary. Populations, beds, shelters, and the ambulance are synthetic.
"""

from __future__ import annotations

import json
import threading
import time
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


def prepare_drive_graph(graph, *, bbox: dict | None = None, district_name: str | None = None) -> dict:
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
                "district": district_name or assign_district(lat, lon),
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

    if district_name:
        districts = [
            {
                "name": district_name,
                "population": 0,
                "zone_id": "area",
                "population_source": "unknown",
            }
        ]
        district_method = "area"
    else:
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
        district_method = "nearest-centroid"
    return {
        "source": "osm",
        "attribution": "© OpenStreetMap contributors",
        "license": "ODbL-1.0",
        "tool": "osmnx",
        "network_type": "drive",
        "bbox": bbox if bbox is not None else bbox_dict(),
        "district_method": district_method,
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


# Public Overpass endpoints, tried in this order. A failure moves to the next
# mirror. The whole sequence stays inside AREA_OVERPASS_BUDGET_S so the map's
# 45s stall still fires if every mirror hangs. Nothing here writes an extract.
USER_AGENT = "Floodline/1.0 (https://github.com/zaprillcom-cpu/Floodline)"
OVERPASS_MIRRORS = (
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
    "https://overpass.private.coffee/api",
    "https://maps.mail.ru/osm/tools/overpass/api",
)
AREA_OVERPASS_TIMEOUT_S = 20
AREA_OVERPASS_BUDGET_S = 40
# Short pauses between failures. They grow once, and the last mirror is not paused.
_MIRROR_BACKOFF_S = (0.3, 0.6, 0.9)

# Provisional shelters until Sahil's model lands: amenity=school|community_centre|social_facility
# and leisure=sports_centre. See shelter_overpass_query.
SHELTER_CAP = 8


def parse_bbox(value) -> dict | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return None
    if not isinstance(value, dict):
        return None
    try:
        return {key: float(value[key]) for key in ("west", "south", "east", "north")}
    except Exception:
        return None


def call_with_deadline(fn, timeout_s: float):
    """Run ``fn`` on a daemon thread and stop waiting after ``timeout_s``.

    OSMnx's Overpass client can sleep on the status endpoint with no overall
    deadline. This bound is the one the area loader actually honors.
    """
    if timeout_s <= 0:
        raise TimeoutError("Overpass exceeded 0s")
    box: dict = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except Exception as exc:
            box["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise TimeoutError(f"Overpass exceeded {timeout_s}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def highway_overpass_query(bbox: tuple[float, float, float, float], timeout_s: int) -> str:
    """One ways-and-nodes query for streets inside the bbox.

    ``highway=*`` keeps the request small. Paths that are not streets are left
    out so a dense city does not pull every footway into FalkorDB.
    """
    west, south, east, north = bbox
    skip = (
        "abandoned|bus_stop|construction|corridor|cycleway|elevator|escalator|"
        "footway|path|pedestrian|planned|platform|proposed|raceway|steps|track"
    )
    return (
        f"[out:json][timeout:{int(timeout_s)}];"
        f'(way["highway"]["highway"!~"{skip}"]["area"!~"yes"]({south},{west},{north},{east});>;);'
        "out body;"
    )


def shelter_overpass_query(bbox: tuple[float, float, float, float], timeout_s: int) -> str:
    west, south, east, north = bbox
    box = f"({south},{west},{north},{east})"
    return (
        f"[out:json][timeout:{int(timeout_s)}];"
        "("
        f'node["amenity"~"school|community_centre|social_facility"]{box};'
        f'node["leisure"="sports_centre"]{box};'
        f'way["amenity"~"school|community_centre|social_facility"]{box};'
        f'way["leisure"="sports_centre"]{box};'
        ");"
        "out center;"
    )


def mirror_attempt_plan(
    urls: tuple[str, ...] = OVERPASS_MIRRORS,
    timeout_s: float = AREA_OVERPASS_TIMEOUT_S,
    budget_s: float = AREA_OVERPASS_BUDGET_S,
) -> list[tuple[str, float, float]]:
    """Share ``budget_s`` across mirrors.

    Each item is ``(url, slot_seconds, pause_after_failure)``. Slots are equal
    and never longer than ``timeout_s``. The pauses are the backoff after a
    failed attempt; the last mirror has none. Worst case is the budget plus
    those short pauses, which stays under the UI's 45s stall.
    """
    mirrors = tuple(urls)
    if not mirrors or budget_s <= 0 or timeout_s <= 0:
        return []
    slot = min(float(timeout_s), float(budget_s) / len(mirrors))
    if slot <= 0:
        return []
    plan = []
    for index, url in enumerate(mirrors):
        pause = 0.0
        if index + 1 < len(mirrors):
            pause = _MIRROR_BACKOFF_S[min(index, len(_MIRROR_BACKOFF_S) - 1)]
        plan.append((url, slot, pause))
    return plan


def _post_overpass(url: str, query: str, timeout_s: float) -> dict:
    import requests

    endpoint = url.rstrip("/") + "/interpreter"
    response = requests.post(
        endpoint,
        data={"data": query},
        timeout=timeout_s,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Overpass returned an unexpected body")
    if payload.get("remark") and not payload.get("elements"):
        raise RuntimeError(str(payload["remark"])[:240])
    return payload


def _graph_from_overpass(payload: dict):
    import networkx as nx
    import osmnx as ox
    from osmnx.graph import _create_graph
    from osmnx.simplification import simplify_graph
    from osmnx.truncate import largest_component

    elements = payload.get("elements") or []
    if not elements:
        return nx.MultiDiGraph()
    graph = _create_graph([payload], bidirectional=False)
    if graph.number_of_edges() == 0:
        return graph
    try:
        graph = simplify_graph(graph)
    except Exception:
        pass
    try:
        graph = largest_component(graph, strongly=False)
    except Exception:
        pass
    graph = ox.add_edge_speeds(graph, fallback=FALLBACK_SPEED_KPH)
    graph = ox.add_edge_travel_times(graph)
    for _src, _dst, _key, data in graph.edges(keys=True, data=True):
        data.pop("geometry", None)
    return graph


def _download_drive(bbox: tuple[float, float, float, float], url: str, timeout_s: int):
    """Direct Overpass POST. Does not use OSMnx's status-endpoint pause."""
    payload = _post_overpass(url, highway_overpass_query(bbox, timeout_s), timeout_s)
    return _graph_from_overpass(payload)


def fetch_area_drive_graph(
    bbox: tuple[float, float, float, float],
    *,
    timeout_s: float = AREA_OVERPASS_TIMEOUT_S,
    budget_s: float = AREA_OVERPASS_BUDGET_S,
    urls: tuple[str, ...] = OVERPASS_MIRRORS,
    download=None,
) -> tuple[dict, str]:
    """Download a drive network for an arbitrary bbox. Does not write a bundled extract.

    Mirrors are tried in order inside ``budget_s``. A failure backs off, then
    the next mirror is used. ``download(bbox, url, timeout_s)`` exists so tests
    can fail a mirror without calling Overpass.
    """
    fetch = download or _download_drive
    box = {"west": bbox[0], "south": bbox[1], "east": bbox[2], "north": bbox[3]}
    errors: list[str] = []
    plan = mirror_attempt_plan(urls, timeout_s, budget_s)
    for index, (url, slot, pause) in enumerate(plan):
        try:
            graph = call_with_deadline(lambda url=url, slot=slot: fetch(bbox, url, slot), slot)
            prepared = prepare_drive_graph(graph, bbox=box, district_name="here")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            if index + 1 < len(plan) and pause > 0:
                time.sleep(pause)
            continue
        prepared["overpass_url"] = url
        if not prepared["edges"]:
            errors.append(f"{url}: no driveable streets")
            if index + 1 < len(plan) and pause > 0:
                time.sleep(pause)
            continue
        return prepared, url
    detail = "; ".join(errors) if errors else "no mirror responded"
    raise RuntimeError(detail)


def _label(value) -> str:
    if value is None:
        return ""
    try:
        if value != value:  # NaN
            return ""
    except Exception:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    return text


def records_from_features(frame) -> list[dict]:
    """Turn an OSMnx GeoDataFrame into plain shelter records. Empty if it is missing."""
    if frame is None or getattr(frame, "empty", True):
        return []
    records = []
    for idx, row in frame.iterrows():
        geom = getattr(row, "geometry", None)
        if geom is None or getattr(geom, "is_empty", True):
            continue
        point = geom if getattr(geom, "geom_type", "") == "Point" else geom.centroid
        if isinstance(idx, tuple) and len(idx) >= 2:
            ident = f"osm-{idx[0]}-{idx[1]}"
        else:
            ident = f"osm-{idx}"
        amenity = _label(row.get("amenity") if hasattr(row, "get") else None)
        leisure = _label(row.get("leisure") if hasattr(row, "get") else None)
        kind = amenity or leisure or "shelter"
        name = _label(row.get("name") if hasattr(row, "get") else None) or kind.replace("_", " ").title()
        records.append(
            {
                "id": ident,
                "name": name[:120],
                "kind": kind[:40],
                "lat": float(point.y),
                "lon": float(point.x),
            }
        )
    return records


def nearest_shelters(records: list[dict], lat: float, lon: float, limit: int = SHELTER_CAP) -> list[dict]:
    """Keep the closest provisional shelters. Farther ones are dropped so routing stays small."""
    scored = []
    for record in records:
        try:
            slat = float(record["lat"])
            slon = float(record["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        scored.append((haversine_m(lat, lon, slat, slon), record, slat, slon))
    scored.sort(key=lambda item: item[0])
    chosen = []
    seen = set()
    for dist, record, slat, slon in scored:
        key = str(record.get("id") or f"{slat:.5f},{slon:.5f}")
        if key in seen:
            continue
        seen.add(key)
        chosen.append(
            {
                "id": key,
                "name": str(record.get("name") or "Shelter")[:120],
                "kind": str(record.get("kind") or "shelter")[:40],
                "lat": slat,
                "lon": slon,
                "dist_m": int(dist),
            }
        )
        if len(chosen) >= limit:
            break
    return chosen


def shelters_from_overpass(payload: dict) -> list[dict]:
    records = []
    for element in payload.get("elements") or []:
        if "lat" in element and "lon" in element:
            slat, slon = element["lat"], element["lon"]
        elif isinstance(element.get("center"), dict):
            slat = element["center"].get("lat")
            slon = element["center"].get("lon")
        else:
            continue
        if slat is None or slon is None:
            continue
        tags = element.get("tags") or {}
        kind = _label(tags.get("amenity")) or _label(tags.get("leisure")) or "shelter"
        name = _label(tags.get("name")) or kind.replace("_", " ").title()
        records.append(
            {
                "id": f"osm-{element.get('type', 'n')}-{element.get('id', len(records))}",
                "name": name[:120],
                "kind": str(kind)[:40],
                "lat": float(slat),
                "lon": float(slon),
            }
        )
    return records


def _download_features(bbox: tuple[float, float, float, float], url: str, timeout_s: int):
    return _post_overpass(url, shelter_overpass_query(bbox, timeout_s), timeout_s)


def fetch_shelter_candidates(
    bbox: tuple[float, float, float, float],
    lat: float,
    lon: float,
    *,
    timeout_s: float = AREA_OVERPASS_TIMEOUT_S,
    budget_s: float = AREA_OVERPASS_BUDGET_S,
    urls: tuple[str, ...] = OVERPASS_MIRRORS,
    download=None,
    limit: int = SHELTER_CAP,
) -> list[dict]:
    """Schools, community centres, social facilities, and sports centres near the citizen.

    Mirrors share the same budget as the street download. If every attempt
    fails, the street graph still loads and this returns an empty list.
    """
    fetch = download or _download_features
    plan = mirror_attempt_plan(urls, timeout_s, budget_s)
    for index, (url, slot, pause) in enumerate(plan):
        try:
            payload = call_with_deadline(lambda url=url, slot=slot: fetch(bbox, url, slot), slot)
        except Exception:
            if index + 1 < len(plan) and pause > 0:
                time.sleep(pause)
            continue
        if isinstance(payload, dict) and "elements" in payload:
            records = shelters_from_overpass(payload)
        else:
            records = records_from_features(payload)
        return nearest_shelters(records, lat, lon, limit)
    return []
