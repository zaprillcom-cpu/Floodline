import json

from floodline.osm_network import EXTRACT_PATH, bbox_dict


def test_committed_valencia_extract_matches_the_documented_bbox():
    assert EXTRACT_PATH.is_file(), "backend/data/osm/valencia_corridor.json is the offline OSM extract"
    assert EXTRACT_PATH.parts[-4:] == ("backend", "data", "osm", "valencia_corridor.json")
    data = json.loads(EXTRACT_PATH.read_text(encoding="utf-8"))
    assert data["source"] == "osm"
    assert data["license"] == "ODbL-1.0"
    assert "OpenStreetMap" in data["attribution"]
    assert data["bbox"] == bbox_dict()
    assert data["network_type"] == "drive"
    assert len(data["nodes"]) == 2526
    assert len(data["edges"]) == 4742
    districts = {node["district"] for node in data["nodes"]}
    assert {"Paiporta", "Alfafar", "Sedaví", "Benetússer", "Valencia"} <= districts
    edge = data["edges"][0]
    for field in ("road_id", "travel_s", "capacity", "src", "dst", "osm_way"):
        assert field in edge
    assert edge["travel_s"] >= 1
    assert edge["capacity"] >= 1
