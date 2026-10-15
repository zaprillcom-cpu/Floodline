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

from floodline.config import Settings, T_CLOSE
from floodline.db import get_graph, rows
from floodline.network import haversine_m
from floodline.osm_demo import load_osm_graph
from floodline.osm_network import (
    bbox_dict,
    fetch_area_drive_graph,
    fetch_shelter_candidates,
    parse_bbox,
)

TILE_DEG = 0.05
VALENCIA_GRAPH = "valencia"
GRAPH_ID_RE = re.compile(r"^fl-[pn]\d{4}-[pn]\d{5}$")
DEFAULT_RADIUS_M = 2000
MAX_RADIUS_M = 5000
MIN_RADIUS_M = 50
RATE_LIMIT = 4
RATE_WINDOW_S = 60.0

OVERPASS_MESSAGE = (
    "Overpass could not download streets for this area. "
    "Open the Valencia demo, which uses the bundled extract."
)
RATE_MESSAGE = (
    "Too many street downloads from this address. "
    "Wait a minute, or open the Valencia demo."
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
    if name == default or name == VALENCIA_GRAPH:
        return True
    return bool(GRAPH_ID_RE.fullmatch(name))


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


def persist_area(settings: Settings, prepared: dict, candidates: list[dict], *, origin: str) -> dict:
    """Delete the tile graph, bulk-load it, build CCH, and hand shelters to the stub."""
    from floodline.bulk import load_records
    from floodline.db import connect
    from floodline.stubs import ensure_shelter_citizen_model

    db = connect(settings, socket_timeout=180)
    graph = db.select_graph(settings.graph)
    try:
        graph.delete()
    except Exception:
        pass
    graph = db.select_graph(settings.graph)
    loaded = load_records(graph, prepared)
    snapped = snap_shelters(prepared["nodes"], candidates)
    model = ensure_shelter_citizen_model(graph, snapped)
    for name, role in (("Scout", "closures"), ("Dispatcher", "routing")):
        graph.query(
            "CREATE (:Agent {name: $name, role: $role, heartbeat_at: $at})",
            {"name": name, "role": role, "at": T_CLOSE},
        )
    provisional = model.get("provisional") or {}
    return {
        "ok": True,
        "graph": settings.graph,
        "cached": False,
        "origin": origin,
        "intersections": loaded["intersections"],
        "road_edges": loaded["road_edges"],
        "passable": loaded["passable"],
        "shelters": int(provisional.get("attached") or 0),
        "bbox": prepared.get("bbox") or bbox_dict(),
        "cch": loaded.get("cch"),
        "shelter_model": model.get("stub"),
    }


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
    if area_is_cached(cfg):
        _report(on_status, "indexing", "Using streets already loaded for this area.")
        return read_cached_area(cfg)
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
