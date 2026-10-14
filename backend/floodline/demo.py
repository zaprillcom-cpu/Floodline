"""Reproduce one closure and one reroute against FalkorDB.

Usage (from the repo root, after `docker compose up -d`):

    PYTHONPATH=backend python -m floodline.demo
"""

from __future__ import annotations

import argparse
import sys

from floodline.config import Settings
from floodline.db import wait_for_db
from floodline.network import CLOSURE_ROAD_ID, DEMO_REPORT, UNIT_CALLSIGN
from floodline.scenario import assert_reroute, file_report, reset_scenario


def _fmt(seconds: int) -> str:
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes} min {remainder:02d} s ({seconds} s)"


def _path(route: dict) -> str:
    return " → ".join(route["names"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Floodline slice 1 demo")
    parser.add_argument("--report", default=DEMO_REPORT, help="Citizen report text")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    print("Floodline demo — one closure, one reroute")
    print(f"FalkorDB {settings.host}:{settings.port}  graph={settings.graph}")
    print("Expected image: falkordb/falkordb:v4.22.0")
    try:
        wait_for_db(settings)
    except TimeoutError as exc:
        print(exc, file=sys.stderr)
        print("Start it with: docker compose up -d", file=sys.stderr)
        return 2

    state = reset_scenario(settings)
    routers = state["load"]["routers"]
    print(f"Loaded {state['load']['intersections']} intersections, "
          f"{state['load']['road_edges']} directed ROAD edges, "
          f"{state['load']['passable']} PASSABLE mirrors")
    print(f"CCH index: {state['load']['cch']}")
    print(f"Router: {routers['chosen']}")
    for name, info in routers["available"].items():
        detail = f"ok ({info['travel_s']} s)" if info.get("ok") else info.get("error")
        print(f"  {name}: {detail}")

    before = state["routes"]["before"]
    print()
    print(f"BEFORE  {UNIT_CALLSIGN}  {_fmt(before['travel_s'])}  algo={before['algo']}")
    print(f"  {_path(before)}")

    print()
    print("SCOUT")
    print(f"  report: {args.report}")
    after_state = file_report(args.report, settings)
    closed = after_state["scout"]["closed"]
    print(f"  closed {closed['name']} ({closed['road_id']})")
    print(f"  directed edges updated: {closed['closed_edges']}")
    print(f"  PASSABLE remaining: {after_state['counts']['passable']}")
    print(f"  handoff task: {after_state['scout']['task_id']}")

    dispatcher = after_state["dispatcher"]
    after = after_state["routes"]["after"]
    print()
    print("DISPATCHER")
    if not dispatcher.get("claimed"):
        print("  did not claim a task")
    else:
        print(f"  claimed {dispatcher['task_id']}")
        print(f"  AFTER  {_fmt(after['travel_s'])}  algo={after['algo']}")
        print(f"  {_path(after)}")
    print()
    print(after_state["narrative"]["route"])
    print()
    try:
        assert_reroute(after_state)
    except AssertionError as exc:
        print(f"DEMO FAILED: {exc}", file=sys.stderr)
        return 1
    if CLOSURE_ROAD_ID not in after_state["closed_roads"]:
        print("DEMO FAILED: closure road missing", file=sys.stderr)
        return 1
    print("DEMO OK: the closure changed the route and the reason is on the Decision node.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
