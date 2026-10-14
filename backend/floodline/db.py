"""FalkorDB connection helpers."""

from __future__ import annotations

import time

from falkordb import FalkorDB

from floodline.config import Settings


def connect(settings: Settings | None = None) -> FalkorDB:
    cfg = settings or Settings.from_env()
    return FalkorDB(
        host=cfg.host,
        port=cfg.port,
        socket_connect_timeout=5,
        socket_timeout=30,
    )


def get_graph(settings: Settings | None = None):
    cfg = settings or Settings.from_env()
    return connect(cfg).select_graph(cfg.graph)


def wait_for_db(settings: Settings | None = None, timeout_s: float = 60) -> None:
    cfg = settings or Settings.from_env()
    deadline = time.time() + timeout_s
    last: Exception | None = None
    while time.time() < deadline:
        try:
            graph = connect(cfg).select_graph(cfg.graph)
            graph.query("RETURN 1")
            return
        except Exception as exc:  # connection refused while the container boots
            last = exc
            time.sleep(1)
    raise TimeoutError(f"FalkorDB not reachable at {cfg.host}:{cfg.port}: {last}")


def rows(result) -> list:
    return list(result.result_set or [])
