import socket

import pytest

from floodline.db import get_graph, rows
from floodline.replay import TICKS, assert_replay, play


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


def test_replay_clock_is_deterministic():
    first = play()
    second = play()
    assert first == second
    assert_replay(first)
    graph = get_graph()
    stored = rows(graph.query("MATCH (t:Tick) RETURN t.seq, t.t ORDER BY t.seq"))
    assert [tuple(row) for row in stored] == [(spec["seq"], spec["t"]) for spec in TICKS]
    incident = rows(
        graph.query(
            """
            MATCH (inc:Incident {id: 'inc-seed-1'})-[:ACCESS]->(i:Intersection)
            RETURN inc.kind, inc.severity, inc.source, i.osmid
            """
        )
    )
    assert [tuple(row) for row in incident] == [("medical", 4, "synthetic", 1005)]
    linked = rows(
        graph.query(
            """
            MATCH (d:Decision)-[:AT_TICK]->(t:Tick)
            RETURN t.seq, count(d)
            ORDER BY t.seq
            """
        )
    )
    assert all(count >= 1 for _seq, count in linked)
    assert len(linked) == 3
