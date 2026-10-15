# AI assistance disclosure

Floodline is a solo entry to WeMakeDevs × FalkorDB Graph Hacks (Tracks 1 and 2).

## Tools used to build this repository

- **Cursor Grok 4.7**, as a cloud coding agent, wrote the FalkorDB loader, the OSMnx Valencia corridor ingest, Scout and Dispatcher (including crash/resume from Task nodes), the replay clock, the WCC cut-off and max-flow tools, the FastAPI service, the Next.js map, tests, and these docs.
- The design notes this code follows (`PLAN.md` and the end-to-end plan) were written before this implementation. They are not committed here; the graph model in the README is the part this slice actually loads.

The author of the entry is responsible for reviewing, running, and explaining the graph model, the Cypher, and the architecture.

## Citizen escape (PLAN_V2)

Cursor Grok 4.7 implemented every task tagged **AI-assist**. Sahil's tasks are stubs: a signature, a docstring, and `# TODO(Sahil)`, falling back to code that already existed. Sahil reviews this file.

### Built by Cursor Grok 4.7

- `backend/data/osm/valencia_corridor.json` lives inside the Railway service root (`backend`). `POST /api/admin/load-valencia` is guarded by `ADMIN_TOKEN`.
- `GET /api/stream` and `GET /api/feed` emit Task, Decision, and Route events, with a heartbeat every 15 seconds.
- Turn-by-turn steps from edge names and bearings (`backend/floodline/navigation.py`). `GET /api/route` returns GeoJSON, steps, and an ETA.
- Full-screen MapLibre map, OpenFreeMap dark style, `fill-extrusion` buildings (`web/components/FloodMap.tsx`).
- Design tokens in `web/lib/theme.ts`.
- Simulated GPS, turn banner, live agent cards, desktop HUD, mobile tabs and bottom sheet, flood-vision tint, Me tab in `localStorage`.
- `POST /api/area/load` returns a job immediately. A background worker downloads an Overpass drive network (four mirrors sharing a 40 s budget, `User-Agent: Floodline/1.0` plus the repo URL, no OSMnx status pause), stores it in a FalkorDB graph named for a 0.05° tile, builds the CCH index, and passes up to 8 OSM shelter candidates into the existing shelter stubs. Default radius is 2000 m, capped at 5000 m. The map polls `GET /api/area/status` and gives up if `queued` or `fetching` does not change for 45 s. No `ADMIN_TOKEN`.
- `POST /api/area/city/{slug}` loads a bundled extract for Valencia, Mumbai, or New York. Mumbai and New York were cropped with osmium from Geofabrik PBF extracts because public Overpass was down, then prepared with the same pipeline as `valencia_corridor.json`. `POST /api/area/valencia` still loads the Valencia graph. Route, overlay, zones, feed, report, and SSE take that graph id.
- The map's **Use my location** action is the default when geolocation permission is already granted. The loading state reads "Mapping streets around you…". The desktop HUD and the mobile Escape tab also offer the bundled cities. If a live download fails, that picker is shown immediately.

### Left for Sahil

| Function | File |
|---|---|
| `ensure_shelter_citizen_model` | `backend/floodline/stubs.py` |
| `snap_point` | `backend/floodline/stubs.py` |
| `route_to_shelter` | `backend/floodline/stubs.py` |
| `geocode_report` | `backend/floodline/stubs.py` |
| `reroute_affected` | `backend/floodline/stubs.py` |
| `shelter_reachability` | `backend/floodline/stubs.py` |
| `plan_with_llm` | `backend/floodline/agents/llm_planner.py` |

The demo video is also Sahil's. No API keys are committed. `ADMIN_TOKEN` is set only on Railway.

## Runtime model inside the product

There is **no live LLM** in this slice. `backend/floodline/agents/planner.py` is a deterministic keyword stub:

- It only chooses a tool (`close_road`, `close_nearest`, or `noop`).
- It does not compute routes, travel times, or hospital assignments.
- Those come from FalkorDB (`db.idx.cch.query`, with `algo.AStar` and `algo.SPpaths` as fallbacks).

The `explain` tool returns the explanation string already stored on the `Decision` node. The field `narrator` is `"stub"`. A later slice can replace `plan` and the narrator with a tool-calling model without moving route computation into the model.

No API keys are required to run the demo. Do not commit any.
