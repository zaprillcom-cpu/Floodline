"""FastAPI surface for the map. State lives in FalkorDB, not in this process."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from floodline.analysis import cut_off_areas, evac_capacity
from floodline.config import Settings
from floodline.db import get_graph
from floodline.network import DEMO_REPORT
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
    return _guard(lambda: file_report(body.text))


@app.get("/api/analysis/cutoff")
def cutoff() -> list:
    return _guard(lambda: cut_off_areas(get_graph()))


@app.get("/api/analysis/evac")
def evac(district: str = "Paiporta") -> dict:
    return _guard(lambda: evac_capacity(get_graph(), district))
