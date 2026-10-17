"""Tool-calling planner. FalkorDB still chooses the road and the route.

``plan_with_llm`` asks OpenAI for schema-validated tool calls
(``geocode``, ``close_road``, ``severity``). A missing ``OPENAI_API_KEY``,
a transport error, or arguments that do not match the schema fall back to
``floodline.agents.planner.plan``. The returned ``planner`` field is
``llm`` or ``keyword`` so Decision and Task nodes can record which one ran.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from floodline.agents.planner import ToolCall, plan

_SEVERITY = {"low", "medium", "high", "critical"}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "geocode",
            "description": "Resolve a street name or landmark to candidate ROAD edges already in the graph.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Street, road code, or landmark from the report."},
                    "landmark": {"type": "string"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_road",
            "description": "Close the road the report says is impassable. Prefer a road_id from the candidate list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "road_id": {"type": "string"},
                    "road_name": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "severity",
            "description": "How severe the closure is.",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                    "note": {"type": "string"},
                },
                "required": ["level"],
                "additionalProperties": False,
            },
        },
    },
]

_SYSTEM = (
    "You turn one citizen disaster report into tool calls. "
    "The street graph lives in FalkorDB. Do not invent travel times, paths, or road ids "
    "that are not in the candidate list. "
    "Call close_road when the report says a road is impassable, using a candidate road_id when one matches. "
    "Call geocode when a street or landmark still needs a name match. "
    "Call severity with level low, medium, high, or critical. "
    "You may call more than one tool. Arguments must match the schema."
)


def _context(graph, text: str) -> dict:
    """Candidate roads and nearby shelters, so the model picks from the graph."""
    if graph is None:
        return {"roads": [], "shelters": []}
    from floodline.db import rows
    from floodline.stubs import geocode_report

    geo = geocode_report(text, graph)
    roads = [
        {"road_id": edge.get("road_id"), "name": edge.get("name"), "dist_m": edge.get("dist_m")}
        for edge in (geo.get("edges") or [])[:8]
    ]
    shelters = []
    try:
        found = rows(
            graph.query(
                """
                MATCH (s:Shelter)-[n:NEAR]->(i:Intersection)
                RETURN s.id, s.name, coalesce(s.open, true), n.dist_m
                ORDER BY n.dist_m ASC
                LIMIT 8
                """
            )
        )
        shelters = [
            {"id": sid, "name": name, "open": bool(open_), "dist_m": int(dist or 0)}
            for sid, name, open_, dist in found
        ]
    except Exception:
        shelters = []
    return {"roads": roads, "shelters": shelters}


def _messages(text: str, context: dict) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                "Report:\n"
                f"{text}\n\n"
                "Graph context (from FalkorDB):\n"
                f"{json.dumps(context, ensure_ascii=False)}"
            ),
        },
    ]


def _openai_complete(messages: list[dict], tools: list[dict]) -> dict:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    body = json.dumps(
        {
            "model": model,
            "temperature": 0,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail}") from exc
    message = (payload.get("choices") or [{}])[0].get("message") or {}
    calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        raw = function.get("arguments") or "{}"
        if isinstance(raw, str):
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = None
        elif isinstance(raw, dict):
            args = raw
        else:
            args = None
        calls.append({"name": function.get("name"), "arguments": args})
    return {"content": message.get("content") or "", "tool_calls": calls}


def _valid(name: str, args) -> bool:
    if not isinstance(args, dict):
        return False
    if name == "geocode":
        return isinstance(args.get("query"), str) and bool(args["query"].strip())
    if name == "close_road":
        road_id = args.get("road_id")
        road_name = args.get("road_name")
        if road_id is not None and not isinstance(road_id, str):
            return False
        if road_name is not None and not isinstance(road_name, str):
            return False
        if args.get("reason") is not None and not isinstance(args.get("reason"), str):
            return False
        return bool((road_id or "").strip() or (road_name or "").strip())
    if name == "severity":
        return args.get("level") in _SEVERITY
    return False


def _validated(raw: dict) -> ToolCall | None:
    """Accept a schema-valid tool call, or None so the caller can fall back."""
    if not isinstance(raw, dict):
        return None
    calls = raw.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        return None
    parsed = []
    for call in calls:
        if not isinstance(call, dict):
            return None
        name = call.get("name")
        args = call.get("arguments")
        if not _valid(name, args):
            return None
        parsed.append((name, args))
    by_name = {name: args for name, args in parsed}
    reasoning = str(raw.get("content") or "").strip()
    severity = by_name.get("severity")
    level = severity.get("level") if severity else None
    if not reasoning and severity and severity.get("note"):
        reasoning = str(severity["note"])
    if "close_road" in by_name:
        args = dict(by_name["close_road"])
        if level:
            args["severity"] = level
        if not reasoning:
            reasoning = str(args.get("reason") or "")
        rationale = str(args.get("reason") or reasoning or "The model closed a road named in the report.")
        return ToolCall("close_road", args, rationale, planner="llm", reasoning=reasoning)
    if "geocode" in by_name:
        args = dict(by_name["geocode"])
        if level:
            args["severity"] = level
        rationale = reasoning or f"Resolve {args['query']} against ROAD names."
        return ToolCall("geocode", args, rationale, planner="llm", reasoning=reasoning)
    if severity:
        rationale = reasoning or f"Severity {level}."
        return ToolCall("severity", {"level": level, "note": severity.get("note") or ""}, rationale, planner="llm", reasoning=reasoning)
    return None


def plan_with_llm(text: str, *, graph=None, context: dict | None = None, complete=None) -> ToolCall:
    """Free-text report → schema-validated tool call, else the keyword planner.

    ``complete(messages, tools)`` exists so tests can supply a model response.
    Without ``OPENAI_API_KEY`` and without ``complete``, this is ``plan(text)``.
    """
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if complete is None and not key:
        return plan(text)
    try:
        ctx = context if context is not None else _context(graph, text)
        runner = complete or _openai_complete
        raw = runner(_messages(text, ctx), TOOLS)
        call = _validated(raw)
    except Exception:
        call = None
        fallback = plan(text)
        return ToolCall(
            fallback.tool,
            fallback.args,
            fallback.rationale,
            planner="keyword",
            reasoning="The model call failed, so the keyword planner chose the action.",
        )
    if call is None:
        fallback = plan(text)
        return ToolCall(
            fallback.tool,
            fallback.args,
            fallback.rationale,
            planner="keyword",
            reasoning="The model output did not match the tool schema, so the keyword planner chose the action.",
        )
    return call
