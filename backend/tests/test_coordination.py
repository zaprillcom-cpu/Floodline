import socket

import pytest

from floodline.db import get_graph, rows
from floodline.scenario import file_report, reset_scenario
from floodline.tools import claim_task, create_task


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


pytestmark = pytest.mark.skipif(not _db_up(), reason="FalkorDB is not listening on localhost:6379")


def test_claim_takes_highest_priority_and_requires_the_agent():
    reset_scenario()
    graph = get_graph()
    now = "2024-10-29T14:05:00Z"
    low = create_task(
        graph,
        kind="reassess",
        status="open",
        priority=1,
        created_at="2024-10-29T14:00:00Z",
        from_osmid=1005,
        to_osmid=1003,
    )
    high = create_task(
        graph,
        kind="reassess",
        status="open",
        priority=9,
        created_at="2024-10-29T14:00:01Z",
        from_osmid=1005,
        to_osmid=1003,
    )
    first = claim_task(graph, "Dispatcher", "reassess", now)
    second = claim_task(graph, "Dispatcher", "reassess", now)
    assert first is not None and first["id"] == high
    assert second is not None and second["id"] == low
    assert claim_task(graph, "Dispatcher", "reassess", now) is None

    leftover = create_task(
        graph,
        kind="reassess",
        status="open",
        priority=1,
        created_at="2024-10-29T14:06:00Z",
        from_osmid=1005,
        to_osmid=1003,
    )
    assert claim_task(graph, "Nobody", "reassess", now) is None
    status = rows(graph.query("MATCH (t:Task {id: $id}) RETURN t.status", {"id": leftover}))
    assert status[0][0] == "open"

    claimed = rows(
        graph.query(
            """
            MATCH (a:Agent {name: 'Dispatcher'})-[c:CLAIMED]->(t:Task)
            RETURN t.id, a.heartbeat_at, c.at
            """
        )
    )
    by_id = {task_id: (heartbeat, at) for task_id, heartbeat, at in claimed}
    assert set(by_id) == {high, low}
    assert all(heartbeat == now and at == now for heartbeat, at in by_id.values())


def test_scout_handoff_and_dispatcher_evidence():
    reset_scenario()
    state = file_report()
    scout = state["scout"]
    dispatcher = state["dispatcher"]
    assert scout["callsign"] == "A-7"
    assert scout["origin_osmid"] == 1005
    assert dispatcher["claimed"] is True
    evidence = dispatcher["evidence"]
    assert scout["report_id"] in evidence["evidence_ids"]
    assert scout["decision_id"] in evidence["evidence_ids"]
    assert evidence["agent"] == "Dispatcher"
    assert evidence["previous_id"]
    assert "A-7" in dispatcher["explanation"]

    graph = get_graph()
    handoff = rows(
        graph.query(
            """
            MATCH (a:Task {kind: 'ingest_report'})-[h:HANDOFF]->(b:Task {id: $id})
            RETURN a.status, b.status, h.reason
            """,
            {"id": scout["task_id"]},
        )
    )
    assert [tuple(row) for row in handoff] == [
        ("done", "done", "road closed; recompute the active route")
    ]
    claimed = rows(
        graph.query(
            """
            MATCH (:Agent {name: 'Dispatcher'})-[:CLAIMED]->(t:Task {id: $id})
            RETURN t.status
            """,
            {"id": dispatcher["task_id"]},
        )
    )
    assert [tuple(row) for row in claimed] == [("done",)]
    affects = rows(
        graph.query(
            """
            MATCH (d:Decision {id: $id})-[:AFFECTS]->(:Unit {callsign: 'A-7'})
            RETURN d.kind
            """,
            {"id": dispatcher["decision_id"]},
        )
    )
    assert [tuple(row) for row in affects] == [("route",)]
