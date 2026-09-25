"""Side-effect-free counterfactual replay of recorded pure tool boundaries."""
from .model import VERSIONS, digest
from .tools import pure_tool


def replay(run, *, policy="recorded"):
    if policy not in ("recorded", "conservative"):
        raise ValueError("unknown replay policy")
    rows = []
    for step in run["steps"]:
        row = {"step": step["id"], "tool": step["tool"]}
        if step["state"] != "succeeded":
            row.update(status="not_checkpointed", explanation="No completed output to compare")
        elif step["tool"] == "publish_report":
            row.update(status="recorded_effect", recorded=step["output"],
                       explanation="Recorded receipt only; this operation is never invoked during replay")
        elif run["versions"].get(step["tool"]) != VERSIONS[step["tool"]]:
            row.update(status="version_mismatch", explanation="Recreate the recorded tool version first")
        else:
            candidate = pure_tool(step["tool"], step["inputs"], threshold=0.20 if policy == "conservative" else 0.05)
            row.update(status="match" if digest(candidate) == digest(step["output"]) else "diverged",
                       recorded=step["output"], candidate=candidate)
        rows.append(row)
    return {"policy": policy, "run_id": run["id"], "rows": rows,
            "divergences": sum(row["status"] == "diverged" for row in rows),
            "side_effects_executed": 0,
            "scope": "Per-step comparison using recorded inputs; not a new end-to-end execution or model evaluation"}
