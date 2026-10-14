# AI assistance disclosure

Floodline is a solo entry to WeMakeDevs × FalkorDB Graph Hacks (Tracks 1 and 2).

## Tools used to build this repository

- **Cursor Grok 4.7**, as a cloud coding agent, wrote the FalkorDB loader, the OSMnx Valencia corridor ingest, Scout and Dispatcher (including crash/resume from Task nodes), the WCC cut-off and max-flow tools, the FastAPI service, the Next.js map, tests, and these docs.
- The design notes this code follows (`PLAN.md` and the end-to-end plan) were written before this implementation. They are not committed here; the graph model in the README is the part this slice actually loads.

The author of the entry is responsible for reviewing, running, and explaining the graph model, the Cypher, and the architecture.

## Runtime model inside the product

There is **no live LLM** in this slice. `backend/floodline/agents/planner.py` is a deterministic keyword stub:

- It only chooses a tool (`close_road`, `close_nearest`, or `noop`).
- It does not compute routes, travel times, or hospital assignments.
- Those come from FalkorDB (`db.idx.cch.query`, with `algo.AStar` and `algo.SPpaths` as fallbacks).

The `explain` tool returns the explanation string already stored on the `Decision` node. The field `narrator` is `"stub"`. A later slice can replace `plan` and the narrator with a tool-calling model without moving route computation into the model.

No API keys are required to run the demo. Do not commit any.
