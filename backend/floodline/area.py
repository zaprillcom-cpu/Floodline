"""Load the drive network around a citizen, one FalkorDB graph per tile.

Repeat loads of the same rounded lat/lon skip Overpass. The Valencia demo
reads the committed extract and never calls Overpass.
"""

from __future__ import annotations

import math
import re
import threading
import time
import uuid
from collections import deque

from floodline.config import Settings, utc_now
from floodline.db import get_graph, rows
from floodline.network import haversine_m
from floodline.osm_demo import load_osm_graph
from floodline.osm_network import (
    BACKEND_ROOT,
    EXTRACT_PATH,
    FacilitySpec,
    apply_facility_labels,
    bbox_dict,
    fetch_area_drive_graph,
    fetch_shelter_candidates,
    parse_bbox,
    read_extract,
    snap_facility,
)
from floodline.regions import crop_tile, region_by_slug, region_containing

TILE_DEG = 0.05
VALENCIA_GRAPH = "valencia"
GRAPH_ID_RE = re.compile(r"^fl-[pn]\d{4}-[pn]\d{5}$")

# City picker metadata. Valencia's narrative demo still uses the corridor JSON.
# Mumbai and New York centres are the offline-region centres (Goregaon, midtown).
# Street data for those cities is the SQLite region, cropped per tile in load_citizen_area.
CITIES: dict[str, dict] = {
    "valencia": {
        "graph": "valencia",
        "place": "Valencia",
        "lat": 39.4294,
        "lon": -0.4183,
        "path": EXTRACT_PATH,
        "callsign": "A-7",
    },
    "mumbai": {
        "graph": "mumbai",
        "place": "Mumbai",
        "lat": 19.1663,
        "lon": 72.8526,
        "path": BACKEND_ROOT / "data" / "regions" / "mumbai.sqlite",
        "callsign": "M-1",
    },
    "nyc": {
        "graph": "nyc",
        "place": "New York",
        "lat": 40.758,
        "lon": -73.9855,
        "path": BACKEND_ROOT / "data" / "regions" / "manhattan.sqlite",
        "callsign": "NY-1",
    },
}
CITY_GRAPHS = frozenset(spec["graph"] for spec in CITIES.values())
DEFAULT_RADIUS_M = 2000
MAX_RADIUS_M = 5000
MIN_RADIUS_M = 50
RATE_LIMIT = 4
RATE_WINDOW_S = 60.0

OVERPASS_MESSAGE = (
    "Overpass could not download streets for this area. "
    "Choose a bundled city, which uses an offline extract."
)
RATE_MESSAGE = (
    "Too many street downloads from this address. "
    "Wait a minute, or choose a bundled city."
)


class OverpassFailed(RuntimeError):
    pass


class RateLimited(RuntimeError):
    pass


_hits: dict[str, deque[float]] = {}
_rate_lock = threading.Lock()
_jobs_lock = threading.Lock()
_jobs: dict[str, dict] = {}
_job_order: list[str] = []
_JOB_LIMIT = 80


def reset_rate_limit() -> None:
    with _rate_lock:
        _hits.clear()


def reset_area_jobs() -> None:
    with _jobs_lock:
        _jobs.clear()
        _job_order.clear()


def enforce_rate_limit(client: str, *, now: float | None = None) -> None:
    """Four uncached Overpass loads per minute per client. Cache hits do not call this."""
    clock = time.monotonic() if now is None else now
    key = client or "unknown"
    with _rate_lock:
        window = _hits.setdefault(key, deque())
        while window and clock - window[0] >= RATE_WINDOW_S:
            window.popleft()
        if len(window) >= RATE_LIMIT:
            raise RateLimited(RATE_MESSAGE)
        window.append(clock)


def snap_tile(value: float, step: float = TILE_DEG) -> float:
    """Nearest tile centre. Half-steps go away from zero so the id is stable."""
    scaled = value / step
    snapped = math.floor(scaled + 0.5)
    return round(snapped * step, 5)


def _axis(value: float, width: int) -> str:
    sign = "n" if value < 0 else "p"
    hundredths = int(round(abs(value) * 100))
    return f"{sign}{hundredths:0{width}d}"


def tile_graph_id(lat: float, lon: float) -> str:
    """Graph name for the 0.05° tile that contains this point.

    39.45, -0.40 → ``fl-p3945-n00040``.
    """
    return f"fl-{_axis(snap_tile(lat), 4)}-{_axis(snap_tile(lon), 5)}"


def allowed_graph_id(name: str, *, default: str) -> bool:
    if name == default or name in CITY_GRAPHS:
        return True
    return bool(GRAPH_ID_RE.fullmatch(name))


class UnknownCity(KeyError):
    pass


def city_spec(slug: str) -> dict:
    spec = CITIES.get(slug)
    if spec is None:
        raise UnknownCity(slug)
    return spec


def radius_bbox(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """West, south, east, north. Longitude shrinks with latitude."""
    dlat = radius_m / 111_320
    cos_lat = math.cos(math.radians(lat))
    dlon = radius_m / (111_320 * cos_lat) if abs(cos_lat) > 0.01 else dlat
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def area_is_cached(settings: Settings) -> bool:
    try:
        graph = get_graph(settings)
        found = rows(graph.query("MATCH (i:Intersection) RETURN count(i) AS n"))
    except Exception:
        return False
    return bool(found and int(found[0][0]) > 0)


def _counts(graph) -> dict:
    intersections = int(rows(graph.query("MATCH (i:Intersection) RETURN count(i)"))[0][0])
    road_edges = int(rows(graph.query("MATCH ()-[r:ROAD]->() RETURN count(r)"))[0][0])
    passable = int(rows(graph.query("MATCH ()-[p:PASSABLE]->() RETURN count(p)"))[0][0])
    shelters = int(rows(graph.query("MATCH (s:Shelter) RETURN count(s)"))[0][0])
    return {
        "intersections": intersections,
        "road_edges": road_edges,
        "passable": passable,
        "shelters": shelters,
    }


def _stored_bbox(graph) -> dict:
    found = rows(graph.query("MATCH (m:Meta {key: 'network'}) RETURN m.bbox"))
    if found:
        parsed = parse_bbox(found[0][0])
        if parsed:
            return parsed
    return bbox_dict()


def read_cached_area(settings: Settings) -> dict:
    graph = get_graph(settings)
    counts = _counts(graph)
    return {
        "ok": True,
        "graph": settings.graph,
        "cached": True,
        "origin": "cache",
        **counts,
        "bbox": _stored_bbox(graph),
    }


def snap_shelters(nodes: list[dict], candidates: list[dict]) -> list[dict]:
    if not nodes or not candidates:
        return []
    snapped = []
    for item in candidates:
        best = min(nodes, key=lambda node: haversine_m(item["lat"], item["lon"], node["lat"], node["lon"]))
        dist = haversine_m(item["lat"], item["lon"], best["lat"], best["lon"])
        snapped.append(
            {
                "id": str(item.get("id") or f"shelter-{best['osmid']}"),
                "name": item.get("name") or "Shelter",
                "kind": item.get("kind") or "shelter",
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
                "osmid": int(best["osmid"]),
                "dist_m": int(dist),
            }
        )
    return snapped


def persist_area(
    settings: Settings,
    prepared: dict,
    candidates: list[dict],
    *,
    origin: str,
    hospitals: list[dict] | None = None,
    unit: dict | None = None,
) -> dict:
    """Delete the tile graph, bulk-load it, build CCH, and write Shelter-NEAR."""
    from floodline.bulk import attach_facilities, load_records
    from floodline.db import connect
    from floodline.stubs import ensure_shelter_citizen_model

    facilities: list[FacilitySpec] = []
    for index, item in enumerate(hospitals or []):
        facilities.append(
            FacilitySpec(
                "hospital",
                str(item.get("id") or f"hospital-{index}")[:80],
                str(item.get("name") or "Hospital")[:120],
                float(item["lat"]),
                float(item["lon"]),
                beds_total=40,
                beds_free=12,
            )
        )
    if unit is not None:
        facilities.append(
            FacilitySpec(
                "unit",
                str(unit.get("id") or "unit")[:80],
                str(unit.get("callsign") or "A-7"),
                float(unit["lat"]),
                float(unit["lon"]),
                callsign=str(unit.get("callsign") or "A-7"),
            )
        )
    snaps = [snap_facility(prepared["nodes"], spec) for spec in facilities]
    if snaps:
        apply_facility_labels(prepared, snaps)
    db = connect(settings, socket_timeout=180)
    graph = db.select_graph(settings.graph)
    try:
        graph.delete()
    except Exception:
        pass
    graph = db.select_graph(settings.graph)
    loaded = load_records(graph, prepared)
    if snaps:
        attach_facilities(graph, snaps)
    snapped = snap_shelters(prepared["nodes"], candidates)
    model = ensure_shelter_citizen_model(graph, snapped)
    landmarks_written = _index_tile_landmarks(graph, prepared, candidates, hospitals or [])
    now = utc_now()
    for name, role in (("Scout", "closures"), ("Dispatcher", "routing"), ("Analyst", "connectivity")):
        graph.query(
            """
            MERGE (a:Agent {name: $name})
            SET a.role = $role, a.heartbeat_at = $at
            """,
            {"name": name, "role": role, "at": now},
        )
    analyst = _run_analyst(graph, now)
    return {
        "ok": True,
        "graph": settings.graph,
        "cached": False,
        "origin": origin,
        "intersections": loaded["intersections"],
        "road_edges": loaded["road_edges"],
        "passable": loaded["passable"],
        "shelters": int(model.get("shelters") or model.get("attached") or 0),
        "bbox": prepared.get("bbox") or bbox_dict(),
        "cch": loaded.get("cch"),
        "shelter_model": "near" if model.get("written") else "unwritten",
        "landmarks": landmarks_written,
        "analyst": analyst.get("task_id"),
    }


def _index_tile_landmarks(graph, prepared: dict, shelters: list[dict], hospitals: list[dict]) -> int:
    """Write Landmark-NEAR from places already on the tile, plus a short POI fetch."""
    try:
        from floodline.landmarks import fetch_poi_landmarks, index_landmarks, landmark_records

        records = list(prepared.get("landmarks") or [])
        if not records:
            records = landmark_records(shelters, hospitals)
            bbox = prepared.get("bbox")
            if isinstance(bbox, dict):
                records.extend(fetch_poi_landmarks(bbox))
        return index_landmarks(graph, records, prepared.get("nodes") or [])
    except Exception:
        return 0


def _report(on_status, status: str, message: str) -> None:
    if on_status is not None:
        on_status(status, message)


def load_citizen_area(
    lat: float,
    lon: float,
    radius_m: int = DEFAULT_RADIUS_M,
    *,
    client: str = "unknown",
    settings: Settings | None = None,
    on_status=None,
) -> dict:
    """Fetch, or reuse, the drive graph for this tile. Rate-limits uncached fetches."""
    if radius_m > MAX_RADIUS_M or radius_m < MIN_RADIUS_M:
        raise ValueError(f"radius_m must be between {MIN_RADIUS_M} and {MAX_RADIUS_M}")
    base = settings or Settings.from_env()
    cfg = base.for_graph(tile_graph_id(lat, lon))
    region = region_containing(lat, lon)
    if area_is_cached(cfg):
        _report(on_status, "indexing", "Using streets already loaded for this area.")
        payload = read_cached_area(cfg)
        payload["center"] = {"lat": lat, "lon": lon}
        if region is not None:
            payload["place"] = region.place
        return payload
    if region is not None:
        _report(on_status, "indexing", "Reading bundled streets.")
        try:
            prepared, shelters, hospitals = crop_tile(region, lat, lon, radius_m)
        except Exception as exc:
            raise OverpassFailed(
                "Bundled streets for this area could not be loaded. Choose another bundled city."
            ) from exc
        _report(on_status, "indexing", "Building the CCH index.")
        payload = persist_area(
            cfg,
            prepared,
            shelters,
            origin="offline",
            hospitals=hospitals,
            unit={
                "id": f"{region.id}-unit",
                "lat": lat,
                "lon": lon,
                "callsign": region.callsign,
            },
        )
        payload["place"] = region.place
        payload["center"] = {"lat": lat, "lon": lon}
        payload["region"] = region.id
        return payload
    enforce_rate_limit(client)
    _report(on_status, "fetching", "Downloading streets from OpenStreetMap.")
    bbox = radius_bbox(lat, lon, radius_m)
    try:
        prepared, mirror = fetch_area_drive_graph(bbox)
    except Exception as exc:
        raise OverpassFailed(OVERPASS_MESSAGE) from exc
    _report(on_status, "indexing", "Building the CCH index.")
    try:
        candidates = fetch_shelter_candidates(bbox, lat, lon, urls=(mirror,))
    except Exception:
        candidates = []
    return persist_area(cfg, prepared, candidates, origin="overpass")


def _update_job(job_id: str, **fields) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(fields)


def _run_area_job(job_id: str, lat: float, lon: float, radius_m: int, client: str) -> None:
    def on_status(status: str, message: str) -> None:
        _update_job(job_id, status=status, message=message)

    try:
        result = load_citizen_area(lat, lon, radius_m, client=client, on_status=on_status)
    except RateLimited as exc:
        _update_job(job_id, status="failed", message=str(exc))
        return
    except OverpassFailed as exc:
        _update_job(job_id, status="failed", message=str(exc))
        return
    except Exception:
        _update_job(
            job_id,
            status="failed",
            message=OVERPASS_MESSAGE,
        )
        return
    _update_job(job_id, status="ready", message="Streets are ready.", **result)


def start_area_job(lat: float, lon: float, radius_m: int, *, client: str) -> dict:
    """Queue an area load and return immediately. The worker updates the job."""
    job_id = uuid.uuid4().hex
    job = {"job_id": job_id, "status": "queued", "message": "Queued."}
    with _jobs_lock:
        _jobs[job_id] = job
        _job_order.append(job_id)
        while len(_job_order) > _JOB_LIMIT:
            _jobs.pop(_job_order.pop(0), None)
        snapshot = dict(job)
    threading.Thread(
        target=_run_area_job,
        args=(job_id, lat, lon, radius_m, client),
        daemon=True,
    ).start()
    return snapshot


def get_area_job(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job is not None else None


def install_city_graph(settings: Settings, prepared: dict, spec: dict) -> dict:
    """Load one bundled extract, with a unit, OSM hospitals, and OSM shelters.

    Hospitals and the unit use ACCESS. Shelter candidates go through
    ``ensure_shelter_citizen_model``, which writes NEAR and ACCESS. No Overpass call.
    """
    from floodline.bulk import attach_facilities, load_records
    from floodline.db import connect
    from floodline.stubs import ensure_shelter_citizen_model

    hospitals = list(prepared.get("hospitals") or [])
    if not hospitals:
        hospitals = [
            {
                "id": f"{spec['graph']}-hospital",
                "name": f"{spec['place']} hospital",
                "lat": spec["lat"],
                "lon": spec["lon"],
            }
        ]
    facilities = []
    for index, item in enumerate(hospitals[:4]):
        facilities.append(
            FacilitySpec(
                "hospital",
                str(item.get("id") or f"hospital-{index}")[:80],
                str(item.get("name") or "Hospital")[:120],
                float(item["lat"]),
                float(item["lon"]),
                beds_total=40,
                beds_free=12,
            )
        )
    facilities.append(
        FacilitySpec(
            "unit",
            f"{spec['graph']}-unit",
            spec["callsign"],
            float(spec["lat"]),
            float(spec["lon"]),
            callsign=spec["callsign"],
        )
    )
    snaps = [snap_facility(prepared["nodes"], item) for item in facilities]
    apply_facility_labels(prepared, snaps)
    db = connect(settings, socket_timeout=180)
    graph = db.select_graph(settings.graph)
    try:
        graph.delete()
    except Exception:
        pass
    graph = db.select_graph(settings.graph)
    loaded = load_records(graph, prepared)
    attach_facilities(graph, snaps)
    candidates = snap_shelters(prepared["nodes"], list(prepared.get("shelters") or []))
    model = ensure_shelter_citizen_model(graph, candidates)
    now = utc_now()
    for name, role in (("Scout", "closures"), ("Dispatcher", "routing"), ("Analyst", "connectivity")):
        graph.query(
            """
            MERGE (a:Agent {name: $name})
            SET a.role = $role, a.heartbeat_at = $at
            """,
            {"name": name, "role": role, "at": now},
        )
    analyst = _run_analyst(graph, now)
    provisional = model.get("provisional") or {}
    center = prepared.get("center") or {"lat": spec["lat"], "lon": spec["lon"]}
    return {
        "ok": True,
        "graph": settings.graph,
        "cached": False,
        "origin": "extract",
        "place": spec["place"],
        "center": {"lat": float(center["lat"]), "lon": float(center["lon"])},
        "intersections": loaded["intersections"],
        "road_edges": loaded["road_edges"],
        "passable": loaded["passable"],
        "shelters": int(model.get("shelters") or provisional.get("attached") or 0),
        "bbox": prepared.get("bbox") or bbox_dict(),
        "cch": loaded.get("cch"),
        "shelter_model": "near" if model.get("written") else "unwritten",
        "analyst": analyst.get("task_id"),
    }


def _run_analyst(graph, at: str) -> dict:
    """Cut-off and evacuation capacity for this tile. A failure does not abort the load."""
    try:
        from floodline.analysis import assess_loaded_area

        return assess_loaded_area(graph, at=at)
    except Exception as exc:
        return {"error": str(exc)}


def _with_place(payload: dict, spec: dict, prepared: dict | None = None) -> dict:
    center = (prepared or {}).get("center") or {"lat": spec["lat"], "lon": spec["lon"]}
    payload["place"] = spec["place"]
    payload["center"] = {"lat": float(center["lat"]), "lon": float(center["lon"])}
    return payload


def load_bundled_city(slug: str, settings: Settings | None = None) -> dict:
    """Load a bundled city the way Valencia does: cache if warm, else the file.

    No Overpass and no admin token. A graph that already has intersections is
    left in place.
    """
    spec = city_spec(slug)
    base = settings or Settings.from_env()
    if slug == "valencia":
        return _with_place(load_valencia_demo(base), spec)
    region = region_by_slug(slug)
    if region is None:
        raise UnknownCity(slug)
    return load_citizen_area(
        region.center_lat,
        region.center_lon,
        2500,
        client="city",
        settings=base,
    )


def load_valencia_demo(settings: Settings | None = None) -> dict:
    """Load the committed extract into the ``valencia`` graph. No Overpass, no admin token.

    A graph that already has intersections is returned as-is so a warm demo is not deleted.
    """
    base = settings or Settings.from_env()
    cfg = base.for_graph(VALENCIA_GRAPH)
    if area_is_cached(cfg):
        payload = read_cached_area(cfg)
        payload["origin"] = "cache"
        return payload
    loaded = load_osm_graph(cfg, refresh=False)
    stats = loaded["loaded"]
    shelters = 0
    by_kind = loaded.get("counts_by_kind") or {}
    if "shelter" in by_kind:
        shelters = int(by_kind["shelter"])
    return {
        "ok": True,
        "graph": VALENCIA_GRAPH,
        "cached": False,
        "origin": loaded.get("origin") or "extract",
        "intersections": stats["intersections"],
        "road_edges": stats["road_edges"],
        "passable": stats["passable"],
        "bbox": bbox_dict(),
        "shelters": shelters,
    }
