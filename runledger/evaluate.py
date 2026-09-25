"""Reproducible synthetic failure matrix; no model calls or quality claims."""
import argparse
import json
import platform
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path

from . import InjectedCrash, ReportSink, Store, Worker, investigation
from .replay import replay

SCENARIOS = ("healthy", "crash_after_effect", "transient_retry", "approval_rejected", "cancelled")


def evaluate(repeats=5):
    rows, timings = [], []
    with tempfile.TemporaryDirectory(prefix="runledger-evaluation-") as directory:
        clock = [1000.0]
        store = Store(Path(directory) / "runs.sqlite3", clock=lambda: clock[0])
        sink = ReportSink(Path(directory) / "reports.sqlite3")
        for scenario in SCENARIOS:
            for iteration in range(repeats):
                started = time.perf_counter()
                run_id = store.create(investigation(), f"{scenario}-{iteration}")
                worker = Worker(store, sink)
                if scenario == "cancelled":
                    store.cancel(run_id)
                else:
                    if scenario == "transient_retry":
                        worker.tick(run_id, fault="transient")
                        clock[0] += 1
                    worker.tick(run_id); worker.tick(run_id); worker.tick(run_id)
                    proposal = store.get(run_id)["steps"][-1]
                    store.decide(run_id, "publish", proposal["approval_hash"], approve=scenario != "approval_rejected", actor="evaluation-harness")
                    if scenario == "crash_after_effect":
                        try: worker.tick(run_id, fault="after_effect")
                        except InjectedCrash: pass
                        clock[0] += 11
                    worker.tick(run_id)
                run = store.get(run_id)
                receipts = len(sink.receipts(run_id))
                expected_state = "cancelled" if scenario == "cancelled" else "failed" if scenario == "approval_rejected" else "succeeded"
                expected_effects = int(expected_state == "succeeded")
                comparison = replay(run, policy="conservative")
                passed = run["state"] == expected_state and receipts == expected_effects and run["journal_valid"]
                rows.append({"scenario": scenario, "iteration": iteration, "passed": passed, "state": run["state"],
                             "attempts": sum(s["attempt"] for s in run["steps"]), "effects": receipts,
                             "journal_valid": run["journal_valid"], "replay_side_effects": comparison["side_effects_executed"]})
                timings.append((time.perf_counter() - started) * 1000)
    return {"suite": "synthetic-failure-matrix-v1", "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
            "runs": len(rows), "passed": sum(row["passed"] for row in rows),
            "duplicate_effects": sum(max(0, row["effects"] - 1) for row in rows),
            "median_case_ms": round(statistics.median(timings), 2), "max_case_ms": round(max(timings), 2),
            "notes": "Local synthetic tools; fake lease clock; timings include SQLite work, not model or network latency. No production throughput claim.",
            "results": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 100:
        parser.error("repeats must be between 1 and 100")
    report = evaluate(args.repeats)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["passed"] == report["runs"] else 1)


if __name__ == "__main__": main()
