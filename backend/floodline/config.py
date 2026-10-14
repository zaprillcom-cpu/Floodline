"""Runtime settings. Values come from the environment or a local .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv() -> None:
    """Load KEY=VALUE lines from the first .env found. Does not override the environment."""
    here = Path(__file__).resolve()
    candidates = [Path.cwd() / ".env", here.parents[2] / ".env"]
    for path in candidates:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))
        return


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    graph: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            host=os.environ.get("FALKORDB_HOST", "localhost"),
            port=int(os.environ.get("FALKORDB_PORT", "6379")),
            graph=os.environ.get("FALKORDB_GRAPH", "floodline"),
        )


# Paths whose weight is at least this are treated as unreachable.
UNREACHABLE_WEIGHT = 100_000_000
CLOSED_TRAVEL_S = 1_000_000_000

# Reconstructed clock for the Valencia slice. Not the real DANA timeline.
T_ROUTE = "2024-10-29T14:01:00Z"
T_CLOSE = "2024-10-29T14:02:00Z"

# A* heuristic is haversine metres. travel_s is seconds.
# 1/33.3 ≈ seconds per metre at 120 km/h, which keeps the heuristic admissible.
ASTAR_HEURISTIC_SCALE = 0.03
