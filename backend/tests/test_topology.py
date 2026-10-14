import heapq

from floodline.network import CLOSURE_ROAD_ID, ROADS, UNIT_OSMID


def _graph(skip: str | None = None) -> dict[int, list[tuple[int, int]]]:
    graph: dict[int, list[tuple[int, int]]] = {}
    for road in ROADS:
        if road.road_id == skip:
            continue
        graph.setdefault(road.a, []).append((road.b, road.travel_s))
        graph.setdefault(road.b, []).append((road.a, road.travel_s))
    return graph


def _shortest(graph, src: int, dst: int):
    queue = [(0, src, [src])]
    seen: set[int] = set()
    while queue:
        cost, node, path = heapq.heappop(queue)
        if node in seen:
            continue
        seen.add(node)
        if node == dst:
            return cost, path
        for nxt, weight in graph.get(node, []):
            if nxt not in seen:
                heapq.heappush(queue, (cost + weight, nxt, path + [nxt]))
    return None


def test_bridge_is_the_shortest_way_to_peset():
    found = _shortest(_graph(), UNIT_OSMID, 1003)
    assert found == (215, [1005, 1004, 1009, 1003])


def test_closing_the_bridge_forces_a_longer_detour():
    found = _shortest(_graph(CLOSURE_ROAD_ID), UNIT_OSMID, 1003)
    assert found == (320, [1005, 1007, 1008, 1009, 1003])
    assert (1005, 1004) not in set(zip(found[1], found[1][1:]))


def test_peset_stays_closer_than_la_fe_after_the_closure():
    graph = _graph(CLOSURE_ROAD_ID)
    peset = _shortest(graph, UNIT_OSMID, 1003)
    lafe = _shortest(graph, UNIT_OSMID, 1011)
    assert peset is not None and lafe is not None
    assert peset[0] < lafe[0]
