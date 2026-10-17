import socket

import pytest
from fastapi.testclient import TestClient

from floodline.graphview import memory_graph, summarise_components


def _db_up() -> bool:
    sock = socket.socket()
    sock.settimeout(1)
    try:
        sock.connect(("127.0.0.1", 6379))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def test_largest_component_is_main():
    main, sizes = summarise_components({1: 0, 2: 0, 3: 0, 4: 7})
    assert main == 0
    assert sizes == {0: 3, 7: 1}


def test_empty_components():
    assert summarise_components({}) == (None, {})


class _MemResult:
    def __init__(self, result_set):
        self.result_set = result_set


class _DuplicateGraph:
    def query(self, text, _params=None):
        if "MATCH (d:Decision)" in text:
            return _MemResult([])
        if "MATCH (t:Task)" in text:
            return _MemResult([])
        if "labels(n)" in text:
            return _MemResult(
                [
                    ("Report", "report-new", "water again", "water again", "t1", None, None, "duplicate"),
                    ("Report", "report-old", "water earlier", "water earlier", "t0", None, None, "open"),
                ]
            )
        if "type(r)" in text:
            return _MemResult([("report-new", "DUPLICATE_OF", "report-old")])
        return _MemResult([])


def test_memory_graph_returns_the_duplicate_of_edge():
    view = memory_graph(_DuplicateGraph())
    edge = next(item for item in view["edges"] if item["type"] == "DUPLICATE_OF")
    assert edge == {"source": "report-new", "target": "report-old", "type": "DUPLICATE_OF", "derived": False}
    ids = {node["id"] for node in view["nodes"]}
    assert {"report-new", "report-old"} <= ids


@pytest.mark.skipif(not _db_up(), reason="FalkorDB is not listening on localhost:6379")
def test_memory_and_network_after_a_report():
    from floodline.api import app
    from floodline.scenario import file_report, reset_scenario

    reset_scenario()
    file_report()
    client = TestClient(app)
    memory = client.get("/api/graph/memory").json()
    labels = {n["label"] for n in memory["nodes"]}
    assert {"Decision", "Report", "Agent"} <= labels
    types = {e["type"] for e in memory["edges"]}
    assert "MADE" in types and "BASED_ON" in types
    ids = {n["id"] for n in memory["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in memory["edges"])
    decision = next(n for n in memory["nodes"] if n["label"] == "Decision" and n["props"].get("algo"))
    assert decision["cypher"]

    net = client.get("/api/graph/network").json()
    kinds = {f["properties"]["kind"] for f in net["features"]}
    assert kinds == {"node", "edge"}
    assert net["stats"]["closed_edges"] >= 1
    assert any(f["properties"].get("on_route") for f in net["features"])
