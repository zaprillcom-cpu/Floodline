# Cypher and graph algorithms

The strings below are the queries the first slice runs. They live in `backend/floodline/cypher.py`. Weights are integer seconds. A path whose weight is `>= 100000000` is treated as unreachable, which is how a closed road (`travel_s = 1000000000`) drops out of a route without deleting the `ROAD` edge.

## Load shape

Intersections:

```cypher
CREATE (:Intersection {
  osmid: $osmid,
  name: $name,
  lat: $lat,
  lon: $lon,
  district: $district,
  role: $role
})
```

Each undirected road is two directed `ROAD` edges plus two `PASSABLE` mirrors:

```cypher
MATCH (a:Intersection {osmid: $src}), (b:Intersection {osmid: $dst})
CREATE (a)-[:ROAD {
  road_id: $road_id,
  name: $name,
  osm_way: $osm_way,
  length_m: $length_m,
  speed_kph: $speed_kph,
  base_travel_s: $travel_s,
  travel_s: $travel_s,
  capacity: $capacity,
  status: 'open'
}]->(b)
CREATE (a)-[:PASSABLE {road_id: $road_id}]->(b)
```

`PASSABLE` exists because `algo.WCC` and `algo.betweenness` filter by relationship type, not by properties. This slice deletes `PASSABLE` on closure so a later cut-off query can use it. It is not CCH-indexed, so deleting it does not force a hierarchy rebuild.

## Indexes

```cypher
CREATE INDEX FOR (i:Intersection) ON (i.osmid)
CREATE INDEX FOR (i:Intersection) ON (i.loc)
CREATE INDEX FOR (h:Hospital) ON (h.id)
CREATE INDEX FOR (t:Task) ON (t.status)
CREATE INDEX FOR (d:Decision) ON (d.id)
CREATE INDEX FOR ()-[r:ROAD]-() ON (r.status)
CREATE CCH INDEX FOR ()-[e:ROAD]->() ON (e.travel_s)
```

`i.loc` is `point({latitude, longitude})`. On FalkorDB v4.22.0 the CCH index reports `travel_s: ['CCH']` from `CALL db.indexes()`.

## Routing

Prefer CCH. If the procedure is missing, the index cannot be built, or the returned path still uses a closed edge, try A*, then `algo.SPpaths`.

CCH:

```cypher
MATCH (a:Intersection {osmid: $src}), (b:Intersection {osmid: $dst})
CALL db.idx.cch.query({
  sourceNode: a,
  targetNode: b,
  relTypes: ['ROAD'],
  weightProp: 'travel_s'
})
YIELD pathWeight, path
WITH pathWeight, path
WHERE pathWeight < 100000000
RETURN pathWeight AS travel_s,
       [n IN nodes(path) | n.osmid] AS osmids,
       [n IN nodes(path) | n.name] AS names
```

A* (`heuristicScale` 0.03 is about 1/33.3, seconds per metre at 120 km/h, so the metre heuristic stays admissible for `travel_s`):

```cypher
MATCH (a:Intersection {osmid: $src}), (b:Intersection {osmid: $dst})
CALL algo.AStar({
  sourceNode: a,
  targetNode: b,
  relTypes: ['ROAD'],
  weightProp: 'travel_s',
  latitudeProperty: 'lat',
  longitudeProperty: 'lon',
  heuristicScale: 0.03
})
YIELD path, pathWeight
WITH pathWeight, path
WHERE pathWeight < 100000000
RETURN pathWeight AS travel_s,
       [n IN nodes(path) | n.osmid] AS osmids,
       [n IN nodes(path) | n.name] AS names
```

Fallback, present on every FalkorDB version this project cares about:

```cypher
MATCH (a:Intersection {osmid: $src}), (b:Intersection {osmid: $dst})
CALL algo.SPpaths({
  sourceNode: a,
  targetNode: b,
  relTypes: ['ROAD'],
  weightProp: 'travel_s',
  relDirection: 'outgoing',
  pathCount: 1
})
YIELD path, pathWeight
WITH pathWeight, path
WHERE pathWeight < 100000000
RETURN pathWeight AS travel_s,
       [n IN nodes(path) | n.osmid] AS osmids,
       [n IN nodes(path) | n.name] AS names
```

On the synthetic network, before the closure, all three return 215 s:
`Paiporta south → Paiporta north → Benetússer → Hospital Doctor Peset`.

## Close a road

Weight-only update (CCH re-customizes; topology stays):

```cypher
MATCH (a)-[r:ROAD {road_id: $road_id}]->(b)
SET r.travel_s = 1000000000,
    r.capacity = 0,
    r.status = 'closed',
    r.closed_at = $at,
    r.closed_by = $by
RETURN count(r) AS closed_edges, collect(DISTINCT r.name)[0] AS name
```

Then drop the mirrors:

```cypher
MATCH (a)-[r:ROAD {road_id: $road_id}]->(b)
MATCH (a)-[p:PASSABLE]->(b)
DELETE p
```

After closing `v31-paiporta-bridge`, the same routers return 320 s:
`Paiporta south → Alfafar → Sedaví → Benetússer → Hospital Doctor Peset`.

## Snap a report to the network

```cypher
WITH point({latitude: $lat, longitude: $lon}) AS p
MATCH (i:Intersection)
WHERE i.loc IS NOT NULL AND distance(i.loc, p) < $radius_m
RETURN i.osmid AS osmid, i.name AS name, distance(i.loc, p) AS dist_m
ORDER BY dist_m ASC
LIMIT 1
```

If `point()` is unavailable, `snap_to_road` scans `Intersection` latitude and longitude in the graph, then the synthetic place table.

## Hospital choice

```cypher
MATCH (h:Hospital)-[:ACCESS]->(i:Intersection)
WHERE h.er_open = true AND h.beds_free > 0
RETURN h.id AS id, h.name AS name, h.beds_free AS beds_free, i.osmid AS osmid
```

The Dispatcher routes to each row and keeps the smallest `travel_s`, breaking ties toward more free beds. On this network that is Hospital Doctor Peset (12 beds free) rather than Hospital La Fe.

## Track 2: claim, handoff, explain

Atomic claim. FalkorDB serializes writes, so two Dispatchers cannot both observe `status = 'open'` and both claim:

The agent is matched before the task is updated. A missing agent name leaves the task `open`.

```cypher
MATCH (a:Agent {name: $agent})
MATCH (t:Task {status: 'open', kind: $kind})
WITH a, t
ORDER BY t.priority DESC, t.created_at ASC
LIMIT 1
SET t.status = 'claimed', t.claimed_at = $now, a.heartbeat_at = $now
CREATE (a)-[:CLAIMED {at: $now}]->(t)
RETURN t.id AS id, t.road_id AS road_id, t.road_name AS road_name,
       t.from_osmid AS from_osmid, t.to_osmid AS to_osmid,
       t.report_id AS report_id, t.close_decision_id AS close_decision_id
```

A restart reads tasks this agent already claimed and did not finish:

```cypher
MATCH (:Agent {name: $agent})-[:CLAIMED]->(t:Task {status: 'claimed', kind: $kind})
RETURN t.id, t.road_id, t.from_osmid, t.to_osmid, t.report_id, t.close_decision_id
ORDER BY t.claimed_at
```

It finishes those, then claims any `reassess` task that is still `open`. `Agent.heartbeat_at` is set when the new process starts.

## Replay clock

`python -m floodline.replay` writes three `Tick` nodes and links each decision taken at that time:

```cypher
CREATE (:Tick {seq: $seq, t: $t, label: $label})
```

```cypher
MATCH (tick:Tick {seq: $seq}), (d:Decision {at: $t})
MERGE (d)-[:AT_TICK]->(tick)
```

The incident is a fixed id, not a random draw: `(:Incident {id: 'inc-seed-1', kind: 'medical', severity: 4, source: 'synthetic'})`.

Scout writes a done `ingest_report` task and an open `reassess` task, then:

```cypher
MATCH (a:Task {id: $source}), (b:Task {id: $target})
CREATE (a)-[:HANDOFF {reason: $reason}]->(b)
```

The new route decision points at the previous one and at the evidence:

```cypher
MATCH (d:Decision {id: $id}), (prev:Decision {id: $prev})
CREATE (d)-[:SUPERSEDES]->(prev)
SET prev.superseded = true
```

```cypher
MATCH (d:Decision {id: $id})
OPTIONAL MATCH (d)-[:BASED_ON]->(evidence)
OPTIONAL MATCH (d)-[:SUPERSEDES]->(prev:Decision)
OPTIONAL MATCH (a:Agent)-[:MADE]->(d)
RETURN d.explanation AS explanation, d.algo AS algo, prev.id AS previous_id,
       a.name AS agent, collect(DISTINCT evidence.id) AS evidence_ids
```

`explain` returns that stored explanation. The response field `narrator` is `stored`. The model does not rewrite it.

## Valencia OSM bulk load

`backend/floodline/bulk.py` inserts the OSMnx extract in batches. The shape matches the synthetic roads: directed `ROAD` (`travel_s`, `base_travel_s`, `capacity`, `base_capacity`, `status`) and a `PASSABLE` mirror. `road_id` is shared by the two directions of one OSM way, so `close_road` closes both. One-way segments get their own id.

```cypher
UNWIND $rows AS row
MATCH (a:Intersection {osmid: row.src}), (b:Intersection {osmid: row.dst})
CREATE (a)-[:ROAD {
  road_id: row.road_id,
  name: row.name,
  osm_way: row.osm_way,
  highway: row.highway,
  length_m: row.length_m,
  speed_kph: row.speed_kph,
  base_travel_s: row.travel_s,
  travel_s: row.travel_s,
  base_capacity: row.capacity,
  capacity: row.capacity,
  status: 'open'
}]->(b)
CREATE (a)-[:PASSABLE {road_id: row.road_id}]->(b)
```

The same indexes as the synthetic load are created after the batches, including `CREATE CCH INDEX FOR ()-[e:ROAD]->() ON (e.travel_s)`. `PASSABLE` is not part of that index.

On the committed extract (2526 intersections, 4742 directed edges) a CCH route from the Paiporta snap of A-7 to the Doctor Peset snap is 467 s. Closing Carretera de Benetússer makes the next CCH route 500 s and drops one `PASSABLE` edge.

## Procedures confirmed on v4.22.0

`CALL dbms.procedures()` on the pinned image includes `algo.AStar`, `algo.SPpaths`, `db.idx.cch.query`, `algo.WCC`, and `algo.maxFlow`. The running module version is `42200` (`4.22.0`). The image default `TIMEOUT` of 1000 ms is raised to 120000 ms at container start (`FALKORDB_ARGS`) and again from the Python client.

## Cut-off (`algo.WCC`)

`PASSABLE` is the open-road mirror. WCC ignores edge properties, so a closure deletes `PASSABLE` instead of filtering on `status`.

```cypher
CALL algo.WCC({
  nodeLabels: ['Intersection'],
  relationshipTypes: ['PASSABLE']
})
YIELD node, componentId
RETURN node.osmid AS osmid, componentId
```

A district is `cut_off = true` when none of its intersections share a component with a hospital's `ACCESS` intersection. On the synthetic graph, closing CV-400, Camí del Poyo, and the Massanassa link puts Paiporta north and south in their own component.

Roads that leave a district:

```cypher
MATCH (inside:Intersection)-[:IN_DISTRICT]->(:District {name: $district})
MATCH (inside)-[r:ROAD {status: 'open'}]-(outside:Intersection)
WHERE NOT (outside)-[:IN_DISTRICT]->(:District {name: $district})
RETURN DISTINCT r.road_id AS road_id, r.name AS name
```

## Evacuation capacity (`algo.maxFlow`)

Sources are the district's intersections, minus the shelter intersections so the two sets stay disjoint. Sinks are open shelters reached by `ACCESS` or `NEAR` that still have free capacity. Edge capacity is `ROAD.capacity` (0 after a closure).

```cypher
MATCH (d:District {name: $district})<-[:IN_DISTRICT]-(src:Intersection)
WITH collect(src) AS sources
MATCH (s:Shelter)-[rel]->(sink:Intersection)
WHERE type(rel) IN ['ACCESS', 'NEAR']
  AND coalesce(s.open, true) = true
  AND (s.capacity IS NULL OR s.occupancy IS NULL OR s.occupancy < s.capacity)
WITH sources, collect(DISTINCT sink) AS targets
WITH [n IN sources WHERE NOT n.osmid IN [t IN targets | t.osmid]] AS sources, targets
WHERE size(sources) > 0 AND size(targets) > 0
CALL algo.maxFlow({
  sourceNodes: sources,
  targetNodes: targets,
  relationshipTypes: ['ROAD'],
  capacityProperty: 'capacity'
})
YIELD edges, edgeFlows, maxFlow
RETURN maxFlow, edges, edgeFlows
```

On the synthetic network this is 1200 veh/h before the corridor closure and 0 after. The critical edge is the saturated edge with the largest flow: V-31 Paiporta bridge, 600 of capacity 600. The same query runs for every District on a loaded city tile.

## Citizen escape queries

These run. FalkorDB does the lookup, the path, and the connectivity. Python only ranks a short candidate list and writes the Decision. Cursor wrote this Cypher at Sahil's direction; Sahil reviews it.

### Snap (`snap_point`)

```cypher
WITH point({latitude: $lat, longitude: $lon}) AS p
MATCH (i:Intersection)
WHERE i.loc IS NOT NULL AND distance(i.loc, p) < $radius_m
RETURN i.osmid AS osmid, i.name AS name, distance(i.loc, p) AS dist_m
ORDER BY dist_m ASC
LIMIT 1
```

If `loc` is missing, Python scans `Intersection.lat/lon` with haversine. Passing a citizen id replaces `(:Citizen)-[:AT]->(:Intersection)`.

### Geocode (`geocode_report`)

Road codes (`CV-36`), short aliases (`SV`, `MG`, `WEH`, `GMLR`, `LBS`), and numbered roads (`No.6`) are the tokens. The Cypher prefilter is:

```cypher
MATCH (a:Intersection)-[r:ROAD]->(b:Intersection)
WHERE toLower(coalesce(r.name, '')) CONTAINS toLower($token)
RETURN r.road_id, r.name, a.lat, a.lon, b.lat, b.lon,
       coalesce(r.highway, ''), coalesce(r.status, 'open')
```

Python then requires a whole word (`tree` does not match `street`, `no 6` does not match `Road No.16`) and drops names that are only an OSM highway type. "CV-36 flooded near Paiporta" returns CV-36. The landmark is not searched as a road name when a road token is already present.

A landmark is:

```cypher
MATCH (l:Landmark)-[:NEAR]->(i:Intersection)
WHERE toLower(l.name) CONTAINS toLower($token)
RETURN l.id, l.name, l.lat, l.lon, i.osmid
LIMIT 12
```

Roads touching that intersection:

```cypher
MATCH (i:Intersection {osmid: $osmid})-[r:ROAD]-(j:Intersection)
RETURN DISTINCT r.road_id, r.name, i.lat, i.lon, j.lat, j.lon,
       coalesce(r.highway, ''), coalesce(r.status, 'open')
```

Python ranks at most eight candidates by landmark distance, then reporter distance, then name overlap. The model receives `name` and `dist_m` and picks one `road_id`.

Landmarks are written when a tile is persisted:

```cypher
MATCH (i:Intersection {osmid: $osmid})
MERGE (l:Landmark {id: $id})
SET l.name = $name, l.kind = $kind, l.lat = $lat, l.lon = $lon
MERGE (l)-[n:NEAR]->(i)
SET n.dist_m = $dist
```

Places come from named schools and hospitals on the tile, plus one Overpass query for malls, stations, hospitals, and schools. A cached tile is not backfilled.

### Shelter model (`ensure_shelter_citizen_model`)

Idempotent indexes: `Shelter(id)`, `Citizen(id)`, `Report(id)`, `Intersection(osmid)`, `Intersection(loc)`. If no `NEAR` edges exist yet, existing `Shelter-ACCESS` edges are copied:

```cypher
MATCH (s:Shelter)-[a:ACCESS]->(i:Intersection)
MERGE (s)-[n:NEAR]->(i)
SET n.dist_m = coalesce(n.dist_m, a.dist_m, 0),
    s.open = coalesce(s.open, true)
```

New candidates `MERGE` the Shelter and both `NEAR` and `ACCESS`.

### Route to shelter (`route_to_shelter`)

```cypher
MATCH (s:Shelter)-[n:NEAR]->(i:Intersection)
RETURN s.id, s.name, s.open, s.capacity, s.occupancy, i.osmid, n.dist_m
ORDER BY n.dist_m ASC
LIMIT $k
```

Python keeps the first five that are open and not full, then calls `routing.route` (CCH, else A*, else SPpaths) and keeps the lowest `travel_s`. The Citizen id is the browser session (`citizen` query on `GET /api/route`), or `citizen` when the client omits a valid id. That node is `MERGE`d. Its old `AT` and `ROUTED_TO` edges are deleted and rewritten:

```cypher
UNWIND $hops AS hop
MATCH (c:Citizen {id: $id}), (i:Intersection {osmid: hop.osmid})
CREATE (c)-[:ROUTED_TO {seq: hop.seq, road_id: hop.road_id}]->(i)
```

A repeated GPS poll updates the existing `citizen_route` Decision in place so the feed does not grow a new card every few seconds.

### Reroute (`reroute_affected`)

```cypher
MATCH (c:Citizen)-[r:ROUTED_TO]->()
WHERE r.road_id = $road_id AND coalesce(c.active, true) = true
RETURN DISTINCT c.id, c.lat, c.lon, c.eta_s, c.decision_id
```

`file_report` passes the `road_id` Scout just closed, not the last id in the alphabetically sorted closed-road list. Each match is routed again with `record_decision=False`, so the new Decision does not supersede itself. The new Decision `SUPERSEDES` the old one, `BASED_ON` the Report, and both `AFFECTS` and `REROUTED` the Citizen. It stores `reason`, `old_eta_s`, and `new_eta_s`.

The green line on Show graph prefers the latest citizen path:

```cypher
MATCH (c:Citizen)
WHERE c.path_osmids IS NOT NULL AND c.path_osmids <> ''
RETURN c.path_osmids
ORDER BY c.updated_at DESC
LIMIT 1
```

### Dispatcher when the path did not change

If the new ambulance osmids equal the previous active `kind: route` Decision, the reassess task is finished with `result: unchanged` and no new Decision is written.

### Duplicate report

When the chosen road is already closed:

```cypher
MATCH (n:Report {id: $new}), (o:Report {id: $old})
MERGE (n)-[:DUPLICATE_OF]->(o)
```

The new Report has `status: duplicate`. A rejected or vague report is stored with `status: rejected` or `status: needs_location` and a notice Decision (`kind: reject` or `request_clarification`). No road is closed.

### Memory reset

`POST /api/admin/memory/reset?graph=` (admin token) reopens closed roads, then:

```cypher
MATCH (n)
WHERE n:Report OR n:Task OR n:Decision OR n:Citizen
DETACH DELETE n
```

### Shelter reachability (`shelter_reachability`)

```cypher
CALL algo.WCC({
  nodeLabels: ['Intersection'],
  relationshipTypes: ['PASSABLE']
})
YIELD node, componentId
RETURN node.osmid AS osmid, componentId
```

Open shelters contribute their component ids. A District is `cut_off` when none of its intersections share one of those components. A mixed district also returns the isolated pocket (`osmids`, name `"{district} isolated"`) so the map can shade it.

### Planner (`plan_with_llm`)

Not Cypher. The system prompt treats the report as untrusted data and allows at most one road unless the text explicitly names more than one. The user message is the report inside `<report>` tags plus up to eight candidates with names and distances. Accepted tools are `close_road`, `reopen_road`, `request_clarification`, and `reject`. Each requires `reason`. `noop` is treated as a clarification. One invalid call rejects the whole reply and the keyword planner runs. If several calls are returned, `reject` wins over a close. Scout closes a road only for `close_road` (a candidate, or the keyword planner's explicit id) or the keyword tool `close_nearest`. The Decision and the Task store `planner` (`llm` or `keyword`) and `reasoning`, and the Live card shows the reason.

`GET /api/route?citizen=` calls `route_to_shelter` for that Citizen and then builds the GeoJSON line and the turn steps in `navigation.py`.
