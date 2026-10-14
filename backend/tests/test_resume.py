import socket

import pytest

from floodline.agents.dispatcher import resume
from floodline.agents.scout import handle_report
from floodline.config import T_CLOSE
from floodline.network import DEMO_REPORT
from floodline.db import get_graph, rows
from floodline.scenario import assert_reroute, reset_scenario, snapshot
from floodline.tools import claim_task


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


def _active_routes() -> int:
    return sum(
        1
        for item in snapshot()["decisions"]
        if item["kind"] == "route" and not item["superseded"]
    )


def test_resume_finishes_the_task_claimed_before_the_crash():
    reset_scenario()
    graph = get_graph()
    handle_report(graph, DEMO_REPORT, at=T_CLOSE)
    task = claim_task(graph, "Dispatcher", "reassess", T_CLOSE)
    assert task is not None
    assert _active_routes() == 1

    result = resume(graph, at="2024-10-29T14:03:00Z")
    assert len(result["finished"]) == 1
    assert result["finished"][0]["resumed"] is True
    assert result["finished"][0]["task_id"] == task["id"]
    assert "resumed from the claimed task" in result["finished"][0]["explanation"]
    state = snapshot()
    assert_reroute(state)
    heartbeat = rows(
        graph.query("MATCH (a:Agent {name: 'Dispatcher'}) RETURN a.heartbeat_at")
    )
    assert heartbeat[0][0] == "2024-10-29T14:03:00Z"
    status = rows(graph.query("MATCH (t:Task {id: $id}) RETURN t.status", {"id": task["id"]}))
    assert status[0][0] == "done"


def test_resume_claims_an_open_task_when_the_process_died_before_claim():
    reset_scenario()
    graph = get_graph()
    handle_report(graph, DEMO_REPORT, at=T_CLOSE)
    result = resume(graph, at=T_CLOSE)
    assert len(result["finished"]) == 1
    assert result["finished"][0]["resumed"] is False
    assert_reroute(snapshot())
