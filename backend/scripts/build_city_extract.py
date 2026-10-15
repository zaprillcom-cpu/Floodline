"""Build a bundled city extract from a Geofabrik crop.

Public Overpass was down, so the Mumbai and New York files were cropped from
Geofabrik PBF extracts with osmium, then run through the same
``prepare_drive_graph`` pipeline as ``valencia_corridor.json``.

Example (files already cropped and tag-filtered):

    PYTHONPATH=backend python backend/scripts/build_city_extract.py \
      --osm /tmp/geofabrik/mumbai.osm \
      --place Mumbai --district Mumbai \
      --lat 19.076 --lon 72.877 --radius-m 3000 \
      --geofabrik https://download.geofabrik.de/asia/india/western-zone-latest.osm.pbf \
      --out backend/data/osm/mumbai.json
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import osmnx as ox

from floodline.network import haversine_m
from floodline.osm_network import (
    FALLBACK_SPEED_KPH,
    SHELTER_CAP,
    prepare_drive_graph,
)

# Same street classes the live Overpass query leaves out.
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
HOSPITAL_AMENITIES = {"hospital", "clinic"}


def _highway_values(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    return [str(value)]


def _drive_graph(path: Path):
    graph = ox.graph_from_xml(path, simplify=False, retain_all=True)
    drop = []
    for src, dst, key, data in graph.edges(keys=True, data=True):
        classes = _highway_values(data.get("highway"))
        if not classes or all(item in SKIP_HIGHWAYS for item in classes):
            drop.append((src, dst, key))
    graph.remove_edges_from(drop)
    graph.remove_nodes_from([node for node, degree in graph.degree() if degree == 0])
    try:
        graph = ox.simplification.simplify_graph(graph)
    except Exception:
        pass
    try:
        graph = ox.truncate.largest_component(graph, strongly=False)
    except Exception:
        pass
    graph = ox.add_edge_speeds(graph, fallback=FALLBACK_SPEED_KPH)
    graph = ox.add_edge_travel_times(graph)
    for _src, _dst, _key, data in graph.edges(keys=True, data=True):
        data.pop("geometry", None)
    return graph


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _child_tags(element: ET.Element) -> dict[str, str]:
    tags = {}
    for child in element:
        if _local(child.tag) == "tag":
            key = child.attrib.get("k")
            if key:
                tags[key] = child.attrib.get("v") or ""
    return tags


def _places(path: Path) -> tuple[list[dict], list[dict]]:
    nodes: dict[int, tuple[float, float]] = {}
    shelters: list[dict] = []
    hospitals: list[dict] = []

    def consider(kind_id: str, tags: dict, lat: float, lon: float) -> None:
        amenity = tags.get("amenity") or ""
        leisure = tags.get("leisure") or ""
        name = (tags.get("name") or "").strip()
        if amenity in SHELTER_AMENITIES or leisure == "sports_centre":
            kind = amenity or leisure
            shelters.append(
                {
                    "id": kind_id,
                    "name": (name or kind.replace("_", " ").title())[:120],
                    "kind": kind[:40],
                    "lat": lat,
                    "lon": lon,
                }
            )
        if amenity in HOSPITAL_AMENITIES:
            hospitals.append(
                {
                    "id": kind_id,
                    "name": (name or amenity.replace("_", " ").title())[:120],
                    "amenity": amenity,
                    "lat": lat,
                    "lon": lon,
                }
            )

    for _event, element in ET.iterparse(path, events=("end",)):
        tag = _local(element.tag)
        if tag == "node" and "lat" in element.attrib:
            nodes[int(element.attrib["id"])] = (float(element.attrib["lat"]), float(element.attrib["lon"]))
            consider(f"osm-node-{element.attrib['id']}", _child_tags(element), float(element.attrib["lat"]), float(element.attrib["lon"]))
            element.clear()
        elif tag == "way":
            refs = []
            for child in element:
                if _local(child.tag) == "nd" and child.attrib.get("ref"):
                    refs.append(int(child.attrib["ref"]))
            points = [nodes[ref] for ref in refs if ref in nodes]
            if points:
                lat = sum(point[0] for point in points) / len(points)
                lon = sum(point[1] for point in points) / len(points)
                consider(f"osm-way-{element.attrib['id']}", _child_tags(element), lat, lon)
            element.clear()
        elif tag == "relation":
            element.clear()
    return shelters, hospitals


def _nearest(records: list[dict], lat: float, lon: float, limit: int) -> list[dict]:
    ranked = sorted(records, key=lambda item: haversine_m(lat, lon, item["lat"], item["lon"]))
    chosen = []
    seen = set()
    for item in ranked:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        chosen.append(item)
        if len(chosen) >= limit:
            break
    return chosen


def build(args: argparse.Namespace) -> dict:
    west, south, east, north = _bbox(args.lat, args.lon, args.radius_m)
    graph = _drive_graph(Path(args.osm))
    prepared = prepare_drive_graph(
        graph,
        bbox={"west": west, "south": south, "east": east, "north": north},
        district_name=args.district,
    )
    shelters, hospitals = _places(Path(args.osm))
    # Prefer hospitals over clinics when both are present.
    real_hospitals = [item for item in hospitals if item["amenity"] == "hospital"] or hospitals
    prepared["place"] = args.place
    prepared["center"] = {"lat": args.lat, "lon": args.lon}
    prepared["radius_m"] = args.radius_m
    prepared["tool"] = "osmium+osmnx"
    prepared["geofabrik"] = args.geofabrik
    prepared["shelters"] = _nearest(shelters, args.lat, args.lon, SHELTER_CAP)
    prepared["hospitals"] = _nearest(real_hospitals, args.lat, args.lon, 4)
    prepared["osmnx_version"] = getattr(ox, "__version__", "")
    return prepared


def _bbox(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    import math

    dlat = radius_m / 111_320
    cos_lat = math.cos(math.radians(lat))
    dlon = radius_m / (111_320 * cos_lat) if abs(cos_lat) > 0.01 else dlat
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a bundled Floodline city extract")
    parser.add_argument("--osm", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--place", required=True)
    parser.add_argument("--district", required=True)
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--radius-m", type=int, default=3000)
    parser.add_argument("--geofabrik", required=True)
    args = parser.parse_args()
    prepared = build(args)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prepared, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(
        f"{path} nodes={len(prepared['nodes'])} edges={len(prepared['edges'])} "
        f"shelters={len(prepared['shelters'])} hospitals={len(prepared['hospitals'])} "
        f"bytes={path.stat().st_size}"
    )


if __name__ == "__main__":
    main()
