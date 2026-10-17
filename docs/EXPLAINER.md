# Explaining the graph operations

This is the walkthrough for the demo. Each function below was implemented by Cursor (Grok 4.7) at Sahil's direction. Sahil reviews it and can explain it. He did not write the functions.

FalkorDB v4.22.0 does the lookup and the algorithms. Python asks, ranks a short list, and writes Decision nodes. The model, when it runs, only picks a tool from roads the graph already returned.

## `ensure_shelter_citizen_model`

**What a judge should hear.** Before anyone is routed, the graph has to know what a shelter is and where it sits. This function makes that model, and it is safe to call again.

**What it writes.** Indexes on `Shelter.id`, `Citizen.id`, `Report.id`, `Intersection.osmid`, and `Intersection.loc`. If the graph already has shelters attached with `ACCESS` and no `NEAR` edges yet, those `ACCESS` edges are copied to `NEAR` so old loads and new loads look the same. Each new candidate is `MERGE`d as a `Shelter` and linked with both `NEAR` and `ACCESS` to its intersection. `ACCESS` stays because the map pins and `algo.maxFlow` already follow it. `NEAR` is what shelter choice ranks.

**Why both edges.** Dropping `ACCESS` would hide shelters from code that was already shipping. Writing only `NEAR` would do the same. Dual-write is the compatible choice. The backfill runs only when the `NEAR` count is zero, so a second call does not invent extra edges.

**Cypher, backfill:**

```cypher
MATCH (s:Shelter)-[a:ACCESS]->(i:Intersection)
MERGE (s)-[n:NEAR]->(i)
SET n.dist_m = coalesce(n.dist_m, a.dist_m, 0),
    s.open = coalesce(s.open, true)
```

## `snap_point`

**What a judge should hear.** A GPS fix is not an intersection. Snap finds the nearest one.

**How.** Prefer the point index. `i.loc` is a FalkorDB point. The query keeps intersections within 8 km and returns the closest:

```cypher
WITH point({latitude: $lat, longitude: $lon}) AS p
MATCH (i:Intersection)
WHERE i.loc IS NOT NULL AND distance(i.loc, p) < $radius_m
RETURN i.osmid, i.name, distance(i.loc, p) AS dist_m
ORDER BY dist_m ASC
LIMIT 1
```

If the index or `loc` is missing, Python walks `lat`/`lon` with haversine. There is no fallback to a hard-coded place list: a miss is a miss. The returned node includes `method` (`point-index` or `haversine`). When a citizen id is passed, the old `AT` edge is deleted and a new `(:Citizen)-[:AT]->(:Intersection)` is created.

## `geocode_report`

**What a judge should hear.** "CV-36 flooded near Paiporta" must close CV-36, not whichever road happens to be nearest the pin.

**How.** A road-code regex (`CV-36`, `V-31`) is the primary token. The Cypher match is `toLower(r.name) CONTAINS toLower($token)`. Words that are just disaster language ("flooded", "road", "closed") are on a stoplist and are not searched. A phrase after "near", "at", or "by" is a landmark and only breaks ties. Distance from the reported coordinate is the other tie-break. The top five `road_id`s come back. Without a graph, the parsed tokens are returned and `edges` is empty, so the function is testable offline.

**Design choice.** Name match before distance. A landmark is context, not the thing that closed.

## `route_to_shelter`

**What a judge should hear.** Snap the person, ask the graph for open shelters, ask CCH which one is actually fastest, and remember the person and the route in the graph.

**Steps.**

1. `ensure_shelter_citizen_model` so `NEAR` exists.
2. `snap_point` to an intersection.
3. `TOP_SHELTERS_NEAR` returns shelters ordered by `NEAR.dist_m`. Python keeps five that are open and not full, skipping a shelter that is the origin itself.
4. `routing.route` tries `db.idx.cch.query` on `ROAD.travel_s`, then `algo.AStar`, then `algo.SPpaths`. Paths whose weight is at least 1e8 seconds are thrown away, which is how a closed road (`travel_s = 1e9`) stays in the graph but drops out of the route.
5. The lowest `travel_s` wins. If no shelter is reachable, the nearest open hospital with free beds is the destination. That is the same CCH chain.

**Memory.** One citizen id, `citizen`, per graph. `remember_route` `MERGE`s that node, deletes old `AT` and `ROUTED_TO` edges, and writes one `ROUTED_TO` per hop with `seq` and `road_id`. The Decision kind is `citizen_route`, not `route`, so the ambulance's before/after pair in the snapshot is not confused with the evacuee. A later GPS poll updates that same Decision in place (same id, same sequence) so the Live tab does not fill with duplicate cards.

**Title.** OSM names every node after the street, so a raw path reads "Aarey Road → Aarey Road". `collapse_names` drops consecutive duplicates before the summary is stored, and the Live card does it again on display.

## `reroute_affected`

**What a judge should hear.** When a road closes, find every citizen whose active route uses that road and give them a new one, with a Decision that points at the old decision and at the report.

**How.** The citizens are found in Cypher, not in Python:

```cypher
MATCH (c:Citizen)-[r:ROUTED_TO]->()
WHERE r.road_id = $road_id AND coalesce(c.active, true) = true
RETURN DISTINCT c.id, c.lat, c.lon, c.eta_s, c.decision_id
```

That list is read before the routes are rewritten. Each citizen is sent through `route_to_shelter` with `record_decision=False`. If that call wrote its own Decision, `SUPERSEDES` would point at itself. A new Decision is then created with `reason`, `old_eta_s`, `new_eta_s`, `SUPERSEDES` to the previous citizen route, `BASED_ON` the report that closed the road, and both `AFFECTS` and `REROUTED` to the citizen. Dispatcher is the agent on `MADE`.

The ambulance still has its own Dispatcher path (`kind: route`). This function is the citizen fan-out.

## `shelter_reachability`

**What a judge should hear.** Purple on the map means that part of the city cannot reach an open shelter, even if the roads inside it are fine.

**How.** `algo.WCC` on `PASSABLE` labels every intersection with a component id. Open shelters (via `ACCESS` or `NEAR`, occupancy under capacity) mark the components that are safe. A district is cut off when none of its intersections sit in a safe component, and `District.cut_off` is set. If a district is only partly isolated, the cut-off pocket is returned separately as `"{name} isolated"` with the osmids, so the map can shade the pocket instead of the whole district.

`PASSABLE` is deleted when a road closes and recreated when it reopens. WCC never walks a closed road. The `ROAD` edge stays, with a huge `travel_s`, so CCH only has to re-customize a weight.

## `plan_with_llm`

**What a judge should hear.** The sentence the citizen typed is turned into a tool call. The graph still decides which edge that name is, and CCH still decides the route. If the model is missing or nonsense, a keyword planner does the same job and the card says so.

**How.** `OPENAI_API_KEY` and `OPENAI_MODEL` (default `gpt-4o-mini`) go to `POST /v1/chat/completions` with `temperature` 0 and a 12 second timeout. No new Python dependency: the call is `urllib`. The user message is the report plus JSON context: up to eight geocoded candidate roads and nearby shelters from `NEAR`.

Tools the schema accepts:

- `geocode` with a `query` string
- `close_road` with a `road_id` or `road_name`
- `severity` with `level` one of `low`, `medium`, `high`, `critical`

Any call that fails that schema rejects the whole reply. A missing key returns the keyword planner exactly, with `planner: keyword`. A transport error or an invalid reply also falls back, and `reasoning` says why. A valid reply prefers `close_road`, attaches the severity level, and stores the model's text as `reasoning`. Scout writes `planner` and `reasoning` on the close Decision and the Tasks. The Live card prints `planner keyword` or `planner llm` and a "Reasoning." line.

**Design choice.** The model is not allowed to invent a `road_id` that was not in the candidate list and have that id trusted blindly: Scout still resolves `close_road` against the graph, and a name that does not match falls through geocoding. Keyword mode has no key and still closes a road from a coordinate or a known id, so the demo runs on Railway before a key is set.

## `assess_loaded_area`

**What a judge should hear.** Analyst is not a Valencia-only script. Any loaded tile gets the same WCC cut-off and the same max-flow, and the three agents show up together.

**How.** On `file_report` and after an area load, Analyst is `MERGE`d, `shelter_reachability` runs, and `evac_capacity` runs `algo.maxFlow` once per District. A failure in maxFlow is recorded on the Decision (`maxFlow unavailable`) and does not abort the report. The function writes an `assess` Task, claims and finishes it as Analyst, and a `HANDOFF` from the previous task (the Dispatcher's reassess task when there is one). Two Decisions follow: `alert` with `algo.WCC`, and `evac_plan` with `algo.maxFlow`. Both are `BASED_ON` the report when there is one.

**maxFlow and shelters inside the district.** Sources are every intersection in the district. Sinks are the shelter intersections. If the shelter is inside the district it would be in both sets, and FalkorDB rejects that. The query drops sink osmids out of the source list first. On the synthetic Paiporta graph the shelter is outside the district, so the numbers stay 1200 veh/h before the corridor closure and 0 after.

## Clocks and place names

The reconstructed clock `2024-10-29` is only the synthetic replay (`seed`, `ingest`, `replay`). A live report, a reopen, and an area-load heartbeat use `utc_now()` (UTC, no microseconds). The Live card formats that timestamp. It does not show a fixed 2024 date for a new report.

The sentence about the Valencia DANA reconstruction is added only when `Meta.source` is `synthetic` or a district name contains "paiporta". A Mumbai tile or an Overpass tile does not get that sentence.

## What still needs a person

Set `OPENAI_API_KEY` on the Railway service if the live demo should show `planner llm`. Without it the card shows `planner keyword` and the road still closes. `OPENAI_MODEL` is optional. `ADMIN_TOKEN` still guards only the Valencia admin load. FalkorDB host, port, and graph name are the existing Railway variables.
