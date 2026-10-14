"""Show a district flip to CUT OFF and a max-flow drop.

Usage (repo root, FalkorDB already up):

    PYTHONPATH=backend python -m floodline.evac_demo

This uses the synthetic 10-node network. Closing the three roads that leave
Paiporta splits PASSABLE (algo.WCC) and drops district-to-shelter maxFlow
from 1200 to 0. The critical edge named before the closure is the saturated
V-31 Paiporta bridge.
"""

from __future__ import annotations

import sys

from floodline.analysis import assert_paiporta_cutoff, prove_cutoff
from floodline.config import Settings
from floodline.db import get_graph, wait_for_db
from floodline.scenario import reset_scenario


def _area(result: dict, when: str, name: str) -> dict:
    return next(item for item in result[when]["areas"] if item["name"] == name)


def main(argv: list[str] | None = None) -> int:
    del argv
    settings = Settings.from_env()
    print("Floodline cut-off and evacuation capacity")
    print(f"FalkorDB {settings.host}:{settings.port}  graph={settings.graph}")
    print("Network: synthetic 10-node Valencia schematic")
    try:
        wait_for_db(settings)
    except TimeoutError as exc:
        print(exc, file=sys.stderr)
        return 2

    reset_scenario(settings)
    result = prove_cutoff(get_graph(settings))
    before = _area(result, "before", "Paiporta")
    after = _area(result, "after", "Paiporta")
    before_evac = result["before"]["evac"]
    after_evac = result["after"]["evac"]
    critical = before_evac["critical_edge"]
    print()
    print(
        f"BEFORE  Paiporta cut_off={before['cut_off']}  "
        f"component={before['component_id']}  "
        f"maxFlow={before_evac['max_flow']} veh/h"
    )
    if critical:
        flag = "saturated" if critical["saturated"] else "busiest"
        print(
            f"  critical edge: {critical['name']}  "
            f"flow={critical['flow']}  capacity={critical['capacity']}  {flag}"
        )
    print("CORRIDOR  closed exits:")
    for item in result["closed"]:
        print(f"  {item['name']} ({item['road_id']})  edges={item['closed_edges']}")
    print(
        f"AFTER   Paiporta cut_off={after['cut_off']}  "
        f"component={after['component_id']}  "
        f"maxFlow={after_evac['max_flow']} veh/h"
    )
    print(f"  delta: {after_evac['max_flow'] - before_evac['max_flow']:+d} veh/h")
    print()
    try:
        assert_paiporta_cutoff(result)
    except AssertionError as exc:
        print(f"DEMO FAILED: {exc}", file=sys.stderr)
        return 1
    print("DEMO OK: Paiporta is CUT OFF and maxFlow dropped. Critical edge: V-31 Paiporta bridge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
