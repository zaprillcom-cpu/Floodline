"""Shelter, citizen, geocode, and reachability operations.

FalkorDB does the work: point-index snap, name-match geocoding, NEAR plus
CCH (A* then SPpaths), WCC on PASSABLE, and Decision edges for a reroute.
"""

from __future__ import annotations

import json
import re

from floodline import cypher
from floodline.config import utc_now
from floodline.db import rows
from floodline.navigation import build_steps, collapse_names, path_hops
from floodline.network import haversine_m
from floodline.routing import NoRoute, route
from floodline.tools import best_hospital, next_seq

# Retired shared node from before per-browser ids. Do not create it, and do not reroute it.
LEGACY_CITIZEN = "citizen"
TOP_K = 5

_CODE = re.compile(r"\b([A-Za-z]{1,6}-\d{1,4}[A-Za-z]?)\b")
_PLACE = re.compile(
    r"\b(?:near|off|at|by)\s+([A-Za-z0-9][A-Za-z0-9'’.\-]*(?:\s+[A-Za-z0-9][A-Za-z0-9'’.\-]*){0,4})",
    re.IGNORECASE,
)
_ROADISH = re.compile(
    r"\b(?:road|rd|marg|street|st|lane|highway|flyover|path)\b|\b(?:no\.?|number)\s*\.?\s*\d{1,3}\b",
    re.IGNORECASE,
)
_ROAD_END = {"road", "rd", "marg", "street", "st", "lane", "highway", "flyover", "path"}
_GENERIC = {"road", "rd", "marg", "street", "st", "lane", "path", "highway", "flyover", "no", "number", "the"}
_CLIP_STOP = {"is", "was", "a", "an", "the", "and", "its", "it"}
_NUMBERED = re.compile(r"\b(?:no\.?|number)\s*\.?\s*(\d{1,3})\b", re.IGNORECASE)
_SHORT = re.compile(r"\b(SV|S\.V\.|MG|M\.G\.|WEH|GMLR|LBS|EEH|JVLR)\b", re.IGNORECASE)
# Spoken abbreviations → the words OSM actually stores on the road name.
_ALIASES = {
    "sv": ["swami vivekanand", "swami vivekananda"],
    "s.v.": ["swami vivekanand", "swami vivekananda"],
    "mg": ["mahatma gandhi"],
    "m.g.": ["mahatma gandhi"],
    "weh": ["western express"],
    "gmlr": ["goregaon mulund"],
    "lbs": ["lal bahadur shastri"],
    "eeh": ["eastern express"],
    "jvlr": ["jogeshwari vikhroli"],
}
# OSM writes the highway tag into `name` when a way is unnamed.
_TYPE_NAMES = {
    "residential",
    "living street",
    "living_street",
    "unclassified",
    "service",
    "tertiary",
    "secondary",
    "primary",
    "trunk",
    "motorway",
    "road",
    "street",
    "path",
    "footway",
    "track",
    "pedestrian",
    "cycleway",
    "primary link",
    "secondary link",
    "tertiary link",
    "trunk link",
    "motorway link",
    "primary_link",
    "secondary_link",
    "tertiary_link",
    "trunk_link",
    "motorway_link",
}
_STOP = {
    "about",
    "ahead",
    "blocked",
    "bridge",
    "citizen",
    "closed",
    "collapse",
    "damage",
    "flood",
    "flooded",
    "from",
    "near",
    "over",
    "passable",
    "report",
    "road",
    "above",
    "across",
    "brigade",
    "building",
    "clear",
    "collapsed",
    "completely",
    "drained",
    "drive",
    "fell",
    "help",
    "lots",
    "past",
    "rubble",
    "street",
    "that",
    "this",
    "today",
    "tree",
    "under",
    "water",
    "weather",
    "wheels",
    "waterlogging",
    "with",
}

_INDEXES = (
    ("shelter", cypher.CREATE_SHELTER_INDEX),
    ("citizen", cypher.CREATE_CITIZEN_INDEX),
    ("report", cypher.CREATE_REPORT_INDEX),
    ("osmid", cypher.CREATE_OSMID_INDEX),
    ("loc", cypher.CREATE_LOC_INDEX),
)


def _count(graph, query: str, params: dict | None = None) -> int:
    found = rows(graph.query(query, params or {}))
    if not found:
        return 0
    return int(found[0][0] or 0)


def _index(graph, query: str) -> bool:
    try:
        graph.query(query)
        return True
    except Exception as exc:
        message = str(exc).lower()
        return "already" in message or "equivalent" in message or "exist" in message


def _open_shelter(open_flag, capacity, occupancy) -> bool:
    if open_flag is False:
        return False
    if capacity is None or occupancy is None:
        return True
    return int(occupancy) < int(capacity)


def _norm_name(text: str) -> str:
    """Lowercase, turn No.6 / no. 6 into 'no 6', and treat underscores as spaces."""
    cleaned = (text or "").lower().replace("_", " ")
    cleaned = re.sub(r"\bno\.?\s*", "no ", cleaned)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _phrase_in_name(name: str, phrase: str) -> bool:
    """Whole-word match. 'tree' does not match 'street'."""
    haystack = _norm_name(name)
    needle = _norm_name(phrase)
    if not haystack or not needle:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def _is_type_name(name: str, highway: str = "") -> bool:
    """Unnamed OSM ways store the highway tag as the name. Those are not roads to close by name."""
    cleaned = _norm_name(name)
    tag = _norm_name(highway)
    if cleaned in _TYPE_NAMES:
        return True
    if tag and cleaned == tag:
        return True
    return False


def road_name_matches(name: str, phrase: str, highway: str = "") -> bool:
    if _is_type_name(name, highway):
        return False
    if _phrase_in_name(name, phrase):
        return True
    for alias in _ALIASES.get(_norm_name(phrase), []):
        if _phrase_in_name(name, alias):
            return True
    return False


def _clip_phrase(phrase: str) -> str:
    """Keep the place or street and drop the words that follow it."""
    kept = []
    for token in phrase.replace(",", " ").split():
        cleaned = token.strip(".,")
        norm = _norm_name(cleaned)
        if not norm or norm in _CLIP_STOP:
            break
        # "flooded" ends the place. "road" and "no" are part of the street name.
        if norm in _STOP and norm not in _ROAD_END and norm != "no":
            break
        kept.append(cleaned)
        if norm in _ROAD_END and len(kept) > 1:
            break
        if any(_norm_name(item) in _ROAD_END for item in kept) and _NUMBERED.search(cleaned):
            break
    return " ".join(kept).strip(" .")


def _sig_tokens(text: str) -> set[str]:
    return set(_norm_name(text).split()) - _GENERIC


def parse_report_place(text: str) -> dict:
    """Pull road codes, short aliases, numbered roads, an anchor street, and a landmark."""
    raw = text or ""
    codes = []
    for match in _CODE.finditer(raw):
        code = match.group(1)
        if code not in codes:
            codes.append(code)
    shorts = []
    for match in _SHORT.finditer(raw):
        token = match.group(1)
        if token not in shorts:
            shorts.append(token)
    numbered = []
    for match in _NUMBERED.finditer(raw):
        label = f"no {int(match.group(1))}"
        if label not in numbered:
            numbered.append(label)
    anchor = ""
    landmark = ""
    found = _PLACE.search(raw)
    if found:
        phrase = _clip_phrase(found.group(1))
        if phrase and not _CODE.fullmatch(phrase):
            if _ROADISH.search(phrase):
                anchor = phrase
            else:
                landmark = phrase
    aliases = []
    for token in [*codes, *shorts]:
        for alias in _ALIASES.get(token.lower().replace(" ", ""), []):
            if alias not in aliases:
                aliases.append(alias)
        for alias in _ALIASES.get(_norm_name(token), []):
            if alias not in aliases:
                aliases.append(alias)
    # Drop the spans we already classified so "CV-36" does not also yield the word "cv".
    stripped = raw
    for span in [*codes, *shorts, anchor]:
        if span:
            stripped = re.sub(rf"\b{re.escape(span)}\b", " ", stripped, flags=re.IGNORECASE)
    stripped = _NUMBERED.sub(" ", stripped)
    skip = set()
    if landmark:
        skip.update(_norm_name(landmark).split())
    if anchor:
        skip.update(_norm_name(anchor).split())
    words = []
    for token in re.findall(r"[A-Za-z][A-Za-z'’]{2,}", stripped):
        lowered = token.strip(" .").lower()
        if lowered in _STOP or lowered in {code.lower() for code in codes}:
            continue
        if lowered in {item.lower() for item in shorts}:
            continue
        if lowered in skip:
            continue
        if lowered not in words:
            words.append(lowered)
    query = codes[0] if codes else (shorts[0] if shorts else (numbered[0] if numbered else (words[0] if words else (anchor or landmark))))
    return {
        "codes": codes,
        "shorts": shorts,
        "aliases": aliases,
        "numbered": numbered,
        "words": words,
        "landmark": landmark,
        "anchor": anchor,
        "query": query,
    }


def _anchor_numbers(anchor: str) -> set[str]:
    found = set()
    for match in _NUMBERED.finditer(anchor or ""):
        found.add(f"no {int(match.group(1))}")
    return found


def _unique(items) -> list[str]:
    phrases: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in phrases:
            phrases.append(text)
    return phrases


def _primary_phrases(parsed: dict) -> list[str]:
    """The road the report is about. A second street ('near Road No.4', 'off Station Road') is not included."""
    anchor_nums = _anchor_numbers(parsed.get("anchor") or "")
    numbered = [item for item in (parsed.get("numbered") or []) if item not in anchor_nums]
    return _unique(
        list(parsed.get("codes") or [])
        + list(parsed.get("shorts") or [])
        + list(parsed.get("aliases") or [])
        + numbered
        + list(parsed.get("words") or [])
    )


def _anchor_phrases(parsed: dict) -> list[str]:
    anchor = (parsed.get("anchor") or "").strip()
    if not anchor:
        return []
    phrases = [anchor, *_anchor_numbers(anchor)]
    for token in _SHORT.findall(anchor):
        phrases.append(token)
        phrases.extend(_ALIASES.get(_norm_name(token), []))
    return _unique(phrases)


def _search_phrases(parsed: dict) -> list[str]:
    phrases = _unique(_primary_phrases(parsed) + _anchor_phrases(parsed))
    # A landmark ("near Oberoi Mall") resolves through Landmark nodes. It is a
    # road-name search only when the report names no road of its own.
    road_specific = bool(_primary_phrases(parsed))
    landmark = (parsed.get("landmark") or "").strip()
    if landmark and not road_specific and landmark not in phrases:
        phrases.append(landmark)
    return phrases


def _match_band(name: str, report: str, phrases: list[str]) -> int:
    """3 exact full name, 2 every significant token, 1 partial, 0 none."""
    if not phrases or not any(road_name_matches(name, phrase) for phrase in phrases):
        return 0
    if _phrase_in_name(report, name):
        return 3
    name_sig = _sig_tokens(name)
    for phrase in phrases:
        options = [_sig_tokens(phrase)]
        options.extend(_sig_tokens(alias) for alias in _ALIASES.get(_norm_name(phrase), []))
        if any(sig and sig == name_sig for sig in options):
            return 2
    return 1


def _is_anchor_road(name: str, anchor_phrases: list[str], primary_band: int) -> bool:
    """The cross street is context. Do not close it unless it is also the named road."""
    if not anchor_phrases or primary_band >= 2:
        return False
    return any(road_name_matches(name, phrase) for phrase in anchor_phrases)


def _better_segment(new: dict, old: dict) -> bool:
    def key(item: dict):
        anchor = item["anchor_m"] if item["anchor_m"] is not None else 10**12
        reporter = item["dist_m"] if item["dist_m"] is not None else 10**12
        return (-item["band"], anchor, reporter)

    return key(new) < key(old)


def _road_rows(graph, token: str) -> list[dict]:
    found = rows(graph.query(cypher.GEOCODE_ROADS, {"token": token}))
    edges = []
    for road_id, name, alat, alon, blat, blon, adistrict, bdistrict, aname, bname, highway, status in found:
        edges.append(
            {
                "road_id": road_id,
                "name": name or road_id,
                "alat": float(alat or 0),
                "alon": float(alon or 0),
                "blat": float(blat or 0),
                "blon": float(blon or 0),
                "adistrict": adistrict or "",
                "bdistrict": bdistrict or "",
                "aname": aname or "",
                "bname": bname or "",
                "highway": highway or "",
                "status": status or "open",
            }
        )
    return edges


def _endpoint_gap(edge: dict, points: list[tuple[float, float]]) -> float | None:
    if not points:
        return None
    best = None
    for lat, lon in ((edge["alat"], edge["alon"]), (edge["blat"], edge["blon"])):
        for plat, plon in points:
            gap = haversine_m(lat, lon, plat, plon)
            if best is None or gap < best:
                best = gap
    return best


def _rank_edges(
    edges: list[dict],
    parsed: dict,
    lat: float | None,
    lon: float | None,
    landmark_points: list[dict] | None = None,
) -> list[dict]:
    """One row per road name. Match quality first, then distance to the cross street, then to the reporter.

    A second street in the report is an anchor. Segments of the named road that
    meet that street come first, and the anchor street itself is not a candidate.
    """
    report = parsed.get("text") or ""
    primary = _primary_phrases(parsed)
    anchor_phrases = _anchor_phrases(parsed)
    anchor_points = [
        (latlon)
        for edge in edges
        if not _is_type_name(edge.get("name") or "", edge.get("highway") or "")
        and any(road_name_matches(edge.get("name") or "", phrase) for phrase in anchor_phrases)
        for latlon in ((edge["alat"], edge["alon"]), (edge["blat"], edge["blon"]))
    ]
    grouped: dict[str, dict] = {}
    saw_named = False
    pending: list[dict] = []
    for edge in edges:
        name = edge.get("name") or ""
        if _is_type_name(name, edge.get("highway") or ""):
            continue
        band = _match_band(name, report, primary)
        if _is_anchor_road(name, anchor_phrases, band):
            continue
        near_landmark = bool(edge.get("near_landmark"))
        if band == 0 and not near_landmark:
            continue
        if band > 0:
            saw_named = True
        mid_lat = (edge["alat"] + edge["blat"]) / 2
        mid_lon = (edge["alon"] + edge["blon"]) / 2
        dist_m = None
        if lat is not None and lon is not None:
            dist_m = haversine_m(lat, lon, mid_lat, mid_lon)
        landmark_m = None
        for point in landmark_points or []:
            if point.get("lat") is None:
                continue
            gap = haversine_m(float(point["lat"]), float(point["lon"]), mid_lat, mid_lon)
            if landmark_m is None or gap < landmark_m:
                landmark_m = gap
        anchor_gap = _endpoint_gap(edge, anchor_points)
        row = {
            "road_id": edge["road_id"],
            "name": name or edge["road_id"],
            "dist_m": None if dist_m is None else int(round(dist_m)),
            "landmark_m": None if landmark_m is None else int(round(landmark_m)),
            "anchor_m": None if anchor_gap is None else int(round(anchor_gap)),
            "band": band,
            "status": edge.get("status") or "open",
        }
        pending.append(row)
    # A named road wins. Roads that only touch a landmark stay when nothing was named.
    rows = [row for row in pending if row["band"] > 0 or not saw_named]
    for row in rows:
        key = _norm_name(row["name"]) or row["road_id"]
        current = grouped.get(key)
        if current is None or _better_segment(row, current):
            grouped[key] = row

    def _order(item: dict):
        anchor = item["anchor_m"] if item["anchor_m"] is not None else 10**12
        place = item["landmark_m"] if item["band"] == 0 and item["landmark_m"] is not None else anchor
        reporter = item["dist_m"] if item["dist_m"] is not None else 10**12
        return (-item["band"], place, reporter)

    ranked = sorted(grouped.values(), key=_order)
    return ranked[:8]


def ensure_shelter_citizen_model(graph, candidates: list[dict] | None = None) -> dict:
    """Create Shelter and Citizen indexes and ``(:Shelter)-[:NEAR]->(:Intersection)``.

    Idempotent. Existing ACCESS shelters are mirrored onto NEAR so CCH shelter
    routing and max-flow see the same destinations. Candidates from an area
    load are stored as NEAR and ACCESS.
    """
    indexes = {name: _index(graph, query) for name, query in _INDEXES}
    if _count(graph, "MATCH ()-[r:NEAR]->() RETURN count(r) AS n") == 0:
        try:
            graph.query(cypher.BACKFILL_NEAR)
        except Exception:
            pass
    written = 0
    for item in candidates or []:
        osmid = item.get("osmid")
        if osmid is None:
            continue
        graph.query(
            """
            MATCH (i:Intersection {osmid: $osmid})
            MERGE (s:Shelter {id: $id})
            SET s.name = $name,
                s.capacity = coalesce(s.capacity, $capacity),
                s.occupancy = coalesce(s.occupancy, $occupancy),
                s.lat = $lat,
                s.lon = $lon,
                s.kind = $kind,
                s.open = coalesce(s.open, true)
            MERGE (s)-[n:NEAR]->(i)
            SET n.dist_m = $dist_m
            MERGE (s)-[a:ACCESS]->(i)
            SET a.dist_m = $dist_m
            """,
            {
                "osmid": int(osmid),
                "id": str(item.get("id") or f"shelter-{osmid}"),
                "name": str(item.get("name") or "Shelter")[:120],
                "capacity": int(item.get("capacity") or 200),
                "occupancy": int(item.get("occupancy") or 0),
                "lat": float(item.get("lat") or 0),
                "lon": float(item.get("lon") or 0),
                "kind": str(item.get("kind") or "shelter")[:40],
                "dist_m": int(item.get("dist_m") or 0),
            },
        )
        written += 1
    shelters = _count(graph, "MATCH (s:Shelter) RETURN count(s) AS n")
    citizens = _count(graph, "MATCH (c:Citizen) RETURN count(c) AS n")
    near = _count(graph, "MATCH ()-[r:NEAR]->() RETURN count(r) AS n")
    at = _count(graph, "MATCH ()-[r:AT]->() RETURN count(r) AS n")
    return {
        "written": True,
        "indexes": indexes,
        "shelters": shelters,
        "attached": written,
        "counts": {"Shelter": shelters, "Citizen": citizens},
        "near": near,
        "at": at,
        "candidates": len(candidates or []),
    }


def snap_point(graph, lat: float, lon: float, citizen_id: str | None = None) -> dict:
    """Nearest Intersection via the point index, then haversine on node coordinates.

    When ``citizen_id`` is set, ``(:Citizen)-[:AT]->(:Intersection)`` is written.
    """
    snapped = None
    try:
        found = rows(
            graph.query(cypher.SNAP_GEO, {"lat": lat, "lon": lon, "radius_m": 8000})
        )
        if found:
            osmid, name, dist_m = found[0]
            snapped = {
                "osmid": int(osmid),
                "name": name,
                "dist_m": int(round(float(dist_m))),
                "method": "point-index",
            }
    except Exception:
        snapped = None
    if snapped is None:
        best = None
        best_dist = 10**12
        for osmid, name, node_lat, node_lon in rows(
            graph.query("MATCH (i:Intersection) RETURN i.osmid, i.name, i.lat, i.lon")
        ):
            if node_lat is None or node_lon is None:
                continue
            dist = haversine_m(lat, lon, float(node_lat), float(node_lon))
            if dist < best_dist:
                best = (int(osmid), name, dist)
                best_dist = dist
        if best is None:
            raise LookupError(f"No intersection near ({lat}, {lon})")
        snapped = {
            "osmid": best[0],
            "name": best[1],
            "dist_m": int(best[2]),
            "method": "haversine",
        }
    node = rows(
        graph.query(
            "MATCH (i:Intersection {osmid: $osmid}) RETURN i.lat, i.lon",
            {"osmid": snapped["osmid"]},
        )
    )
    if node and node[0][0] is not None:
        snapped["lat"] = float(node[0][0])
        snapped["lon"] = float(node[0][1])
    else:
        snapped["lat"] = lat
        snapped["lon"] = lon
    snapped["query_lat"] = lat
    snapped["query_lon"] = lon
    if citizen_id:
        _attach_at(graph, citizen_id, snapped["osmid"], lat, lon)
    return snapped


def _attach_at(graph, citizen_id: str, osmid: int, lat: float, lon: float) -> None:
    graph.query(
        """
        MERGE (c:Citizen {id: $id})
        SET c.lat = $lat, c.lon = $lon, c.updated_at = $at, c.active = true
        WITH c
        MATCH (c)-[r]->()
        WHERE type(r) = 'AT'
        DELETE r
        """,
        {"id": citizen_id, "lat": lat, "lon": lon, "at": utc_now()},
    )
    graph.query(
        """
        MATCH (c:Citizen {id: $id}), (i:Intersection {osmid: $osmid})
        CREATE (c)-[:AT]->(i)
        """,
        {"id": citizen_id, "osmid": int(osmid)},
    )


def _landmark_hits(graph, phrase: str) -> list[dict]:
    if not phrase:
        return []
    tokens = [phrase] + [word for word in phrase.split() if len(word) >= 4]
    hits = []
    seen = set()
    for token in tokens:
        try:
            found = rows(graph.query(cypher.LANDMARKS_BY_NAME, {"token": token}))
        except Exception:
            continue
        for lid, name, lat, lon, osmid in found:
            if lid in seen or not _phrase_in_name(name or "", token):
                continue
            seen.add(lid)
            hits.append(
                {
                    "id": lid,
                    "name": name,
                    "lat": float(lat or 0),
                    "lon": float(lon or 0),
                    "osmid": int(osmid) if osmid is not None else None,
                }
            )
    return hits


def _roads_touching(graph, osmid: int) -> list[dict]:
    try:
        found = rows(graph.query(cypher.ROADS_TOUCHING, {"osmid": int(osmid)}))
    except Exception:
        return []
    edges = []
    for road_id, name, alat, alon, blat, blon, highway, status in found:
        edges.append(
            {
                "road_id": road_id,
                "name": name or road_id,
                "alat": float(alat or 0),
                "alon": float(alon or 0),
                "blat": float(blat or 0),
                "blon": float(blon or 0),
                "adistrict": "",
                "bdistrict": "",
                "aname": "",
                "bname": "",
                "highway": highway or "",
                "status": status or "open",
                "near_landmark": True,
            }
        )
    return edges


def geocode_report(text: str, graph=None, lat: float | None = None, lon: float | None = None) -> dict:
    """Street name or landmark → ROAD edges.

    Whole words only, so 'tree' does not match 'street'. Short names (SV, MG)
    and numbered roads (No.6) are kept. Unnamed ways whose name is the highway
    type are skipped. Candidates are ranked by how well the name matches, then
    by distance to a named cross street, then by distance to the reporter.
    The cross street itself is not a candidate. Without a graph the parsed
    tokens are returned and ``edges`` stays empty.
    """
    parsed = parse_report_place(text)
    parsed["text"] = text or ""
    result = {**parsed, "edges": [], "landmarks": [], "method": "name+distance"}
    if graph is None:
        return result
    edges: list[dict] = []
    seen = set()
    for token in _search_phrases(parsed):
        probes = [token]
        if token.startswith("no "):
            number = token.split()[-1]
            probes = [f"no.{number}", f"no. {number}", f"no {number}", number]
        for probe in probes:
            try:
                batch = _road_rows(graph, probe)
            except Exception:
                batch = []
            for edge in batch:
                key = (edge["road_id"], edge["alat"], edge["blat"])
                if key in seen:
                    continue
                seen.add(key)
                edges.append(edge)
    landmarks = _landmark_hits(graph, parsed.get("landmark") or "")
    for landmark in landmarks:
        if landmark.get("osmid") is None:
            continue
        for edge in _roads_touching(graph, int(landmark["osmid"])):
            key = (edge["road_id"], edge["alat"], edge["blat"])
            if key in seen:
                continue
            seen.add(key)
            edges.append(edge)
    result["landmarks"] = [
        {"id": item["id"], "name": item["name"], "dist_m": None, "osmid": item.get("osmid")}
        for item in landmarks
    ]
    result["edges"] = _rank_edges(edges, parsed, lat, lon, landmarks)
    return result


def _shelter_candidates(graph) -> list[dict]:
    found = rows(graph.query(cypher.TOP_SHELTERS_NEAR, {"k": 12}))
    shelters = []
    for sid, name, capacity, occupancy, slat, slon, kind, open_flag, osmid, ilat, ilon, dist_m in found:
        if not _open_shelter(open_flag, capacity, occupancy):
            continue
        shelters.append(
            {
                "kind": "shelter",
                "id": sid,
                "name": name,
                "osmid": int(osmid),
                "lat": float(slat if slat is not None else ilat or 0),
                "lon": float(slon if slon is not None else ilon or 0),
                "capacity": int(capacity or 0),
                "occupancy": int(occupancy or 0),
                "open": True,
                "metric": "people",
                "subtype": kind or "shelter",
                "dist_m": int(dist_m or 0),
            }
        )
        if len(shelters) >= TOP_K:
            break
    return shelters


def _as_destination(hospital: dict, found_algo: str) -> dict:
    beds_total = int(hospital.get("beds_total") or hospital.get("beds_free") or 0)
    beds_free = int(hospital.get("beds_free") or 0)
    return {
        "kind": "hospital",
        "id": hospital["id"],
        "name": hospital["name"],
        "osmid": int(hospital["osmid"]),
        "lat": hospital.get("lat"),
        "lon": hospital.get("lon"),
        "capacity": beds_total,
        "occupancy": max(0, beds_total - beds_free),
        "open": True,
        "metric": "beds",
        "travel_s": int(hospital["travel_s"]),
        "algo": found_algo or hospital.get("algo"),
    }


def _node_lat_lon(graph, osmid: int) -> tuple[float | None, float | None]:
    found = rows(
        graph.query(
            "MATCH (i:Intersection {osmid: $osmid}) RETURN i.lat, i.lon",
            {"osmid": int(osmid)},
        )
    )
    if not found:
        return None, None
    lat, lon = found[0]
    if lat is None or lon is None:
        return None, None
    return float(lat), float(lon)


def remember_route(
    graph,
    lat: float,
    lon: float,
    origin: dict,
    found,
    hops: list[dict],
    destination: dict,
    *,
    citizen_id: str | None = None,
    record_decision: bool = True,
) -> str:
    """Create or update one visitor's Citizen, its AT edge, and ROUTED_TO hops.

    The retired shared id ``citizen`` is not written. Callers that have no
    session id get a route and no Citizen node.
    """
    if not citizen_id or citizen_id == LEGACY_CITIZEN:
        return ""
    at = utc_now()
    graph.query(
        """
        MERGE (c:Citizen {id: $id})
        SET c.lat = $lat,
            c.lon = $lon,
            c.updated_at = $at,
            c.eta_s = $eta,
            c.algo = $algo,
            c.active = true,
            c.shelter_id = $shelter,
            c.origin_osmid = $osmid,
            c.path_osmids = $osmids
        """,
        {
            "id": citizen_id,
            "lat": lat,
            "lon": lon,
            "at": at,
            "eta": int(found.travel_s),
            "algo": found.algo,
            "shelter": str(destination.get("id") or ""),
            "osmid": int(origin["osmid"]),
            "osmids": json.dumps([int(v) for v in found.osmids]),
        },
    )
    graph.query(
        """
        MATCH (c:Citizen {id: $id})-[r]->()
        WHERE type(r) IN ['AT', 'ROUTED_TO']
        DELETE r
        """,
        {"id": citizen_id},
    )
    graph.query(
        """
        MATCH (c:Citizen {id: $id}), (i:Intersection {osmid: $osmid})
        CREATE (c)-[:AT]->(i)
        """,
        {"id": citizen_id, "osmid": int(origin["osmid"])},
    )
    routed = []
    for seq, hop in enumerate(hops):
        road_id = hop.get("road_id") or ""
        osmid = found.osmids[seq + 1] if seq + 1 < len(found.osmids) else None
        if osmid is None or not road_id:
            continue
        routed.append({"seq": seq, "osmid": int(osmid), "road_id": str(road_id)})
    if routed:
        graph.query(
            """
            UNWIND $hops AS hop
            MATCH (c:Citizen {id: $id}), (i:Intersection {osmid: hop.osmid})
            CREATE (c)-[:ROUTED_TO {seq: hop.seq, road_id: hop.road_id}]->(i)
            """,
            {"id": citizen_id, "hops": routed},
        )
    if record_decision:
        existing = rows(
            graph.query(
                "MATCH (c:Citizen {id: $id}) RETURN c.decision_id",
                {"id": citizen_id},
            )
        )
        current = existing[0][0] if existing and existing[0][0] else None
        decision_id = _sync_citizen_decision(
            graph,
            citizen_id=citizen_id,
            found=found,
            destination=destination,
            at=at,
            existing_id=current,
        )
        graph.query(
            "MATCH (c:Citizen {id: $id}) SET c.decision_id = $decision",
            {"id": citizen_id, "decision": decision_id},
        )
    return citizen_id


def _sync_citizen_decision(graph, *, citizen_id, found, destination, at, existing_id) -> str:
    names = " → ".join(collapse_names(found.names)) or destination.get("name") or "route"
    summary = f"{names} ({found.travel_s}s)"
    explanation = (
        f"Citizen routed to {destination.get('name')} in {found.travel_s} s "
        f"via {names}. Algorithm: {found.algo}."
    )
    if existing_id:
        graph.query(
            """
            MATCH (d:Decision {id: $id})
            SET d.explanation = $explanation,
                d.result_summary = $summary,
                d.travel_s = $travel,
                d.algo = $algo,
                d.path_osmids = $osmids,
                d.path_names = $names
            """,
            {
                "id": existing_id,
                "explanation": explanation,
                "summary": summary,
                "travel": int(found.travel_s),
                "algo": found.algo,
                "osmids": json.dumps([int(v) for v in found.osmids]),
                "names": json.dumps(list(found.names)),
            },
        )
        return existing_id
    from floodline.tools import _id

    decision_id = _id("dec")
    seq = next_seq(graph)
    graph.query(
        """
        CREATE (:Decision {
          id: $id,
          seq: $seq,
          at: $at,
          kind: 'citizen_route',
          algo: $algo,
          explanation: $explanation,
          result_summary: $summary,
          superseded: false,
          travel_s: $travel,
          path_osmids: $osmids,
          path_names: $names
        })
        """,
        {
            "id": decision_id,
            "seq": seq,
            "at": at,
            "algo": found.algo,
            "explanation": explanation,
            "summary": summary,
            "travel": int(found.travel_s),
            "osmids": json.dumps([int(v) for v in found.osmids]),
            "names": json.dumps(list(found.names)),
        },
    )
    graph.query(
        """
        MERGE (a:Agent {name: 'Dispatcher'})
        SET a.role = coalesce(a.role, 'routing'), a.heartbeat_at = $at
        WITH a
        MATCH (d:Decision {id: $id}), (c:Citizen {id: $citizen})
        MERGE (a)-[:MADE]->(d)
        MERGE (d)-[:AFFECTS]->(c)
        """,
        {"at": at, "id": decision_id, "citizen": citizen_id},
    )
    return decision_id


def route_to_shelter(
    graph,
    lat: float,
    lon: float,
    candidates: list[dict] | None = None,
    *,
    record_decision: bool = True,
    citizen_id: str | None = None,
) -> dict:
    """Snap, rank open shelters by NEAR, then CCH (A*, then SPpaths). Lowest travel_s wins.

    The active Citizen and its ROUTED_TO edges are created or updated in the graph.
    """
    ensure_shelter_citizen_model(graph, candidates)
    snapped = snap_point(graph, lat, lon)
    origin = int(snapped["osmid"])
    chosen = None
    found = None
    for shelter in _shelter_candidates(graph):
        if shelter["osmid"] == origin:
            continue
        try:
            candidate = route(graph, origin, shelter["osmid"])
        except (NoRoute, LookupError):
            continue
        except Exception:
            continue
        if chosen is None or candidate.travel_s < found.travel_s:
            chosen = {**shelter, "travel_s": candidate.travel_s, "algo": candidate.algo}
            found = candidate

    if found is None:
        hospital = best_hospital(graph, origin)
        found = route(graph, origin, int(hospital["osmid"]))
        hlat, hlon = _node_lat_lon(graph, int(hospital["osmid"]))
        extra = rows(
            graph.query(
                """
                MATCH (h:Hospital {id: $id})
                RETURN h.beds_total, h.beds_free, h.lat, h.lon
                """,
                {"id": hospital["id"]},
            )
        )
        if extra:
            beds_total, beds_free, blat, blon = extra[0]
            hospital = {
                **hospital,
                "beds_total": int(beds_total or 0),
                "beds_free": int(beds_free if beds_free is not None else hospital.get("beds_free") or 0),
                "lat": float(blat if blat is not None else hlat or 0),
                "lon": float(blon if blon is not None else hlon or 0),
            }
        else:
            hospital = {**hospital, "lat": hlat, "lon": hlon, "beds_total": int(hospital.get("beds_free") or 0)}
        chosen = _as_destination(hospital, found.algo)

    hops = path_hops(graph, found.osmids)
    steps = build_steps(hops)
    remember_route(
        graph,
        lat,
        lon,
        snapped,
        found,
        hops,
        chosen,
        citizen_id=citizen_id,
        record_decision=record_decision,
    )
    return {
        "origin": snapped,
        "destination": chosen,
        "algo": found.algo,
        "eta_s": found.travel_s,
        "osmids": found.osmids,
        "names": found.names,
        "hops": hops,
        "steps": steps,
        "citizen_id": citizen_id,
    }


def _report_for_road(graph, road_id: str) -> str | None:
    found = rows(
        graph.query(
            """
            MATCH (rep:Report)-[:CLOSES {road_id: $road_id}]->()
            RETURN rep.id
            ORDER BY rep.received_at DESC
            LIMIT 1
            """,
            {"road_id": road_id},
        )
    )
    if found:
        return found[0][0]
    latest = rows(
        graph.query(
            """
            MATCH (rep:Report)
            RETURN rep.id
            ORDER BY rep.received_at DESC
            LIMIT 1
            """
        )
    )
    return latest[0][0] if latest else None


def reroute_affected(graph, road_id: str) -> dict:
    """Citizens whose active ROUTED_TO edge is this road get a new shelter route.

    The new Decision SUPERSEDES the previous citizen route, is BASED_ON the
    report that closed the road, and AFFECTS plus REROUTED the citizen.
    """
    affected = rows(graph.query(cypher.CITIZENS_ON_ROAD, {"road_id": road_id}))
    citizens = []
    decisions = []
    report_id = _report_for_road(graph, road_id)
    road_name = road_id
    named = rows(
        graph.query(
            "MATCH ()-[r:ROAD {road_id: $road_id}]->() RETURN r.name LIMIT 1",
            {"road_id": road_id},
        )
    )
    if named and named[0][0]:
        road_name = named[0][0]
    for citizen_id, lat, lon, eta_s, decision_id in affected:
        if lat is None or lon is None:
            continue
        if not citizen_id or str(citizen_id) == LEGACY_CITIZEN:
            continue
        old_eta = int(eta_s or 0)
        try:
            fresh = route_to_shelter(
                graph,
                float(lat),
                float(lon),
                record_decision=False,
                citizen_id=str(citizen_id),
            )
        except Exception as exc:
            citizens.append({"id": citizen_id, "error": str(exc), "old_eta_s": old_eta})
            continue
        new_eta = int(fresh["eta_s"])
        new_id = _write_reroute_decision(
            graph,
            citizen_id=str(citizen_id),
            road_id=road_id,
            road_name=road_name,
            old_eta=old_eta,
            new_eta=new_eta,
            found_algo=fresh["algo"],
            names=fresh["names"],
            osmids=fresh["osmids"],
            previous_id=decision_id,
            report_id=report_id,
            destination=fresh["destination"],
        )
        graph.query(
            "MATCH (c:Citizen {id: $id}) SET c.decision_id = $decision, c.eta_s = $eta",
            {"id": citizen_id, "decision": new_id, "eta": new_eta},
        )
        citizens.append(
            {
                "id": citizen_id,
                "old_eta_s": old_eta,
                "new_eta_s": new_eta,
                "decision_id": new_id,
                "shelter_id": fresh["destination"].get("id"),
            }
        )
        decisions.append(new_id)
    return {"road_id": road_id, "citizens": citizens, "decisions": decisions, "report_id": report_id}


def _write_reroute_decision(
    graph,
    *,
    citizen_id,
    road_id,
    road_name,
    old_eta,
    new_eta,
    found_algo,
    names,
    osmids,
    previous_id,
    report_id,
    destination,
) -> str:
    from floodline.tools import _id

    at = utc_now()
    decision_id = _id("dec")
    seq = next_seq(graph)
    via = " → ".join(collapse_names(names)) or destination.get("name") or "route"
    reason = f"Active route used closed edge {road_name}"
    explanation = (
        f"Citizen rerouted because {road_name} closed. {reason}. "
        f"Previous ETA {old_eta} s. New ETA {new_eta} s via {via}. "
        f"Algorithm: {found_algo}."
    )
    summary = f"{via} ({new_eta}s)"
    graph.query(
        """
        CREATE (:Decision {
          id: $id,
          seq: $seq,
          at: $at,
          kind: 'citizen_route',
          algo: $algo,
          explanation: $explanation,
          result_summary: $summary,
          superseded: false,
          travel_s: $travel,
          path_osmids: $osmids,
          path_names: $names,
          road_id: $road_id,
          reason: $reason,
          old_eta_s: $old_eta,
          new_eta_s: $new_eta
        })
        """,
        {
            "id": decision_id,
            "seq": seq,
            "at": at,
            "algo": found_algo,
            "explanation": explanation,
            "summary": summary,
            "travel": int(new_eta),
            "osmids": json.dumps([int(v) for v in osmids]),
            "names": json.dumps(list(names)),
            "road_id": road_id,
            "reason": reason,
            "old_eta": int(old_eta),
            "new_eta": int(new_eta),
        },
    )
    graph.query(
        """
        MERGE (a:Agent {name: 'Dispatcher'})
        SET a.heartbeat_at = $at
        WITH a
        MATCH (d:Decision {id: $id}), (c:Citizen {id: $citizen})
        MERGE (a)-[:MADE]->(d)
        MERGE (d)-[:AFFECTS]->(c)
        MERGE (d)-[:REROUTED]->(c)
        """,
        {"at": at, "id": decision_id, "citizen": citizen_id},
    )
    if previous_id:
        graph.query(
            """
            MATCH (d:Decision {id: $id}), (prev:Decision {id: $prev})
            MERGE (d)-[:SUPERSEDES]->(prev)
            SET prev.superseded = true
            """,
            {"id": decision_id, "prev": previous_id},
        )
    if report_id:
        graph.query(
            """
            MATCH (d:Decision {id: $id}), (rep:Report {id: $report})
            MERGE (d)-[:BASED_ON]->(rep)
            """,
            {"id": decision_id, "report": report_id},
        )
    return decision_id


def shelter_reachability(graph) -> dict:
    """Areas that cannot reach an open shelter, from ``algo.WCC`` on PASSABLE.

    A district is cut off when none of its intersections share a component with
    an open shelter. Mixed districts also return the isolated component so the
    map can shade that pocket.
    """
    components = {
        int(osmid): int(component_id)
        for osmid, component_id in rows(graph.query(cypher.WCC_PASSABLE))
        if osmid is not None
    }
    shelter_components = set()
    for osmid, open_flag, capacity, occupancy in rows(
        graph.query(
            """
            MATCH (s:Shelter)-[rel]->(i:Intersection)
            WHERE type(rel) IN ['ACCESS', 'NEAR']
            RETURN DISTINCT i.osmid, s.open, s.capacity, s.occupancy
            """
        )
    ):
        if osmid is None or int(osmid) not in components:
            continue
        if _open_shelter(open_flag, capacity, occupancy):
            shelter_components.add(components[int(osmid)])
    districts = rows(
        graph.query(
            """
            MATCH (d:District)
            OPTIONAL MATCH (i:Intersection)-[:IN_DISTRICT]->(d)
            RETURN d.name, d.population, collect(i.osmid)
            ORDER BY d.name
            """
        )
    )
    areas = []
    at = utc_now()
    if districts:
        for name, population, osmids in districts:
            node_ids = [int(osmid) for osmid in osmids or [] if osmid is not None]
            areas.extend(
                _areas_for_nodes(
                    graph,
                    name,
                    int(population or 0),
                    node_ids,
                    components,
                    shelter_components,
                    at,
                    district=True,
                )
            )
    else:
        grouped: dict[str, list[int]] = {}
        for osmid, district in rows(
            graph.query("MATCH (i:Intersection) RETURN i.osmid, coalesce(i.district, 'area')")
        ):
            grouped.setdefault(str(district or "area"), []).append(int(osmid))
        for name, node_ids in sorted(grouped.items()):
            areas.extend(
                _areas_for_nodes(
                    graph, name, 0, node_ids, components, shelter_components, at, district=False
                )
            )
    return {
        "areas": areas,
        "algo": "algo.WCC",
        "shelter_components": sorted(shelter_components),
    }


def _areas_for_nodes(graph, name, population, node_ids, components, shelter_components, at, *, district: bool) -> list[dict]:
    member_components = {components[osmid] for osmid in node_ids if osmid in components}
    reachable = bool(member_components & shelter_components)
    cut_off = bool(node_ids) and not reachable
    component_id = None
    if member_components:
        component_id = max(
            member_components,
            key=lambda cid: sum(1 for osmid in node_ids if components.get(osmid) == cid),
        )
    if district:
        graph.query(
            """
            MATCH (d:District {name: $name})
            SET d.cut_off = $cut_off,
                d.component_id = $component_id,
                d.cut_off_since = $since
            """,
            {
                "name": name,
                "cut_off": cut_off,
                "component_id": -1 if component_id is None else component_id,
                "since": at if cut_off else "",
            },
        )
    areas = [
        {
            "name": name,
            "population": population,
            "cut_off": cut_off,
            "component_id": component_id,
            "intersections": len(node_ids),
        }
    ]
    if cut_off or not member_components:
        return areas
    for cid in sorted(member_components):
        if cid in shelter_components:
            continue
        members = [osmid for osmid in node_ids if components.get(osmid) == cid]
        if not members:
            continue
        areas.append(
            {
                "name": f"{name} isolated",
                "population": 0,
                "cut_off": True,
                "component_id": cid,
                "intersections": len(members),
                "osmids": members,
            }
        )
    return areas
