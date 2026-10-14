import socket

import pytest

from floodline.bulk import load_records
from floodline.config import Settings
from floodline.db import connect
from floodline.osm_demo import prove_cch_closure


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


def _diamond():
    def node(osmid, name, lat, lon):
        return {
            "osmid": osmid,
            "name": name,
            "lat": lat,
            "lon": lon,
            "district": "Paiporta",
            "role": "junction",
        }

    def edge(src, dst, road_id, travel_s):
        return {
            "src": src,
            "dst": dst,
            "road_id": road_id,
            "name": road_id,
            "osm_way": "1",
            "highway": "primary",
            "length_m": travel_s * 10,
            "speed_kph": 36.0,
            "travel_s": travel_s,
            "capacity": 500,
        }

    return {
        "source": "test",
        "attribution": "synthetic",
        "bbox": {},
        "district_method": "hand",
        "nodes": [
            node(1, "south", 39.42, -0.42),
            node(2, "bridge", 39.43, -0.41),
            node(3, "hospital", 39.45, -0.40),
            node(4, "detour", 39.43, -0.39),
        ],
        "edges": [
            edge(1, 2, "short", 10),
            edge(2, 1, "short", 10),
            edge(2, 3, "short-b", 10),
            edge(3, 2, "short-b", 10),
            edge(1, 4, "long", 40),
            edge(4, 1, "long", 40),
            edge(4, 3, "long-b", 40),
            edge(3, 4, "long-b", 40),
        ],
        "districts": [
            {
                "name": "Paiporta",
                "population": 10,
                "zone_id": "test",
                "population_source": "synthetic",
            }
        ],
    }


def test_one_closure_changes_the_cch_route():
    settings = Settings.from_env()
    db = connect(settings)
    graph = db.select_graph("floodline_bulk_test")
    try:
        graph.delete()
    except Exception:
        pass
    graph = db.select_graph("floodline_bulk_test")
    loaded = load_records(graph, _diamond())
    assert loaded["cch"]["ok"] is True
    assert loaded["intersections"] == 4
    assert loaded["road_edges"] == 8
    proof = prove_cch_closure(graph, 1, 3)
    assert proof["before"].algo == "cch"
    assert proof["after"].algo == "cch"
    assert proof["before"].travel_s == 20
    assert proof["after"].travel_s == 80
    assert proof["closed"]["road_id"] in {"short", "short-b"}
    assert proof["before"].osmids != proof["after"].osmids
