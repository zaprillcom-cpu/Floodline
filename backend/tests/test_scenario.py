import socket

import pytest

from floodline.scenario import assert_reroute, file_report, reset_scenario


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


def test_closure_changes_the_route():
    reset_scenario()
    state = file_report()
    assert_reroute(state)
    assert state["routes"]["before"]["travel_s"] == 215
    assert state["routes"]["after"]["travel_s"] == 320
    assert state["dispatcher"]["claimed"] is True
    assert state["narrative"]["closure"]
    assert "V-31" in state["narrative"]["route"] or "Paiporta" in state["narrative"]["route"]
