import socket

import pytest

from floodline.analysis import assert_paiporta_cutoff, cut_off_areas, evac_capacity, prove_cutoff
from floodline.db import get_graph
from floodline.scenario import reset_scenario


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


def test_open_network_can_reach_a_hospital():
    reset_scenario()
    graph = get_graph()
    areas = cut_off_areas(graph)
    paiporta = next(item for item in areas if item["name"] == "Paiporta")
    assert paiporta["cut_off"] is False
    evac = evac_capacity(graph, "Paiporta")
    assert evac["max_flow"] == 1200
    assert evac["critical_edge"]["name"] == "V-31 Paiporta bridge"
    assert evac["critical_edge"]["saturated"] is True
    assert evac["critical_edge"]["flow"] == 600


def test_closing_paiporta_exits_cuts_the_district_off():
    reset_scenario()
    result = prove_cutoff(get_graph())
    assert_paiporta_cutoff(result)
    after = result["after"]["evac"]
    assert after["max_flow"] == 0
    assert after["critical_edge"] is None
