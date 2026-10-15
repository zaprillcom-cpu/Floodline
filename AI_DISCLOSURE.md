# AI assistance disclosure

Floodline is a solo entry to WeMakeDevs × FalkorDB Graph Hacks (Tracks 1 and 2).

## Tools used to build this repository

- **Cursor Grok 4.7**, as a cloud coding agent, wrote the FalkorDB loader, the OSMnx Valencia corridor ingest, Scout and Dispatcher (including crash/resume from Task nodes), the replay clock, the WCC cut-off and max-flow tools, the FastAPI service, the Next.js map, tests, and these docs.
- The design notes this code follows (`PLAN.md` and the end-to-end plan) were written before this implementation. They are not committed here; the graph model in the README is the part this slice actually loads.

The author of the entry is responsible for reviewing, running, and explaining the graph model, the Cypher, and the architecture.

## Citizen escape — backend (AI-assist)

Cursor Grok 4.7 added the following, from the tasks tagged AI-assist in PLAN_V2:

- Moved the Valencia extract to `backend/data/osm/valencia_corridor.json` so the Railway service (root directory `backend`) contains it, and added `POST /api/admin/load-valencia`, guarded by the `ADMIN_TOKEN` environment variable.
- `GET /api/stream` (SSE) and `GET /api/feed` emit Task, Decision, and Route events already stored on those nodes, with a heartbeat every 15 seconds.
- Turn-by-turn step text in `backend/floodline/navigation.py` (edge name and bearing). `GET /api/route` returns a GeoJSON line, steps, and an ETA.
- The citizen web app: full-screen MapLibre (OpenFreeMap dark, fill-extrusion buildings), design tokens in `web/lib/theme.ts`, simulated GPS and a turn banner, a live Scout/Dispatcher/Analyst feed over the SSE stream, a desktop side HUD, a separate mobile layout (bottom tabs Escape / Report / Live / Me and a three-stop bottom sheet), flood vision, and a Me tab stored in `localStorage`.
- Stubs only, each marked `TODO(Sahil)`, for the Shelter/Citizen model, `route_to_shelter`, reroute fan-out, snap, shelter WCC, the LLM planner, and report geocoding. They fall back to the existing CCH → A* → SPpaths router or the keyword planner. See `backend/floodline/stubs.py` and `backend/floodline/agents/llm_planner.py`.

Sahil still writes and explains those graph operations. The fallbacks exist so the map can draw a line before that work lands.

## Runtime model inside the product

There is **no live LLM** in this slice. `backend/floodline/agents/planner.py` is a deterministic keyword stub:

- It only chooses a tool (`close_road`, `close_nearest`, or `noop`).
- It does not compute routes, travel times, or hospital assignments.
- Those come from FalkorDB (`db.idx.cch.query`, with `algo.AStar` and `algo.SPpaths` as fallbacks).

The `explain` tool returns the explanation string already stored on the `Decision` node. The field `narrator` is `"stub"`. A later slice can replace `plan` and the narrator with a tool-calling model without moving route computation into the model.

No API keys are required to run the demo. Do not commit any.
