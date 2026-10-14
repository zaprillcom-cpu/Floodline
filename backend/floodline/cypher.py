"""Cypher used by the first slice.

These strings are the queries the tools actually run. docs/queries.md quotes them.
"""

from floodline.config import ASTAR_HEURISTIC_SCALE, CLOSED_TRAVEL_S, UNREACHABLE_WEIGHT

CREATE_OSMID_INDEX = "CREATE INDEX FOR (i:Intersection) ON (i.osmid)"
CREATE_LOC_INDEX = "CREATE INDEX FOR (i:Intersection) ON (i.loc)"
CREATE_HOSPITAL_INDEX = "CREATE INDEX FOR (h:Hospital) ON (h.id)"
CREATE_TASK_INDEX = "CREATE INDEX FOR (t:Task) ON (t.status)"
CREATE_DECISION_INDEX = "CREATE INDEX FOR (d:Decision) ON (d.id)"
CREATE_ROAD_STATUS_INDEX = "CREATE INDEX FOR ()-[r:ROAD]-() ON (r.status)"

CREATE_CCH = "CREATE CCH INDEX FOR ()-[e:ROAD]->() ON (e.travel_s)"

# Shared tail: drop paths that only exist by traversing a closed edge (travel_s = 1e9).
_PATH_RETURN = f"""
WITH pathWeight, path
WHERE pathWeight < {UNREACHABLE_WEIGHT}
RETURN pathWeight AS travel_s,
       [n IN nodes(path) | n.osmid] AS osmids,
       [n IN nodes(path) | n.name] AS names
"""

ROUTE_CCH = f"""
MATCH (a:Intersection {{osmid: $src}}), (b:Intersection {{osmid: $dst}})
CALL db.idx.cch.query({{
  sourceNode: a,
  targetNode: b,
  relTypes: ['ROAD'],
  weightProp: 'travel_s'
}})
YIELD pathWeight, path
{_PATH_RETURN}
"""

ROUTE_ASTAR = f"""
MATCH (a:Intersection {{osmid: $src}}), (b:Intersection {{osmid: $dst}})
CALL algo.AStar({{
  sourceNode: a,
  targetNode: b,
  relTypes: ['ROAD'],
  weightProp: 'travel_s',
  latitudeProperty: 'lat',
  longitudeProperty: 'lon',
  heuristicScale: {ASTAR_HEURISTIC_SCALE}
}})
YIELD path, pathWeight
{_PATH_RETURN}
"""

ROUTE_SPPATHS = f"""
MATCH (a:Intersection {{osmid: $src}}), (b:Intersection {{osmid: $dst}})
CALL algo.SPpaths({{
  sourceNode: a,
  targetNode: b,
  relTypes: ['ROAD'],
  weightProp: 'travel_s',
  relDirection: 'outgoing',
  pathCount: 1
}})
YIELD path, pathWeight
{_PATH_RETURN}
"""

CLOSE_ROAD = f"""
MATCH (a)-[r:ROAD {{road_id: $road_id}}]->(b)
SET r.travel_s = {CLOSED_TRAVEL_S},
    r.capacity = 0,
    r.status = 'closed',
    r.closed_at = $at,
    r.closed_by = $by
RETURN count(r) AS closed_edges, collect(DISTINCT r.name)[0] AS name
"""

DELETE_PASSABLE = """
MATCH (a)-[r:ROAD {road_id: $road_id}]->(b)
MATCH (a)-[p:PASSABLE]->(b)
DELETE p
"""

SNAP_GEO = """
WITH point({latitude: $lat, longitude: $lon}) AS p
MATCH (i:Intersection)
WHERE i.loc IS NOT NULL AND distance(i.loc, p) < $radius_m
RETURN i.osmid AS osmid, i.name AS name, distance(i.loc, p) AS dist_m
ORDER BY dist_m ASC
LIMIT 1
"""

# The agent is matched first. If that name is missing, the task stays open.
# FalkorDB serializes writes, so two callers cannot both claim the same open task.
CLAIM_TASK = """
MATCH (a:Agent {name: $agent})
MATCH (t:Task {status: 'open', kind: $kind})
WITH a, t
ORDER BY t.priority DESC, t.created_at ASC
LIMIT 1
SET t.status = 'claimed', t.claimed_at = $now, a.heartbeat_at = $now
CREATE (a)-[:CLAIMED {at: $now}]->(t)
RETURN t.id AS id,
       t.road_id AS road_id,
       t.road_name AS road_name,
       t.from_osmid AS from_osmid,
       t.to_osmid AS to_osmid,
       t.report_id AS report_id,
       t.close_decision_id AS close_decision_id
"""

WCC_PASSABLE = """
CALL algo.WCC({
  nodeLabels: ['Intersection'],
  relationshipTypes: ['PASSABLE']
})
YIELD node, componentId
RETURN node.osmid AS osmid, componentId
"""

MAX_FLOW = """
MATCH (d:District {name: $district})<-[:IN_DISTRICT]-(src:Intersection)
WITH collect(src) AS sources
MATCH (:Shelter)-[:ACCESS]->(sink:Intersection)
WITH sources, collect(DISTINCT sink) AS targets
CALL algo.maxFlow({
  sourceNodes: sources,
  targetNodes: targets,
  relationshipTypes: ['ROAD'],
  capacityProperty: 'capacity'
})
YIELD edges, edgeFlows, maxFlow
RETURN maxFlow, edges, edgeFlows
"""

DISTRICT_EXITS = """
MATCH (inside:Intersection)-[:IN_DISTRICT]->(:District {name: $district})
MATCH (inside)-[r:ROAD {status: 'open'}]-(outside:Intersection)
WHERE NOT (outside)-[:IN_DISTRICT]->(:District {name: $district})
RETURN DISTINCT r.road_id AS road_id, r.name AS name
"""

EXPLAIN_DECISION = """
MATCH (d:Decision {id: $id})
OPTIONAL MATCH (d)-[:BASED_ON]->(evidence)
OPTIONAL MATCH (d)-[:SUPERSEDES]->(prev:Decision)
OPTIONAL MATCH (a:Agent)-[:MADE]->(d)
RETURN d.id AS id,
       d.kind AS kind,
       d.explanation AS explanation,
       d.algo AS algo,
       d.at AS at,
       prev.id AS previous_id,
       a.name AS agent,
       collect(DISTINCT evidence.id) AS evidence_ids
"""
