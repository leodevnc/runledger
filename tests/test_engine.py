import copy
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from runledger import Conflict, InjectedCrash, ReportSink, StaleLease, Store, Worker, investigation
from runledger.model import VERSIONS, validate_plan
from runledger.replay import replay
from runledger.store import verify_events
from runledger.tools import pure_tool


class Clock:
    def __init__(self): self.now = 1000.0
    def __call__(self): return self.now
    def advance(self, seconds=11): self.now += seconds


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock = Clock()
        self.store = Store(self.root / "runs.sqlite3", clock=self.clock)
        self.sink = ReportSink(self.root / "reports.sqlite3")
        self.worker = Worker(self.store, self.sink, owner="first")
        self.run_id = self.store.create(investigation(), "request-1")

    def approval(self):
        self.worker.tick(self.run_id)
        self.worker.tick(self.run_id)
        self.worker.tick(self.run_id)
        return self.store.get(self.run_id)["steps"][-1]

    def approve(self):
        proposal = self.approval()
        self.store.decide(self.run_id, "publish", proposal["approval_hash"], approve=True)
        return proposal

    def test_complete_workflow_and_integrity(self):
        self.approve()
        self.worker.tick(self.run_id)
        run = self.store.get(self.run_id)
        self.assertEqual(run["state"], "succeeded")
        self.assertEqual([s["attempt"] for s in run["steps"]], [1, 1, 1])
        self.assertTrue(run["journal_valid"])
        self.assertEqual(len(self.sink.receipts(self.run_id)), 1)

    def test_creation_is_idempotent_and_conflicts_on_changed_plan(self):
        self.assertEqual(self.store.create(investigation(), "request-1"), self.run_id)
        with self.assertRaises(Conflict): self.store.create(investigation("INC-1043"), "request-1")
        self.assertEqual(len(self.store.get(self.run_id)["events"]), 1)

    def test_server_policy_requires_approval(self):
        proposal = self.approval()
        self.assertEqual(proposal["state"], "waiting")
        self.assertEqual(self.sink.receipts(self.run_id), [])
        self.assertEqual(self.worker.tick(self.run_id)["outcome"], "idle")
        with self.assertRaises(Conflict): self.store.decide(self.run_id, "publish", "wrong", approve=True)

    def test_approval_is_bound_to_actual_inputs_and_idempotent(self):
        proposal = self.approve()
        count = len(self.store.get(self.run_id)["events"])
        self.store.decide(self.run_id, "publish", proposal["approval_hash"], approve=True)
        self.assertEqual(len(self.store.get(self.run_id)["events"]), count)
        with self.store.tx(write=True) as db:
            changed = pure_tool("assess_incident", {"incident": {"incident_id": "INC-1042", "error_rate": 0.0, "evidence": []}})
            db.execute("UPDATE steps SET output=? WHERE run_id=? AND id='assess'", (json.dumps(changed), self.run_id))
        self.worker.tick(self.run_id)
        current = self.store.get(self.run_id)["steps"][-1]
        self.assertEqual(current["state"], "waiting")
        self.assertNotEqual(current["approval_hash"], proposal["approval_hash"])
        self.assertEqual(self.sink.receipts(self.run_id), [])

    def test_rejection_prevents_publication(self):
        proposal = self.approval()
        self.store.decide(self.run_id, "publish", proposal["approval_hash"], approve=False)
        self.assertEqual(self.worker.tick(self.run_id)["outcome"], "idle")
        self.assertEqual(self.store.get(self.run_id)["state"], "failed")
        self.assertEqual(self.sink.receipts(self.run_id), [])

    def test_crash_after_external_commit_recovers_without_duplicate_effect(self):
        self.approve()
        with self.assertRaises(InjectedCrash): self.worker.tick(self.run_id, fault="after_effect")
        run = self.store.get(self.run_id)
        self.assertEqual(run["steps"][-1]["state"], "running")
        self.assertIsNone(run["steps"][-1]["output"])
        self.assertEqual(len(self.sink.receipts(self.run_id)), 1)
        self.clock.advance()
        Worker(self.store, self.sink, owner="replacement").tick(self.run_id)
        run = self.store.get(self.run_id)
        self.assertEqual(run["state"], "succeeded")
        self.assertEqual(run["steps"][-1]["attempt"], 2)
        self.assertEqual(len(self.sink.receipts(self.run_id)), 1)
        self.assertIn("lease_reclaimed", [e["kind"] for e in run["events"]])

    def test_crash_before_tool_recovers_after_expiration(self):
        abandoned = self.store.claim(self.run_id, "abandoned")
        self.clock.advance()
        self.worker.tick(self.run_id)
        self.assertEqual(self.store.get(self.run_id)["steps"][0]["attempt"], 2)
        with self.assertRaises(StaleLease): self.store.complete(abandoned, {})

    def test_live_lease_cannot_be_stolen(self):
        self.assertIsNotNone(self.store.claim(self.run_id, "a"))
        self.assertIsNone(self.store.claim(self.run_id, "b"))

    def test_stale_complete_and_renew_are_rejected(self):
        old = self.store.claim(self.run_id, "a")
        self.clock.advance()
        current = self.store.claim(self.run_id, "b")
        self.assertGreater(current["epoch"], old["epoch"])
        with self.assertRaises(StaleLease): self.store.complete(old, {})
        with self.assertRaises(StaleLease): self.store.renew(old)
        self.store.complete(current, pure_tool(current["tool"], current["inputs"]))

    def test_renewed_lease_blocks_takeover(self):
        claim = self.store.claim(self.run_id, "a")
        self.clock.advance(9)
        self.store.renew(claim)
        self.clock.advance(2)
        self.assertIsNone(self.store.claim(self.run_id, "b"))

    def test_expired_lease_cannot_commit_even_without_replacement(self):
        claim = self.store.claim(self.run_id, "a")
        self.clock.advance(10)
        with self.assertRaises(StaleLease): self.store.complete(claim, {})

    def test_concurrent_claims_have_one_owner(self):
        barrier = threading.Barrier(8)
        def claim(index):
            barrier.wait(timeout=5)
            return self.store.claim(self.run_id, f"worker-{index}")
        with ThreadPoolExecutor(max_workers=8) as pool: claims = list(pool.map(claim, range(8)))
        self.assertEqual(sum(c is not None for c in claims), 1)

    def test_transient_failure_waits_and_eventually_exhausts(self):
        for attempt in range(3):
            self.worker.tick(self.run_id, fault="transient")
            if attempt < 2:
                self.assertEqual(self.worker.tick(self.run_id)["outcome"], "idle")
                self.clock.advance()
        self.assertEqual(self.store.get(self.run_id)["state"], "failed")
        self.assertEqual(self.store.get(self.run_id)["steps"][0]["attempt"], 3)

    def test_repeated_worker_loss_exhausts_attempt_limit(self):
        for _ in range(3):
            self.assertIsNotNone(self.store.claim(self.run_id, "lost"))
            self.clock.advance()
        self.assertIsNone(self.store.claim(self.run_id, "replacement"))
        self.assertEqual(self.store.get(self.run_id)["state"], "failed")

    def test_cancel_fences_inflight_worker(self):
        claim = self.store.claim(self.run_id, "a")
        self.store.cancel(self.run_id)
        with self.assertRaises(StaleLease): self.store.complete(claim, {})
        self.assertIsNone(self.store.claim(self.run_id, "b"))
        self.assertEqual(self.store.get(self.run_id)["state"], "cancelled")

    def test_sink_conflict_and_concurrent_duplicates(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            receipts = list(pool.map(lambda _: self.sink.publish(self.run_id + ":publish", {"ok": True}), range(20)))
        self.assertTrue(all(r == receipts[0] for r in receipts))
        self.assertEqual(len(self.sink.receipts(self.run_id)), 1)
        with self.assertRaises(Conflict): self.sink.publish(self.run_id + ":publish", {"ok": False})

    def test_replay_does_not_execute_side_effects_and_exposes_divergence(self):
        self.approve(); self.worker.tick(self.run_id)
        run = self.store.get(self.run_id)
        self.assertEqual(replay(run)["divergences"], 0)
        changed = replay(run, policy="conservative")
        self.assertEqual(changed["divergences"], 1)
        self.assertEqual(changed["side_effects_executed"], 0)
        self.assertEqual(changed["rows"][-1]["status"], "recorded_effect")
        self.assertEqual(len(self.sink.receipts(self.run_id)), 1)

    def test_partial_replay_does_not_fabricate_unexecuted_outputs(self):
        self.worker.tick(self.run_id)
        result = replay(self.store.get(self.run_id))
        self.assertEqual([r["status"] for r in result["rows"]], ["match", "not_checkpointed", "not_checkpointed"])

    def test_event_mutation_is_detected(self):
        events = self.store.get(self.run_id)["events"]
        self.assertTrue(verify_events(self.run_id, events))
        events[0]["data"]["step_count"] = 100
        self.assertFalse(verify_events(self.run_id, events))

    def test_tool_version_drift_blocks_resume(self):
        with self.store.tx(write=True) as db:
            versions = dict(VERSIONS, read_incident="99")
            db.execute("UPDATE runs SET versions=? WHERE id=?", (json.dumps(versions), self.run_id))
        with self.assertRaises(Conflict): self.worker.tick(self.run_id)

    def test_unknown_tools_and_forward_refs_are_rejected(self):
        plan = investigation()
        for mutate in (
            lambda p: p["steps"][0].update(tool="shell"),
            lambda p: p["steps"][1]["args"].update(incident={"$step": "publish"}),
            lambda p: p["steps"][2].update(requires_approval=False),
            lambda p: p["steps"][0]["args"].update(incident_id="../../secrets"),
        ):
            changed = copy.deepcopy(plan); mutate(changed)
            with self.assertRaises(ValueError): validate_plan(changed)

    def test_oversized_output_does_not_commit_checkpoint(self):
        claim = self.store.claim(self.run_id, "worker")
        with self.assertRaises(ValueError): self.store.complete(claim, "x" * 128_001)
        self.assertEqual(self.store.get(self.run_id)["steps"][0]["state"], "running")

    def test_real_process_exit_after_effect_commit(self):
        self.approve()
        # The child has a fixed clock, a one-second lease, and exits without cleanup.
        code = """
import os, sys
from runledger import Store, ReportSink, Worker
s=Store(sys.argv[1], clock=lambda: 1000.0)
k=ReportSink(sys.argv[2])
Worker(s,k,owner='crashed-process',lease_seconds=1).tick(sys.argv[3], after_effect=lambda: os._exit(23))
"""
        result = subprocess.run([sys.executable, "-c", code, self.store.path, self.sink.path, self.run_id], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(len(self.sink.receipts(self.run_id)), 1)
        self.clock.advance(2)
        restarted = Store(self.store.path, clock=self.clock)
        Worker(restarted, self.sink).tick(self.run_id)
        self.assertEqual(restarted.get(self.run_id)["state"], "succeeded")
        self.assertEqual(len(self.sink.receipts(self.run_id)), 1)


if __name__ == "__main__": unittest.main()
