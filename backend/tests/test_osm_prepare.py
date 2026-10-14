import networkx as nx

from floodline.osm_network import (
    BBOX,
    capacity_for,
    prepare_drive_graph,
)


def _graph():
    graph = nx.MultiDiGraph()
    # A small two-way primary plus a one-way residential spur, near Paiporta.
    graph.add_node(1, x=-0.4180, y=39.4290)
    graph.add_node(2, x=-0.4100, y=39.4320)
    graph.add_node(3, x=-0.4050, y=39.4360)
    graph.add_edge(
        1, 2, osmid=10, name="Camí de Prova", highway="primary", length=400,
        speed_kph=50, travel_time=28.8, oneway=False,
    )
    graph.add_edge(
        2, 1, osmid=10, name="Camí de Prova", highway="primary", length=400,
        speed_kph=50, travel_time=28.8, oneway=False,
    )
    graph.add_edge(
        2, 3, osmid=11, name="Carrer Estret", highway="residential", length=100,
        speed_kph=30, travel_time=12, oneway=True,
    )
    # A slower parallel edge in the same direction should lose to the fast one,
    # and its capacity should be kept.
    graph.add_edge(
        1, 2, osmid=99, name="Camí lent", highway="residential", length=400,
        speed_kph=20, travel_time=80, oneway=False,
    )
    return graph


def test_bbox_covers_the_flood_towns_and_peset():
    west, south, east, north = BBOX
    assert west < -0.418 < east  # Paiporta
    assert west < -0.391 < east  # Alfafar
    assert south < 39.422 < north
    assert south < 39.4518 < north  # Hospital Doctor Peset
    assert north - south < 0.06
    assert east - west < 0.08


def test_capacity_prefers_the_higher_class():
    assert capacity_for("primary") == 1200
    assert capacity_for(["residential", "primary"]) == 1200
    assert capacity_for("not-a-class") == 400


def test_prepare_pairs_two_way_roads_and_keeps_oneway_distinct():
    prepared = prepare_drive_graph(_graph())
    edges = {(edge["src"], edge["dst"]): edge for edge in prepared["edges"]}
    assert set(edges) == {(1, 2), (2, 1), (2, 3)}
    assert edges[(1, 2)]["road_id"] == edges[(2, 1)]["road_id"]
    assert edges[(1, 2)]["travel_s"] == 29
    assert edges[(1, 2)]["capacity"] == 1200 + 350
    assert edges[(2, 3)]["road_id"] != edges[(1, 2)]["road_id"]
    assert edges[(2, 3)]["capacity"] == 350
    districts = {node["osmid"]: node["district"] for node in prepared["nodes"]}
    assert districts[1] == "Paiporta"
    assert all(node["role"] == "junction" for node in prepared["nodes"])
