"""Deterministic reconstructed clock for the synthetic Valencia scenario.

No randomness. The same three ticks run every time: opening route, the V-31
report, then the Paiporta corridor. Times are not the real DANA timeline.

    PYTHONPATH=backend python -m floodline.replay
"""

from __future__ import annotations

import sys

from floodline.agents.dispatcher import reroute
from floodline.agents.scout import handle_report
from floodline.analysis import evac_capacity, prove_cutoff, record_corridor
from floodline.config import T_CLOSE, T_ROUTE, Settings
from floodline.db import get_graph, rows, wait_for_db
from floodline.network import DEMO_REPORT
from floodline.scenario import reset_scenario

# Reconstructed. Fixed strings so a second run writes the same clock.
TICKS = (
    {
        "seq": 0,
        "t": T_ROUTE,
        "label": "Opening route. A-7 is transporting. No roads are closed.",
    },
    {
        "seq": 1,
        "t": T_CLOSE,
        "label": "Citizen report closes the V-31 Paiporta bridge.",
    },
    {
        "seq": 2,
        "t": "2024-10-29T14:15:00Z",
        "label": "Paiporta exits close. The district is cut off.",
    },
)


def _tick(graph, spec: dict) -> None:
    graph.query(
        """
        CREATE (:Tick {seq: $seq, t: $t, label: $label})
        """,
        spec,
    )


def _link_decisions(graph, spec: dict) -> None:
    graph.query(
        """
        MATCH (tick:Tick {seq: $seq}), (d:Decision {at: $t})
        MERGE (d)-[:AT_TICK]->(tick)
        """,
        {"seq": spec["seq"], "t": spec["t"]},
    )


def _seed_incident(graph, at: str, osmid: int) -> None:
    graph.query(
        """
        CREATE (:Incident {
          id: 'inc-seed-1',
          kind: 'medical',
          severity: 4,
          status: 'open',
          created_at: $at,
          source: 'synthetic'
        })
        """,
        {"at": at},
    )
    graph.query(
        """
        MATCH (inc:Incident {id: 'inc-seed-1'}), (i:Intersection {osmid: $osmid})
        CREATE (inc)-[:ACCESS {dist_m: 0}]->(i)
        """,
        {"osmid": osmid},
    )


def play(settings: Settings | None = None) -> dict:
    """Reset the synthetic graph and apply the three ticks. Returns the clock log."""
    cfg = settings or Settings.from_env()
    reset_scenario(cfg)
    graph = get_graph(cfg)
    opening, report_tick, corridor_tick = TICKS
    _tick(graph, opening)
    _link_decisions(graph, opening)

    scout = handle_report(graph, DEMO_REPORT, at=report_tick["t"])
    dispatcher = reroute(graph, at=report_tick["t"])
    _seed_incident(graph, report_tick["t"], scout["origin_osmid"])
    _tick(graph, report_tick)
    _link_decisions(graph, report_tick)

    before_flow = evac_capacity(graph, "Paiporta")["max_flow"]
    corridor = prove_cutoff(graph, "Paiporta", at=corridor_tick["t"])
    record_corridor(graph, corridor, at=corridor_tick["t"])
    _tick(graph, corridor_tick)
    _link_decisions(graph, corridor_tick)

    paiporta = next(item for item in corridor["after"]["areas"] if item["name"] == "Paiporta")
    log = [
        {
            "seq": opening["seq"],
            "t": opening["t"],
            "label": opening["label"],
            "travel_s": dispatcher["previous"]["travel_s"] if dispatcher.get("previous") else None,
        },
        {
            "seq": report_tick["seq"],
            "t": report_tick["t"],
            "label": report_tick["label"],
            "travel_s": dispatcher["route"]["travel_s"],
            "closed": scout["closed"]["road_id"],
            "incident": "inc-seed-1",
        },
        {
            "seq": corridor_tick["seq"],
            "t": corridor_tick["t"],
            "label": corridor_tick["label"],
            "cut_off": paiporta["cut_off"],
            "max_flow_before": before_flow,
            "max_flow_after": corridor["after"]["evac"]["max_flow"],
        },
    ]
    return {"ticks": log}


def assert_replay(result: dict) -> None:
    ticks = result["ticks"]
    if [item["t"] for item in ticks] != [spec["t"] for spec in TICKS]:
        raise AssertionError(f"Clock changed: {ticks}")
    if ticks[0]["travel_s"] != 215 or ticks[1]["travel_s"] != 320:
        raise AssertionError(f"Routes were not 215 then 320: {ticks[0]['travel_s']} {ticks[1]['travel_s']}")
    if ticks[1]["closed"] != "v31-paiporta-bridge" or ticks[1]["incident"] != "inc-seed-1":
        raise AssertionError(f"Report tick did not close the bridge: {ticks[1]}")
    if (
        not ticks[2]["cut_off"]
        or ticks[2]["max_flow_before"] != 1200
        or ticks[2]["max_flow_after"] != 0
    ):
        raise AssertionError(f"Corridor tick did not cut Paiporta off: {ticks[2]}")


def main(argv: list[str] | None = None) -> int:
    del argv
    settings = Settings.from_env()
    print("Floodline replay — reconstructed clock, seed is the fixed timeline")
    print(f"FalkorDB {settings.host}:{settings.port}  graph={settings.graph}")
    try:
        wait_for_db(settings)
    except TimeoutError as exc:
        print(exc, file=sys.stderr)
        return 2
    result = play(settings)
    for tick in result["ticks"]:
        extra = ""
        if "travel_s" in tick and tick["seq"] == 0:
            extra = f"  route {tick['travel_s']} s"
        elif tick["seq"] == 1:
            extra = f"  route {tick['travel_s']} s  closed {tick['closed']}  incident {tick['incident']}"
        elif tick["seq"] == 2:
            extra = (
                f"  cut_off={tick['cut_off']}  "
                f"maxFlow {tick['max_flow_before']} → {tick['max_flow_after']} veh/h"
            )
        print(f"TICK {tick['seq']}  {tick['t']}  {tick['label']}{extra}")
    stored = rows(get_graph(settings).query("MATCH (t:Tick) RETURN t.seq, t.t ORDER BY t.seq"))
    print(f"Tick nodes: {stored}")
    try:
        assert_replay(result)
    except AssertionError as exc:
        print(f"DEMO FAILED: {exc}", file=sys.stderr)
        return 1
    print("DEMO OK: the same three ticks, the same routes, the same cut-off.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
