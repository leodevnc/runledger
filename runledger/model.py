"""Workflow contracts. Tool policy comes from the server registry, not from a planner."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

VERSIONS = {"read_incident": "1", "assess_incident": "1", "publish_report": "1"}
APPROVAL_TOOLS = frozenset({"publish_report"})
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


class Conflict(ValueError):
    pass


class StaleLease(Conflict):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate_plan(plan: dict) -> dict:
    # Round-trip isolates the caller's mutable objects and rejects non-JSON values.
    encoded = canonical(plan)
    if len(encoded.encode()) > 128_000:
        raise ValueError("plan exceeds 128 KB")
    plan = json.loads(encoded)
    if set(plan) != {"name", "steps"} or not isinstance(plan["name"], str) or not 1 <= len(plan["name"]) <= 120:
        raise ValueError("plan requires a name and steps")
    if not isinstance(plan["steps"], list) or not 1 <= len(plan["steps"]) <= 32:
        raise ValueError("plan requires 1 to 32 steps")
    seen = set()
    for step in plan["steps"]:
        if not isinstance(step, dict) or set(step) != {"id", "tool", "args"}:
            raise ValueError("each step requires id, tool, args")
        key = step["id"]
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", key) or key in seen:
            raise ValueError("step identifiers must be unique lowercase names")
        tool = step["tool"]
        if not isinstance(tool, str) or tool not in VERSIONS:
            raise ValueError("tool is not in the server allowlist")
        args = step["args"]
        if not isinstance(args, dict):
            raise ValueError("tool arguments must be an object")
        if tool == "read_incident":
            if set(args) != {"incident_id"} or args["incident_id"] not in ("INC-1042", "INC-1043"):
                raise ValueError("unknown synthetic incident")
        else:
            field = "incident" if tool == "assess_incident" else "assessment"
            if set(args) != {field} or not isinstance(args[field], dict) or set(args[field]) != {"$step"}:
                raise ValueError(f"{tool} requires a {field} step reference")
            ref = args[field]["$step"]
            if not isinstance(ref, str) or ref not in seen:
                raise ValueError("references must target an earlier step")
            expected_tool = "read_incident" if tool == "assess_incident" else "assess_incident"
            if next(s["tool"] for s in plan["steps"] if s["id"] == ref) != expected_tool:
                raise ValueError("step reference has incompatible output type")
        seen.add(key)
    return plan


def investigation(incident_id: str = "INC-1042") -> dict:
    return {
        "name": f"Investigate {incident_id}",
        "steps": [
            {"id": "collect", "tool": "read_incident", "args": {"incident_id": incident_id}},
            {"id": "assess", "tool": "assess_incident", "args": {"incident": {"$step": "collect"}}},
            {"id": "publish", "tool": "publish_report", "args": {"assessment": {"$step": "assess"}}},
        ],
    }
