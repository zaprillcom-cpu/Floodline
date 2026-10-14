"""Kill Dispatcher after it claims a task, then resume from the graph.

The task and the CLAIMED edge are the only memory. Run the two phases in
separate processes:

    PYTHONPATH=backend python -m floodline.resume_demo --crash
    PYTHONPATH=backend python -m floodline.resume_demo --resume

`--crash` loads the synthetic network, lets Scout close the bridge, and claims
the reassess task. It exits before writing the route Decision.

`--resume` does not reload the graph. It finishes that claimed task, then
claims anything still open.
"""

from __future__ import annotations

import argparse
import sys

from floodline.agents.dispatcher import resume
from floodline.agents.scout import handle_report
from floodline.config import T_CLOSE, Settings
from floodline.db import get_graph, rows, wait_for_db
from floodline.network import DEMO_REPORT
from floodline.scenario import assert_reroute, reset_scenario, snapshot
from floodline.tools import claim_task


def crash(settings: Settings) -> int:
    reset_scenario(settings)
    graph = get_graph(settings)
    scout = handle_report(graph, DEMO_REPORT, at=T_CLOSE)
    task = claim_task(graph, "Dispatcher", "reassess", T_CLOSE)
    if task is None:
        print("DEMO FAILED: nothing to claim", file=sys.stderr)
        return 1
    print("Scout closed the road and handed off a reassess task.")
    print(f"Dispatcher claimed {task['id']} and then the process stopped.")
    print("No route Decision has been written for that task yet.")
    print("Start a new process with: PYTHONPATH=backend python -m floodline.resume_demo --resume")
    print(f"  handoff task: {scout['task_id']}")
    return 0


def resume_from_graph(settings: Settings) -> int:
    graph = get_graph(settings)
    open_or_claimed = rows(
        graph.query(
            """
            MATCH (t:Task {kind: 'reassess'})
            WHERE t.status IN ['open', 'claimed']
            RETURN t.id, t.status
            """
        )
    )
    if not open_or_claimed:
        print("No open or claimed reassess task. Run --crash first.", file=sys.stderr)
        return 1
    print("Dispatcher process started again. Reading tasks from FalkorDB.")
    for task_id, status in open_or_claimed:
        print(f"  found {task_id} status={status}")
    result = resume(graph, at=T_CLOSE)
    print(f"Heartbeat: {result['heartbeat_at']}")
    for item in result["finished"]:
        how = "resumed claimed task" if item["resumed"] else "claimed an open task"
        print(f"  {how} {item['task_id']}")
        print(f"  route {item['route']['travel_s']} s via {' → '.join(item['route']['names'])}")
    state = snapshot(settings)
    try:
        assert_reroute(state)
    except AssertionError as exc:
        print(f"DEMO FAILED: {exc}", file=sys.stderr)
        return 1
    print("DEMO OK: the new process finished the route from the graph, without the old process.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Crash Dispatcher and resume from FalkorDB")
    phase = parser.add_mutually_exclusive_group(required=True)
    phase.add_argument("--crash", action="store_true")
    phase.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    print(f"FalkorDB {settings.host}:{settings.port}  graph={settings.graph}")
    try:
        wait_for_db(settings)
    except TimeoutError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.crash:
        return crash(settings)
    return resume_from_graph(settings)


if __name__ == "__main__":
    raise SystemExit(main())
