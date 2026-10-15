"""LLM planner stub. OWNER: Sahil.

The live planner stays the deterministic keyword function in ``planner.py``.
This module is the seam Sahil replaces with a schema-validated tool call
(``geocode``, ``close_road``, ``severity``). Bad JSON and model failures must
fall back to ``plan``.
"""

from __future__ import annotations

from floodline.agents.planner import ToolCall, plan


def plan_with_llm(text: str) -> ToolCall:
    """TODO(Sahil): free-text report → tool calls, schema-validated.

    Tools the model may call: ``geocode``, ``close_road``, ``severity``.
    On any failure, including JSON that does not match the schema, return
    ``floodline.agents.planner.plan(text)`` and keep ``narrator`` as ``stub``.

    Fallback: the deterministic planner, so reports still close a road.
    """
    # TODO(Sahil)
    return plan(text)
