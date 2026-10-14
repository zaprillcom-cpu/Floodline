"""Point-to-point routing. Prefer CCH, then A*, then algo.SPpaths."""

from __future__ import annotations

import json
from dataclasses import dataclass

from floodline import cypher
from floodline.config import UNREACHABLE_WEIGHT
from floodline.db import rows


class NoRoute(RuntimeError):
    pass


@dataclass
class Route:
    algo: str
    travel_s: int
    osmids: list[int]
    names: list[str]

    def as_dict(self) -> dict:
        return {
            "algo": self.algo,
            "travel_s": self.travel_s,
            "osmids": self.osmids,
            "names": self.names,
        }


_ROUTERS = (
    ("cch", cypher.ROUTE_CCH),
    ("astar", cypher.ROUTE_ASTAR),
    ("sppaths", cypher.ROUTE_SPPATHS),
)


def _as_int_list(value) -> list[int]:
    if value is None:
        return []
    if isinstance(value, str):
        value = json.loads(value)
    return [int(v) for v in value]


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = json.loads(value)
    return [str(v) for v in value]


def _parse_route(algo: str, result) -> Route | None:
    found = rows(result)
    if not found:
        return None
    travel_s, osmids, names = found[0]
    weight = float(travel_s)
    if weight >= UNREACHABLE_WEIGHT:
        return None
    return Route(
        algo=algo,
        travel_s=int(round(weight)),
        osmids=_as_int_list(osmids),
        names=_as_str_list(names),
    )


def _edge_is_closed(graph, src: int, dst: int) -> bool:
    result = graph.query(
        """
        MATCH (a:Intersection {osmid: $src})-[r:ROAD]->(b:Intersection {osmid: $dst})
        RETURN r.status AS status, r.travel_s AS travel_s
        ORDER BY r.travel_s ASC
        LIMIT 1
        """,
        {"src": src, "dst": dst},
    )
    found = rows(result)
    if not found:
        return False
    status, travel_s = found[0]
    if status == "closed":
        return True
    return travel_s is not None and float(travel_s) >= UNREACHABLE_WEIGHT


def path_uses_closed_edge(graph, osmids: list[int]) -> bool:
    return any(
        _edge_is_closed(graph, src, dst) for src, dst in zip(osmids, osmids[1:])
    )


def try_route(graph, algo: str, src: int, dst: int) -> Route | None:
    query = dict(_ROUTERS)[algo]
    result = graph.query(query, {"src": src, "dst": dst})
    route = _parse_route(algo, result)
    if route is None:
        return None
    if path_uses_closed_edge(graph, route.osmids):
        return None
    return route


def probe_routers(graph, src: int, dst: int) -> dict:
    """Ask each router for one path and remember which ones answered."""
    available: dict[str, dict] = {}
    for algo, _query in _ROUTERS:
        try:
            route = try_route(graph, algo, src, dst)
        except Exception as exc:
            available[algo] = {"ok": False, "error": str(exc)}
            continue
        if route is None:
            available[algo] = {"ok": False, "error": "no path under the closure threshold"}
        else:
            available[algo] = {"ok": True, "travel_s": route.travel_s}
    chosen = next((name for name, _query in _ROUTERS if available[name].get("ok")), None)
    if chosen is None:
        raise NoRoute(f"No FalkorDB router returned a path: {available}")
    graph.query(
        """
        MERGE (m:Meta {key: 'router'})
        SET m.algo = $algo, m.available = $available
        """,
        {"algo": chosen, "available": json.dumps(available)},
    )
    return {"chosen": chosen, "available": available}


def active_algo(graph) -> str | None:
    result = graph.query(
        "MATCH (m:Meta {key: 'router'}) RETURN m.algo AS algo, m.available AS available"
    )
    found = rows(result)
    if not found:
        return None
    return found[0][0]


def router_status(graph) -> dict:
    result = graph.query(
        "MATCH (m:Meta {key: 'router'}) RETURN m.algo AS algo, m.available AS available"
    )
    found = rows(result)
    if not found:
        return {"chosen": None, "available": {}}
    algo, available = found[0]
    if isinstance(available, str):
        available = json.loads(available)
    return {"chosen": algo, "available": available or {}}


def route(graph, src: int, dst: int) -> Route:
    """Shortest open path. Falls through CCH → A* → SPpaths when one is missing or stale."""
    status = router_status(graph)
    preferred = [name for name, _query in _ROUTERS]
    chosen = status.get("chosen")
    if chosen in preferred:
        preferred.remove(chosen)
        preferred.insert(0, chosen)
    errors: list[str] = []
    for algo in preferred:
        try:
            found = try_route(graph, algo, src, dst)
        except Exception as exc:
            errors.append(f"{algo}: {exc}")
            continue
        if found is None:
            errors.append(f"{algo}: no open path")
            continue
        return found
    raise NoRoute(
        f"No open route from {src} to {dst}. " + "; ".join(errors)
    )


def ensure_cch(graph) -> dict:
    """Create the travel-time CCH index. Failure is recorded; routing falls back."""
    try:
        graph.query(cypher.CREATE_CCH)
        return {"ok": True}
    except Exception as exc:
        message = str(exc)
        if "already exists" in message.lower():
            return {"ok": True, "note": "already exists"}
        return {"ok": False, "error": message}
