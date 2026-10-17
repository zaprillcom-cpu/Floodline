"""Turn-by-turn steps from a path's edge names and bearings.

This is presentation on top of an existing route. It does not choose the path.
Street names are shown only when the edge actually has one; otherwise the step
says "continue" plus a distance.
"""

from __future__ import annotations

import math

from floodline.db import rows
from floodline.network import haversine_m

# OSM highway classes used as a stand-in when the way has no name.
_GENERIC = {
    "",
    "unnamed road",
    "road",
    "residential",
    "service",
    "unclassified",
    "living_street",
    "tertiary",
    "tertiary_link",
    "secondary",
    "secondary_link",
    "primary",
    "primary_link",
    "trunk",
    "trunk_link",
    "motorway",
    "motorway_link",
    "track",
    "busway",
}

_CARDINALS = (
    "north",
    "northeast",
    "east",
    "southeast",
    "south",
    "southwest",
    "west",
    "northwest",
)


def initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Forward azimuth in degrees, 0 = north, clockwise."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    y = math.sin(d_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def cardinal(bearing: float) -> str:
    return _CARDINALS[int((bearing + 22.5) // 45) % 8]


def _delta(a: float, b: float) -> float:
    return (b - a + 540.0) % 360.0 - 180.0


def street_name(name: str | None) -> str:
    text = (name or "").strip()
    if text.lower() in _GENERIC:
        return ""
    return text


def _instruction(prev_bearing: float | None, bearing: float, name: str, distance_m: int, first: bool) -> str:
    if first or prev_bearing is None:
        if name:
            return f"Head {cardinal(bearing)} on {name}"
        return f"Continue {distance_m} m"
    delta = _delta(prev_bearing, bearing)
    if abs(delta) < 35:
        if name:
            return f"Continue on {name}"
        return f"Continue {distance_m} m"
    if abs(delta) >= 150:
        phrase = "Make a U-turn"
    elif delta > 0:
        phrase = "Turn right"
    else:
        phrase = "Turn left"
    if name:
        return f"{phrase} onto {name}"
    return f"{phrase} and continue {distance_m} m"


def build_steps(hops: list[dict]) -> list[dict]:
    """Collapse consecutive hops that share a name and a similar bearing.

    Each hop needs lat1, lon1, lat2, lon2. length_m and name are optional.
    """
    groups: list[dict] = []
    for hop in hops:
        lat1, lon1 = float(hop["lat1"]), float(hop["lon1"])
        lat2, lon2 = float(hop["lat2"]), float(hop["lon2"])
        name = street_name(hop.get("name"))
        bearing = initial_bearing(lat1, lon1, lat2, lon2)
        length = hop.get("length_m")
        if length is None:
            length = haversine_m(lat1, lon1, lat2, lon2)
        distance_m = max(1, int(round(float(length))))
        road_id = str(hop.get("road_id") or "")
        if (
            groups
            and groups[-1]["name"] == name
            and abs(_delta(groups[-1]["end_bearing"], bearing)) < 35
        ):
            groups[-1]["distance_m"] += distance_m
            if road_id:
                groups[-1]["road_ids"].append(road_id)
            groups[-1]["end_bearing"] = bearing
            continue
        groups.append(
            {
                "name": name,
                "bearing": round(bearing, 1),
                "end_bearing": bearing,
                "distance_m": distance_m,
                "road_ids": [road_id] if road_id else [],
            }
        )

    steps = []
    cursor = 0
    prev = None
    for index, group in enumerate(groups):
        steps.append(
            {
                "instruction": _instruction(
                    prev, group["bearing"], group["name"], group["distance_m"], index == 0
                ),
                "name": group["name"],
                "distance_m": group["distance_m"],
                "from_m": cursor,
                "bearing": group["bearing"],
                "road_ids": group["road_ids"],
                "arrive": False,
            }
        )
        cursor += group["distance_m"]
        prev = group["end_bearing"]
    return steps


def path_hops(graph, osmids: list[int]) -> list[dict]:
    """Ordered hops for a node path. Missing edges fall back to node coordinates."""
    if len(osmids) < 2:
        return []
    pairs = [{"src": int(src), "dst": int(dst), "i": i} for i, (src, dst) in enumerate(zip(osmids, osmids[1:]))]
    found = rows(
        graph.query(
            """
            UNWIND $pairs AS pair
            MATCH (a:Intersection {osmid: pair.src})-[r:ROAD]->(b:Intersection {osmid: pair.dst})
            RETURN pair.i AS i, a.lat AS lat1, a.lon AS lon1, b.lat AS lat2, b.lon AS lon2,
                   r.name AS name, r.length_m AS length_m, r.road_id AS road_id, r.travel_s AS travel_s
            """,
            {"pairs": pairs},
        )
    )
    best: dict[int, dict] = {}
    for index, lat1, lon1, lat2, lon2, name, length_m, road_id, travel_s in found:
        item = {
            "lat1": float(lat1),
            "lon1": float(lon1),
            "lat2": float(lat2),
            "lon2": float(lon2),
            "name": name or "",
            "length_m": None if length_m is None else int(length_m),
            "road_id": road_id or "",
            "travel_s": int(travel_s or 0),
        }
        current = best.get(int(index))
        if current is None or item["travel_s"] < current["travel_s"]:
            best[int(index)] = item

    if len(best) == len(pairs):
        return [best[i] for i in range(len(pairs))]

    coords = {
        int(osmid): (float(lat), float(lon))
        for osmid, lat, lon in rows(
            graph.query(
                """
                UNWIND $ids AS osmid
                MATCH (i:Intersection {osmid: osmid})
                RETURN i.osmid, i.lat, i.lon
                """,
                {"ids": [int(v) for v in osmids]},
            )
        )
        if lat is not None and lon is not None
    }
    hops = []
    for index, pair in enumerate(pairs):
        if index in best:
            hops.append(best[index])
            continue
        start = coords.get(pair["src"])
        end = coords.get(pair["dst"])
        if start is None or end is None:
            continue
        hops.append(
            {
                "lat1": start[0],
                "lon1": start[1],
                "lat2": end[0],
                "lon2": end[1],
                "name": "",
                "length_m": haversine_m(start[0], start[1], end[0], end[1]),
                "road_id": "",
                "travel_s": 0,
            }
        )
    return hops


def collapse_names(names: list[str]) -> list[str]:
    """Drop blank labels and repeated consecutive street names.

    OSM paths name every intersection after the same road, so a title built
    from the raw node list reads "Aarey Road → Aarey Road".
    """
    collapsed: list[str] = []
    for name in names:
        text = str(name or "").strip()
        if not text:
            continue
        if collapsed and collapsed[-1].casefold() == text.casefold():
            continue
        collapsed.append(text)
    return collapsed


def dedupe_route_title(title: str) -> str:
    """Collapse consecutive duplicates in a 'Road → Road (12s)' title."""
    text = (title or "").strip()
    if "→" in text:
        arrow = "→"
    elif "->" in text:
        arrow = "->"
    else:
        return title or ""
    suffix = ""
    body = text
    if body.endswith(")") and "(" in body:
        head, _, tail = body.rpartition("(")
        if "→" not in tail and "->" not in tail:
            body = head.rstrip()
            suffix = f" ({tail}"
    parts = [part.strip() for part in body.split(arrow)]
    collapsed = collapse_names(parts)
    if not collapsed:
        return title or ""
    return f" {arrow} ".join(collapsed) + suffix


def line_coordinates(hops: list[dict]) -> list[list[float]]:
    coordinates: list[list[float]] = []
    for hop in hops:
        point = [round(float(hop["lon1"]), 7), round(float(hop["lat1"]), 7)]
        if not coordinates or coordinates[-1] != point:
            coordinates.append(point)
        coordinates.append([round(float(hop["lon2"]), 7), round(float(hop["lat2"]), 7)])
    return coordinates
