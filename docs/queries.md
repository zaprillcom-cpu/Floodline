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

If `point()` is unavailable, Scout falls back to a haversine scan of the ten intersections.

## Hospital choice

```cypher
MATCH (h:Hospital)-[:ACCESS]->(i:Intersection)
WHERE h.er_open = true AND h.beds_free > 0
RETURN h.id AS id, h.name AS name, h.beds_free AS beds_free, i.osmid AS osmid
```

The Dispatcher routes to each row and keeps the smallest `travel_s`, breaking ties toward more free beds. On this network that is Hospital Doctor Peset (12 beds free) rather than Hospital La Fe.

## Track 2: claim, handoff, explain

Atomic claim. FalkorDB serializes writes, so two Dispatchers cannot both observe `status = 'open'` and both claim:

```cypher
MATCH (t:Task {status: 'open', kind: $kind})
WITH t
ORDER BY t.priority DESC, t.created_at ASC
LIMIT 1
SET t.status = 'claimed', t.claimed_at = $now
WITH t
MATCH (a:Agent {name: $agent})
CREATE (a)-[:CLAIMED {at: $now}]->(t)
SET a.heartbeat_at = $now
RETURN t.id AS id, t.road_id AS road_id, t.road_name AS road_name,
       t.from_osmid AS from_osmid, t.to_osmid AS to_osmid,
       t.report_id AS report_id, t.close_decision_id AS close_decision_id
```

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

The narrator in this slice returns that explanation unchanged. A later slice can hand the same payload to a model.

## Procedures confirmed on v4.22.0

`CALL dbms.procedures()` on the pinned image includes `algo.AStar`, `algo.SPpaths`, `db.idx.cch.query`, `algo.WCC`, and `algo.maxFlow`. WCC and max-flow are not called by this slice.
