"""Build a compact SQLite street region from a tag-filtered OSM XML file.

Geofabrik PBF extracts are cropped with osmium, then this script keeps drive
roads plus shelter and hospital points. Runtime loads crop a few kilometres
out of the SQLite file, so Railway does not need Overpass or the whole PBF.
"""

from __future__ import annotations

import argparse
import sqlite3
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx

from floodline.network import haversine_m
from floodline.osm_network import FALLBACK_SPEED_KPH, prepare_drive_graph

SKIP_HIGHWAYS = {
    "abandoned",
    "bus_stop",
    "construction",
    "corridor",
    "cycleway",
    "elevator",
    "escalator",
    "footway",
    "path",
    "pedestrian",
    "planned",
    "platform",
    "proposed",
    "raceway",
    "steps",
    "track",
}
SHELTER_AMENITIES = {"school", "community_centre", "social_facility"}
SPEED_KPH = {
    "motorway": 80,
    "motorway_link": 50,
    "trunk": 60,
    "trunk_link": 40,
    "primary": 50,
    "primary_link": 40,
    "secondary": 40,
    "secondary_link": 30,
    "tertiary": 30,
    "tertiary_link": 25,
    "residential": 30,
    "living_street": 15,
    "service": 20,
    "unclassified": 30,
    "busway": 30,
    "road": 30,
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _tags(element: ET.Element) -> dict[str, str]:
    found = {}
    for child in element:
        if _local(child.tag) == "tag":
            key = child.attrib.get("k")
            if key:
                found[key] = child.attrib.get("v") or ""
    return found


def _speed(highway: str, maxspeed: str) -> float:
    text = (maxspeed or "").lower().replace("km/h", "").replace("kph", "").strip()
    if text.endswith("mph"):
        try:
            return max(10.0, min(130.0, float(text[:-3].strip()) * 1.60934))
        except ValueError:
            pass
    try:
        if text:
            return max(10.0, min(130.0, float(text.split()[0])))
    except ValueError:
        pass
    return float(SPEED_KPH.get(highway, FALLBACK_SPEED_KPH))


def _oneway(tags: dict[str, str]) -> int:
    value = (tags.get("oneway") or "").lower()
    if value in {"yes", "true", "1"}:
        return 1
    if value in {"-1", "reverse"}:
        return -1
    if tags.get("junction") == "roundabout":
        return 1
    return 0


def parse_osm(path: Path):
    nodes: dict[int, tuple[float, float]] = {}
    ways = []
    places = []

    def consider_place(ident: str, tags: dict, lat: float, lon: float) -> None:
        amenity = tags.get("amenity") or ""
        leisure = tags.get("leisure") or ""
        name = (tags.get("name") or "").strip()
        if amenity in SHELTER_AMENITIES or leisure == "sports_centre":
            kind = amenity or leisure
            places.append(
                {
                    "id": ident,
                    "name": (name or kind.replace("_", " ").title())[:120],
                    "kind": kind[:40],
                    "role": "shelter",
                    "lat": lat,
                    "lon": lon,
                }
            )
        elif amenity == "hospital":
            places.append(
                {
                    "id": ident,
                    "name": (name or "Hospital")[:120],
                    "kind": "hospital",
                    "role": "hospital",
                    "lat": lat,
                    "lon": lon,
                }
            )

    for _event, element in ET.iterparse(path, events=("end",)):
        tag = _local(element.tag)
        if tag == "node" and "id" in element.attrib and "lat" in element.attrib:
            node_id = int(element.attrib["id"])
            lat = float(element.attrib["lat"])
            lon = float(element.attrib["lon"])
            nodes[node_id] = (lat, lon)
            consider_place(f"osm-node-{node_id}", _tags(element), lat, lon)
            element.clear()
        elif tag == "way":
            tags = _tags(element)
            refs = [int(child.attrib["ref"]) for child in element if _local(child.tag) == "nd" and child.attrib.get("ref")]
            highway = tags.get("highway") or ""
            if highway and highway not in SKIP_HIGHWAYS and len(refs) >= 2:
                ways.append(
                    {
                        "id": int(element.attrib["id"]),
                        "refs": refs,
                        "name": (tags.get("name") or highway)[:120],
                        "highway": highway[:40],
                        "speed": _speed(highway, tags.get("maxspeed") or ""),
                        "oneway": _oneway(tags),
                    }
                )
            points = [nodes[ref] for ref in refs if ref in nodes]
            if points:
                lat = sum(point[0] for point in points) / len(points)
                lon = sum(point[1] for point in points) / len(points)
                consider_place(f"osm-way-{element.attrib['id']}", tags, lat, lon)
            element.clear()
        elif tag in {"relation", "bounds"}:
            element.clear()
    return nodes, ways, places


def drive_graph(nodes, ways):
    membership: dict[int, int] = defaultdict(int)
    endpoints = set()
    for way in ways:
        refs = [ref for ref in way["refs"] if ref in nodes]
        way["refs"] = refs
        if len(refs) < 2:
            continue
        endpoints.add(refs[0])
        endpoints.add(refs[-1])
        for ref in set(refs):
            membership[ref] += 1
    junctions = {ref for ref, count in membership.items() if count >= 2 or ref in endpoints}

    graph = nx.MultiDiGraph()
    for node_id in junctions:
        lat, lon = nodes[node_id]
        graph.add_node(node_id, x=lon, y=lat)

    for way in ways:
        refs = way["refs"]
        if len(refs) < 2:
            continue
        last = None
        length = 0.0
        prev = None
        for ref in refs:
            if prev is not None and ref in nodes and prev in nodes:
                length += haversine_m(nodes[prev][0], nodes[prev][1], nodes[ref][0], nodes[ref][1])
            if ref in junctions:
                if last is not None and ref != last and length > 0:
                    _add_edge(graph, last, ref, way, length)
                last = ref
                length = 0.0
            prev = ref
    return graph


def _add_edge(graph, src: int, dst: int, way: dict, length: float) -> None:
    speed = way["speed"]
    travel = max(1, int(round(length / (speed / 3.6))))
    data = {
        "osmid": way["id"],
        "name": way["name"],
        "highway": way["highway"],
        "length": max(1.0, length),
        "speed_kph": speed,
        "travel_time": travel,
    }
    if way["oneway"] >= 0:
        graph.add_edge(src, dst, **data)
    if way["oneway"] <= 0:
        graph.add_edge(dst, src, **data)


def write_db(path: Path, prepared: dict, places: list[dict], meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute(
        """
        CREATE TABLE nodes (
          osmid INTEGER PRIMARY KEY,
          lat REAL NOT NULL,
          lon REAL NOT NULL,
          name TEXT NOT NULL
        )
        """
    )
    db.execute("CREATE INDEX nodes_lat_lon ON nodes(lat, lon)")
    db.execute(
        """
        CREATE TABLE edges (
          src INTEGER NOT NULL,
          dst INTEGER NOT NULL,
          road_id TEXT NOT NULL,
          name TEXT NOT NULL,
          osm_way TEXT NOT NULL,
          highway TEXT NOT NULL,
          length_m INTEGER NOT NULL,
          speed_kph REAL NOT NULL,
          travel_s INTEGER NOT NULL,
          capacity INTEGER NOT NULL
        )
        """
    )
    db.execute("CREATE INDEX edges_src ON edges(src)")
    db.execute("CREATE INDEX edges_dst ON edges(dst)")
    db.execute(
        """
        CREATE TABLE places (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          kind TEXT NOT NULL,
          role TEXT NOT NULL,
          lat REAL NOT NULL,
          lon REAL NOT NULL
        )
        """
    )
    db.execute("CREATE INDEX places_lat_lon ON places(lat, lon)")
    db.executemany("INSERT INTO meta VALUES (?, ?)", [(key, str(value)) for key, value in meta.items()])
    db.executemany(
        "INSERT INTO nodes VALUES (?, ?, ?, ?)",
        [(node["osmid"], node["lat"], node["lon"], node["name"]) for node in prepared["nodes"]],
    )
    db.executemany(
        "INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                edge["src"],
                edge["dst"],
                edge["road_id"],
                edge["name"],
                edge["osm_way"],
                edge["highway"],
                edge["length_m"],
                edge["speed_kph"],
                edge["travel_s"],
                edge["capacity"],
            )
            for edge in prepared["edges"]
        ],
    )
    seen = set()
    rows = []
    for place in places:
        if place["id"] in seen:
            continue
        seen.add(place["id"])
        rows.append((place["id"], place["name"], place["kind"], place["role"], place["lat"], place["lon"]))
    db.executemany("INSERT INTO places VALUES (?, ?, ?, ?, ?, ?)", rows)
    db.commit()
    db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--osm", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--place", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--west", type=float, required=True)
    parser.add_argument("--south", type=float, required=True)
    parser.add_argument("--east", type=float, required=True)
    parser.add_argument("--north", type=float, required=True)
    parser.add_argument("--center-lat", type=float, required=True)
    parser.add_argument("--center-lon", type=float, required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    nodes, ways, places = parse_osm(Path(args.osm))
    graph = drive_graph(nodes, ways)
    prepared = prepare_drive_graph(
        graph,
        bbox={"west": args.west, "south": args.south, "east": args.east, "north": args.north},
        district_name=args.place,
    )
    out = Path(args.out)
    write_db(
        out,
        prepared,
        places,
        {
            "region": args.region,
            "place": args.place,
            "west": args.west,
            "south": args.south,
            "east": args.east,
            "north": args.north,
            "center_lat": args.center_lat,
            "center_lon": args.center_lon,
            "source": args.source,
            "license": "ODbL-1.0",
            "attribution": "© OpenStreetMap contributors",
        },
    )
    print(
        f"{out} nodes={len(prepared['nodes'])} edges={len(prepared['edges'])} "
        f"places={len(places)} bytes={out.stat().st_size}"
    )


if __name__ == "__main__":
    main()
