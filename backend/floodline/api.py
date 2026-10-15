"""FastAPI surface for the map. State lives in FalkorDB, not in this process."""

from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from floodline.admin_auth import require_admin
from floodline.analysis import cut_off_areas, evac_capacity, prove_cutoff, record_corridor
from floodline.area import (
    CITIES,
    DEFAULT_RADIUS_M,
    MAX_RADIUS_M,
    MIN_RADIUS_M,
    allowed_graph_id,
    get_area_job,
    load_bundled_city,
    load_valencia_demo,
    start_area_job,
)
from floodline.config import Settings
from floodline.db import get_graph
from floodline.escape import cutoff_zones, note_reroute, overlay_payload, route_payload
from floodline.events import collect_events, format_sse
from floodline.network import DEMO_REPORT
from floodline.osm_demo import load_osm_graph
from floodline.scenario import file_report, reopen_roads, reset_scenario, snapshot

app = FastAPI(title="Floodline", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ReportIn(BaseModel):
    text: str = Field(default=DEMO_REPORT, min_length=1)
    graph: str | None = None


class ReopenIn(BaseModel):
    road_id: str | None = Field(default=None, max_length=80)
    graph: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    clear_test: bool = False


class AreaIn(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    radius_m: int = Field(default=DEFAULT_RADIUS_M, ge=MIN_RADIUS_M, le=MAX_RADIUS_M)


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _settings_for(graph: str | None) -> Settings:
    base = Settings.from_env()
    if graph is None or graph == "":
        return base
    if not allowed_graph_id(graph, default=base.graph):
        raise HTTPException(status_code=400, detail="Unknown graph id")
    return base.for_graph(graph)


def _guard(fn):
    try:
        return fn()
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/health")
def health() -> dict:
    cfg = Settings.from_env()
    try:
        state = snapshot(cfg)
        return {"ok": True, "graph": cfg.graph, "loaded": bool(state.get("loaded"))}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/state")
def state() -> dict:
    return _guard(lambda: snapshot())


@app.post("/api/scenario/reset")
def reset() -> dict:
    return _guard(reset_scenario)


@app.post("/api/scenario/report")
def report(body: ReportIn, graph: str | None = Query(default=None)) -> dict:
    cfg = _settings_for(graph or body.graph)

    def run():
        state = file_report(body.text, cfg)
        closed = state.get("closed_roads") or []
        road_id = closed[-1] if closed else ""
        if road_id:
            state["citizen_reroute"] = note_reroute(road_id, cfg)
        return state

    return _guard(run)


@app.post("/api/road/reopen")
def road_reopen(body: ReopenIn, graph: str | None = Query(default=None)) -> dict:
    """Mark a closed road clear. Restores travel_s, recreates PASSABLE, writes a Decision.

    No admin token. ``clear_test`` reopens every closure on the graph. A lat/lon
    reopens closures within 800 m. The Decision shows up on the SSE feed.
    """
    cfg = _settings_for(graph or body.graph)
    return _guard(
        lambda: reopen_roads(
            road_id=body.road_id,
            lat=body.lat,
            lon=body.lon,
            clear_test=body.clear_test,
            settings=cfg,
        )
    )


@app.post("/api/scenario/corridor")
def corridor() -> dict:
    """Close the roads leaving Paiporta, then return the map snapshot."""

    def run():
        graph = get_graph()
        result = prove_cutoff(graph)
        record_corridor(graph, result)
        state = snapshot()
        critical = result["before"]["evac"]["critical_edge"]
        state["corridor"] = {
            "district": result["district"],
            "before_max_flow": result["before"]["evac"]["max_flow"],
            "after_max_flow": result["after"]["evac"]["max_flow"],
            "critical_edge": None if critical is None else critical["name"],
            "critical_flow": None if critical is None else critical["flow"],
            "closed": [item["name"] for item in result["closed"]],
        }
        return state

    return _guard(run)


@app.get("/api/analysis/cutoff")
def cutoff() -> list:
    return _guard(lambda: cut_off_areas(get_graph()))


@app.get("/api/analysis/evac")
def evac(district: str = "Paiporta") -> dict:
    return _guard(lambda: evac_capacity(get_graph(), district))


@app.post("/api/admin/load-valencia")
def load_valencia(
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None),
) -> dict:
    """Load the committed Valencia extract into FalkorDB.

    Railway's service root is ``backend/``, which is where
    ``data/osm/valencia_corridor.json`` lives. Set ``ADMIN_TOKEN`` on that
    service. The body is unused; refresh-from-Overpass is intentionally not
    exposed here.
    """
    require_admin(authorization, x_admin_token)

    def run():
        settings = Settings.from_env()
        loaded = load_osm_graph(settings, refresh=False)
        stats = loaded["loaded"]
        return {
            "ok": True,
            "origin": loaded["origin"],
            "graph": settings.graph,
            "falkordb": loaded["version"],
            "intersections": stats["intersections"],
            "road_edges": stats["road_edges"],
            "passable": stats["passable"],
        }

    return _guard(run)


@app.post("/api/area/load")
def area_load(body: AreaIn, request: Request) -> dict:
    """Queue a drive-network load. Returns immediately. No admin token.

    Poll ``GET /api/area/status``. A repeated load of the same rounded tile
    is served from FalkorDB by the worker. Overpass work does not run in this
    request.
    """
    return start_area_job(
        body.lat,
        body.lon,
        body.radius_m,
        client=_client_key(request),
    )


@app.get("/api/area/status")
def area_status(job_id: str = Query(..., min_length=8, max_length=64)) -> dict:
    """queued, fetching, indexing, ready, or failed, plus a short message."""
    job = get_area_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown area job")
    return job


@app.post("/api/area/valencia")
def area_valencia() -> dict:
    """One-tap demo. Loads the bundled extract into the ``valencia`` graph.

    Does not call Overpass and does not require ``ADMIN_TOKEN``. A warm graph
    is left in place.
    """
    return _guard(load_valencia_demo)


@app.post("/api/area/city/{slug}")
def area_city(slug: str) -> dict:
    """Load a bundled city (valencia, mumbai, nyc). Same cache rules as Valencia.

    Does not call Overpass and does not require ``ADMIN_TOKEN``.
    """
    if slug not in CITIES:
        raise HTTPException(status_code=404, detail="Unknown city")
    return _guard(lambda: load_bundled_city(slug))


@app.get("/api/route")
def citizen_route(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    graph: str | None = Query(default=None),
    dest: str | None = Query(default=None, max_length=80),
) -> dict:
    """GeoJSON line, turn steps, and ETA. Shelter choice is the Sahil stub."""
    cfg = _settings_for(graph)
    return _guard(lambda: route_payload(lat, lon, cfg, dest_id=dest))


@app.get("/api/escape/overlay")
def escape_overlay(graph: str | None = Query(default=None)) -> dict:
    cfg = _settings_for(graph)
    return _guard(lambda: overlay_payload(cfg))


@app.get("/api/escape/zones")
def escape_zones(graph: str | None = Query(default=None)) -> dict:
    """Flood-vision shades. WCC-to-shelter is the Sahil stub."""
    cfg = _settings_for(graph)
    return _guard(lambda: cutoff_zones(get_graph(cfg)))


@app.get("/api/feed")
def feed(since: int = 0, graph: str | None = Query(default=None)) -> dict:
    """JSON twin of the SSE stream, for clients that have to poll."""
    cfg = _settings_for(graph)

    def run():
        events = collect_events(get_graph(cfg), since)
        if len(events) > 40:
            events = events[-40:]
        cursor = since
        if events:
            cursor = max(cursor, max(int(item["seq"]) for item in events))
        return {"events": events, "cursor": cursor}

    return _guard(run)


def _pull_events(since: int, settings: Settings | None = None) -> list[dict]:
    return collect_events(get_graph(settings), since)


@app.get("/api/stream")
async def stream(once: bool = False, graph: str | None = Query(default=None)) -> StreamingResponse:
    """Server-sent Task, Decision, and Route events. Heartbeat every 15 s."""
    cfg = _settings_for(graph)

    async def generate():
        since = 0
        primed = False
        last_beat = time.monotonic()
        while True:
            try:
                batch = await asyncio.to_thread(_pull_events, since, cfg)
            except Exception as exc:
                yield format_sse("error", {"detail": str(exc)})
                batch = []
            emit = batch
            if batch and not primed and len(batch) > 40:
                emit = batch[-40:]
            send_beat = (not primed) or once or (time.monotonic() - last_beat >= 15)
            if batch:
                since = max(since, max(int(item["seq"]) for item in batch))
            primed = True
            for item in emit:
                yield format_sse(item["type"], item)
            if send_beat:
                yield ": heartbeat\n\n"
                yield format_sse("heartbeat", {"ok": True})
                last_beat = time.monotonic()
            if once:
                break
            await asyncio.sleep(0.8)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
