"""Load the Valencia OSM drive graph and prove one closure changes a CCH route.

Usage (repo root, FalkorDB already up):

    PYTHONPATH=backend python -m floodline.osm_demo

`--refresh` re-downloads the bbox with OSMnx. Without it, the committed extract
in backend/data/osm/valencia_corridor.json is loaded (the Railway service root
is `backend/`). If that file is missing and the download fails, the synthetic
10-node demo runs instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from floodline.bulk import attach_facilities, load_records
from floodline.config import Settings, T_CLOSE
from floodline.db import connect, falkordb_version, get_graph, rows, wait_for_db
from floodline.osm_network import (
    BBOX,
    EXTRACT_PATH,
    FACILITIES,
    apply_facility_labels,
    fetch_drive_graph,
    snap_facility,
)
from floodline.routing import try_route
from floodline.tools import close_road, reopen_road


def _fmt(seconds: int) -> str:
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes} min {remainder:02d} s ({seconds} s)"


def _hops(graph, osmids: list[int]) -> list[dict]:
    if len(osmids) < 2:
        return []
    pairs = [{"src": src, "dst": dst} for src, dst in zip(osmids, osmids[1:])]
    found = rows(
        graph.query(
            """
            UNWIND $pairs AS pair
            MATCH (a:Intersection {osmid: pair.src})-[r:ROAD {status: 'open'}]->(b:Intersection {osmid: pair.dst})
            RETURN a.osmid AS src, b.osmid AS dst, r.road_id AS road_id,
                   r.name AS name, r.travel_s AS travel_s
            """,
            {"pairs": pairs},
        )
    )
    best: dict[tuple[int, int], dict] = {}
    for src, dst, road_id, name, travel_s in found:
        key = (int(src), int(dst))
        item = {
            "src": key[0],
            "dst": key[1],
            "road_id": road_id,
            "name": name,
            "travel_s": int(travel_s),
        }
        current = best.get(key)
        if current is None or item["travel_s"] < current["travel_s"]:
            best[key] = item
    return [best[key] for key in ((int(a), int(b)) for a, b in zip(osmids, osmids[1:])) if key in best]


def _compress(names: list[str]) -> str:
    ordered = []
    for name in names:
        if name and (not ordered or ordered[-1] != name):
            ordered.append(name)
    if len(ordered) > 8:
        return " → ".join(ordered[:6]) + " → … → " + ordered[-1]
    return " → ".join(ordered)


def prove_cch_closure(graph, src: int, dst: int, *, max_tries: int = 40) -> dict:
    """Close one open edge on the CCH path until the path and the ETA both change.

    The successful closure is left in place. Attempts that do not change the
    route are reopened.
    """
    before = try_route(graph, "cch", src, dst)
    if before is None:
        raise RuntimeError(f"CCH returned no open path from {src} to {dst}")
    hops = _hops(graph, before.osmids)
    # Prefer a long interior segment. The first and last hops are often driveways.
    body = hops[1:-1] if len(hops) > 4 else list(hops)
    body.sort(key=lambda hop: hop["travel_s"], reverse=True)
    seen = set()
    tried = 0
    for hop in body:
        if hop["road_id"] in seen:
            continue
        seen.add(hop["road_id"])
        tried += 1
        if tried > max_tries:
            break
        close_road(graph, hop["road_id"], by="osm-demo", at=T_CLOSE)
        after = try_route(graph, "cch", src, dst)
        if (
            after is not None
            and after.osmids != before.osmids
            and after.travel_s > before.travel_s
            and hop["road_id"] not in _road_ids(graph, after.osmids)
        ):
            return {
                "before": before,
                "after": after,
                "closed": hop,
                "before_roads": _compress([item["name"] for item in hops]),
                "after_roads": _compress([item["name"] for item in _hops(graph, after.osmids)]),
            }
        reopen_road(graph, hop["road_id"])
    raise RuntimeError(
        f"No single closure on the CCH path from {src} to {dst} changed the route "
        f"({tried} segments tried)"
    )


def _road_ids(graph, osmids: list[int]) -> set[str]:
    return {hop["road_id"] for hop in _hops(graph, osmids)}


def load_osm_graph(settings: Settings, *, refresh: bool = False) -> dict:
    prepared, origin = fetch_drive_graph(refresh=refresh)
    snaps = [snap_facility(prepared["nodes"], spec) for spec in FACILITIES]
    apply_facility_labels(prepared, snaps)
    db = connect(settings, socket_timeout=180)
    version = falkordb_version(db)
    graph = db.select_graph(settings.graph)
    try:
        graph.delete()
    except Exception:
        pass
    graph = db.select_graph(settings.graph)
    loaded = load_records(graph, prepared)
    attach_facilities(graph, snaps)
    for name, role in (("Scout", "closures"), ("Dispatcher", "routing")):
        graph.query(
            "CREATE (:Agent {name: $name, role: $role, heartbeat_at: $at})",
            {"name": name, "role": role, "at": T_CLOSE},
        )
    by_kind = {}
    for snap in snaps:
        by_kind.setdefault(snap["spec"].kind, []).append(snap)
    unit = next(snap for snap in snaps if snap["spec"].kind == "unit")
    hospital = next(snap for snap in snaps if snap["spec"].id == "peset")
    return {
        "origin": origin,
        "version": version,
        "prepared": prepared,
        "loaded": loaded,
        "snaps": [
            {
                "id": snap["spec"].id,
                "kind": snap["spec"].kind,
                "name": snap["spec"].name,
                "osmid": snap["osmid"],
                "dist_m": snap["dist_m"],
            }
            for snap in snaps
        ],
        "unit_osmid": unit["osmid"],
        "hospital_osmid": hospital["osmid"],
        "counts_by_kind": {kind: len(items) for kind, items in by_kind.items()},
    }


def _print_stats(state: dict) -> None:
    prepared = state["prepared"]
    bbox = prepared["bbox"]
    loaded = state["loaded"]
    print(f"FalkorDB version: {state['version']}")
    print(f"Source: {state['origin']}  ({prepared.get('attribution', 'OSM')}, {prepared.get('license', 'ODbL')})")
    print(
        "BBox: "
        f"west={bbox['west']} south={bbox['south']} east={bbox['east']} north={bbox['north']}"
    )
    print(
        f"Loaded {loaded['intersections']} intersections, "
        f"{loaded['road_edges']} directed ROAD edges, "
        f"{loaded['passable']} PASSABLE mirrors, "
        f"{loaded['districts']} districts"
    )
    print(f"CCH index: {loaded['cch']}")
    print(f"Geo points: {loaded['geo_points']}")
    print("Districts (nearest town centroid, populations are synthetic):")
    for name, count in sorted(Counter(node["district"] for node in prepared["nodes"]).items()):
        print(f"  {name}: {count} intersections")
    print("Snaps (synthetic beds / shelters / unit onto OSM intersections):")
    for snap in state["snaps"]:
        print(f"  {snap['kind']:8} {snap['name']} -> osmid {snap['osmid']} ({snap['dist_m']} m)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the Valencia OSM graph and prove a CCH detour")
    parser.add_argument("--refresh", action="store_true", help="Re-download the bbox with OSMnx")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    west, south, east, north = BBOX
    print("Floodline OSM load — Valencia flood corridor")
    print(f"FalkorDB {settings.host}:{settings.port}  graph={settings.graph}")
    print("Expected image: falkordb/falkordb:v4.22.0")
    print(f"BBox west={west} south={south} east={east} north={north}")
    try:
        wait_for_db(settings)
    except TimeoutError as exc:
        print(exc, file=sys.stderr)
        print("Start it with: docker compose up -d", file=sys.stderr)
        return 2

    try:
        state = load_osm_graph(settings, refresh=args.refresh)
    except Exception as exc:
        print(f"OSM load failed: {exc}", file=sys.stderr)
        if EXTRACT_PATH.is_file() and not args.refresh:
            print("The committed extract exists but did not load. Not falling back.", file=sys.stderr)
            return 1
        print("OSM download failed. Synthetic 10-node fallback:", file=sys.stderr)
        from floodline.demo import main as synthetic_main

        return synthetic_main([])

    _print_stats(state)
    if state["version"] != "4.22.0":
        print(f"DEMO FAILED: expected FalkorDB 4.22.0, got {state['version']}", file=sys.stderr)
        return 1
    if not state["loaded"]["cch"].get("ok"):
        print(f"DEMO FAILED: CCH index was not created: {state['loaded']['cch']}", file=sys.stderr)
        return 1

    graph = get_graph(settings)
    print()
    print(
        f"Routing A-7 (osmid {state['unit_osmid']}) -> Hospital Doctor Peset "
        f"(osmid {state['hospital_osmid']})"
    )
    try:
        proof = prove_cch_closure(graph, state["unit_osmid"], state["hospital_osmid"])
    except Exception as exc:
        print(f"DEMO FAILED: {exc}", file=sys.stderr)
        return 1

    before = proof["before"]
    after = proof["after"]
    closed = proof["closed"]
    passable = int(rows(graph.query("MATCH ()-[p:PASSABLE]->() RETURN count(p)"))[0][0])
    print(f"BEFORE  {_fmt(before.travel_s)}  algo={before.algo}  hops={len(before.osmids) - 1}")
    print(f"  roads: {proof['before_roads']}")
    print(f"CLOSED  {closed['name']} ({closed['road_id']})  was {closed['travel_s']} s")
    print(f"  PASSABLE remaining: {passable}")
    print(f"AFTER   {_fmt(after.travel_s)}  algo={after.algo}  hops={len(after.osmids) - 1}")
    print(f"  roads: {proof['after_roads']}")
    print(f"  delta: {after.travel_s - before.travel_s:+d} s")
    graph.query(
        """
        MERGE (m:Meta {key: 'osm-proof'})
        SET m.closed_road_id = $road_id,
            m.closed_name = $name,
            m.before_s = $before_s,
            m.after_s = $after_s,
            m.algo = 'cch'
        """,
        {
            "road_id": closed["road_id"],
            "name": closed["name"],
            "before_s": before.travel_s,
            "after_s": after.travel_s,
        },
    )
    print()
    print(
        "DEMO OK: one closure changed the CCH route. "
        f"Extract: {EXTRACT_PATH.relative_to(EXTRACT_PATH.parents[2])}"
    )
    print(json.dumps({"closed_road_id": closed["road_id"], "before_s": before.travel_s, "after_s": after.travel_s}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
