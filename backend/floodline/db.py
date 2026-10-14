"""FalkorDB connection helpers."""

from __future__ import annotations

import time

from falkordb import FalkorDB

from floodline.config import Settings


def connect(settings: Settings | None = None, socket_timeout: float = 120) -> FalkorDB:
    cfg = settings or Settings.from_env()
    db = FalkorDB(
        host=cfg.host,
        port=cfg.port,
        socket_connect_timeout=5,
        socket_timeout=socket_timeout,
    )
    configure_runtime(db)
    return db


def configure_runtime(db: FalkorDB) -> None:
    """Raise the image defaults so a city graph and a CCH build can finish.

    v4.22.0 ships with TIMEOUT 1000 (milliseconds) and RESULTSET_SIZE 10000.
    """
    conn = db.connection
    for args in (
        ("GRAPH.CONFIG", "SET", "TIMEOUT", "120000"),
        ("GRAPH.CONFIG", "SET", "RESULTSET_SIZE", "1000000"),
    ):
        try:
            conn.execute_command(*args)
        except Exception:
            return


def falkordb_version(db: FalkorDB) -> str:
    """Module version integer 42200 is FalkorDB 4.22.0."""
    try:
        modules = db.connection.execute_command("MODULE", "LIST")
    except Exception as exc:
        return f"unknown ({exc})"
    for mod in modules or []:
        if len(mod) >= 4 and mod[0] == "name" and mod[1] == "graph" and mod[2] == "ver":
            ver = int(mod[3])
            return f"{ver // 10000}.{(ver // 100) % 100}.{ver % 100}"
    return "unknown"


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
