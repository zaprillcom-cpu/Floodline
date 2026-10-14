"""Deterministic stand-in for a tool-calling LLM.

The planner only chooses a tool. It does not compute routes or edit the graph.
Swap `plan` for a real model later; keep this function as the offline fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCall:
    tool: str
    args: dict
    rationale: str


_BRIDGE = ("v-31", "v31", "paiporta bridge", "puente de paiporta")
_CLOSURE_WORDS = ("closed", "flood", "water", "impassable", "under water", "not passable")


def plan(text: str) -> ToolCall:
    lowered = text.lower()
    if any(hint in lowered for hint in _BRIDGE):
        return ToolCall(
            tool="close_road",
            args={"road_id": "v31-paiporta-bridge"},
            rationale="Report names the V-31 Paiporta bridge.",
        )
    if any(word in lowered for word in _CLOSURE_WORDS):
        match = re.search(r"(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", text)
        if match:
            return ToolCall(
                tool="close_nearest",
                args={"lat": float(match.group(1)), "lon": float(match.group(2))},
                rationale="Report gives a coordinate and a closure condition.",
            )
    return ToolCall(
        tool="noop",
        args={},
        rationale="No road closure could be extracted from the report.",
    )
