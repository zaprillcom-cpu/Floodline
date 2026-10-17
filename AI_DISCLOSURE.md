# AI assistance disclosure

Floodline is a solo entry to WeMakeDevs × FalkorDB Graph Hacks (Tracks 1 and 2).

## Tools used to build this repository

- **Cursor (Grok 4.7)**, as a cloud coding agent, wrote the FalkorDB loader, the OSMnx Valencia corridor ingest, Scout and Dispatcher (including crash/resume from Task nodes), the replay clock, the WCC cut-off and max-flow tools, the FastAPI service, the Next.js map, the tests, and these docs.
- The same assistant, at Sahil's direction, implemented the citizen-escape functions that used to be `TODO(Sahil)` stubs. Sahil reviews this file and the code. He did not write those functions.

The author of the entry is responsible for reviewing, running, and explaining the graph model, the Cypher, and the architecture. A walkthrough written for that explanation is [docs/EXPLAINER.md](docs/EXPLAINER.md).

## Citizen escape

### Written earlier by Cursor

- `backend/data/osm/valencia_corridor.json` lives inside the Railway service root (`backend`). `POST /api/admin/load-valencia` is guarded by `ADMIN_TOKEN`.
- `GET /api/stream` and `GET /api/feed` emit Task, Decision, and Route events, with a heartbeat every 15 seconds.
- Turn-by-turn steps from edge names and bearings (`backend/floodline/navigation.py`). `GET /api/route` returns GeoJSON, steps, and an ETA.
- Full-screen MapLibre map, OpenFreeMap dark style, `fill-extrusion` buildings (`web/components/FloodMap.tsx`).
- Design tokens in `web/lib/theme.ts`.
- Simulated GPS, turn banner, live agent cards, desktop HUD, mobile tabs and bottom sheet, flood-vision tint, Me tab in `localStorage`.
- `POST /api/area/load` returns a job immediately. Inside Greater Mumbai, Manhattan, or wider Valencia the worker crops a 2–3 km tile from `backend/data/regions/*.sqlite` (about 59 MB committed in git, not Git LFS), loads that tile graph, builds the CCH index, and passes up to 8 OSM shelters plus 4 hospitals from the same file into `ensure_shelter_citizen_model`. Overpass is only used outside those regions (four mirrors sharing a 40 s budget, `User-Agent: Floodline/1.0` plus the repo URL). Default radius is 2000 m, capped at 5000 m. The map polls `GET /api/area/status` and gives up if the status message does not change for 45 s. No `ADMIN_TOKEN`.
- `POST /api/area/city/mumbai` and `/nyc` crop the offline tile at Goregaon (19.1663, 72.8526) and midtown Manhattan. `POST /api/area/city/valencia` and `POST /api/area/valencia` still load `valencia_corridor.json`. The city buttons call `/api/area/load`, using the last GPS fix when it falls inside the city.
- The map shows a destination card (shelter or hospital name, distance, ETA), a Destination pin, and a change-destination list. Live mode follows `watchPosition`. Demo mode simulates movement.

### Written by Cursor at Sahil's direction (reviewed by Sahil, not written by him)

| Function | File | What it does |
|---|---|---|
| `ensure_shelter_citizen_model` | `backend/floodline/stubs.py` | Idempotent Shelter and Citizen indexes, `NEAR` and `ACCESS` |
| `snap_point` | `backend/floodline/stubs.py` | Nearest Intersection by the point index |
| `route_to_shelter` | `backend/floodline/stubs.py` | Snap, top-k open shelters, CCH, Citizen route in the graph |
| `geocode_report` | `backend/floodline/stubs.py` | Street name or landmark onto ROAD edges |
| `reroute_affected` | `backend/floodline/stubs.py` | Citizens on a closed edge, new Decision |
| `shelter_reachability` | `backend/floodline/stubs.py` | `algo.WCC` on `PASSABLE` toward open shelters |
| `plan_with_llm` | `backend/floodline/agents/llm_planner.py` | OpenAI tool calls, keyword fallback |
| `assess_loaded_area` | `backend/floodline/analysis.py` | Analyst WCC + maxFlow on any loaded district |

The demo video is Sahil's. No API keys are committed. `ADMIN_TOKEN` and, if used, `OPENAI_API_KEY` are set only on Railway.

## Runtime model inside the product

Routing, geocoding, connectivity, and flow stay in FalkorDB (`db.idx.cch.query`, `algo.AStar`, `algo.SPpaths`, `algo.WCC`, `algo.maxFlow`). The model never computes a path.

`plan_with_llm` is a live OpenAI tool call when `OPENAI_API_KEY` is set (`OPENAI_MODEL`, default `gpt-4o-mini`). It may call `geocode`, `close_road`, and `severity`. The prompt includes candidate roads and nearby shelters already read from the graph. A missing key, a failed request, or a reply that does not match the tool schema falls back to `backend/floodline/agents/planner.py`, the keyword planner. The close Decision and the Task store `planner` as `llm` or `keyword`, plus `reasoning`. The Live card shows both.

The keyword planner only chooses a tool (`close_road`, `close_nearest`, or `noop`). It does not compute routes, travel times, or hospital assignments.

`explain` returns the explanation string already stored on the `Decision` node. The field `narrator` is `"stored"`.
