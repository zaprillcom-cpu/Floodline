"""FastAPI surface for the map. State lives in FalkorDB, not in this process."""

from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from floodline.admin_auth import require_admin
from floodline.analysis import cut_off_areas, evac_capacity, prove_cutoff, record_corridor
from floodline.config import Settings
from floodline.db import get_graph
from floodline.escape import cutoff_zones, note_reroute, overlay_payload, route_payload
from floodline.events import collect_events, format_sse
from floodline.network import DEMO_REPORT
from floodline.osm_demo import load_osm_graph
from floodline.scenario import file_report, reset_scenario, snapshot

app = FastAPI(title="Floodline", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ReportIn(BaseModel):
    text: str = Field(default=DEMO_REPORT, min_length=1)


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
def report(body: ReportIn) -> dict:
    def run():
        state = file_report(body.text)
        closed = state.get("closed_roads") or []
        road_id = closed[-1] if closed else ""
        if road_id:
            state["citizen_reroute"] = note_reroute(road_id)
        return state

    return _guard(run)


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


@app.get("/api/route")
def citizen_route(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> dict:
    """GeoJSON line, turn steps, and ETA. Shelter choice is the Sahil stub."""
    return _guard(lambda: route_payload(lat, lon))


@app.get("/api/escape/overlay")
def escape_overlay() -> dict:
    return _guard(overlay_payload)


@app.get("/api/escape/zones")
def escape_zones() -> dict:
    """Flood-vision shades. WCC-to-shelter is the Sahil stub."""
    return _guard(lambda: cutoff_zones(get_graph()))


@app.get("/api/feed")
def feed(since: int = 0) -> dict:
    """JSON twin of the SSE stream, for clients that have to poll."""

    def run():
        events = collect_events(get_graph(), since)
        if len(events) > 40:
            events = events[-40:]
        cursor = since
        if events:
            cursor = max(cursor, max(int(item["seq"]) for item in events))
        return {"events": events, "cursor": cursor}

    return _guard(run)


def _pull_events(since: int) -> list[dict]:
    return collect_events(get_graph(), since)


@app.get("/api/stream")
async def stream(once: bool = False) -> StreamingResponse:
    """Server-sent Task, Decision, and Route events. Heartbeat every 15 s."""

    async def generate():
        since = 0
        primed = False
        last_beat = time.monotonic()
        while True:
            try:
                batch = await asyncio.to_thread(_pull_events, since)
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
