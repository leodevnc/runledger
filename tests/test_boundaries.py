import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runledger import ReportSink, Store, Worker, investigation


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = Store(root / "runs.sqlite3")
        self.sink = ReportSink(root / "reports.sqlite3")
        self.worker = Worker(self.store, self.sink)
        self.run_id = self.store.create(investigation(), "boundary")

    def test_state_change_rolls_back_if_event_append_fails(self):
        claim = self.store.claim(self.run_id, "owner")
        before = self.store.get(self.run_id)
        with patch.object(self.store, "_event", side_effect=RuntimeError("disk fault")):
            with self.assertRaises(RuntimeError): self.store.complete(claim, {"result": "ok"})
        after = self.store.get(self.run_id)
        self.assertEqual(after["steps"], before["steps"])
        self.assertEqual(after["events"], before["events"])

    def test_cancellation_does_not_retract_an_already_committed_effect(self):
        for _ in range(3): self.worker.tick(self.run_id)
        proposal = self.store.get(self.run_id)["steps"][-1]
        self.store.decide(self.run_id, "publish", proposal["approval_hash"], approve=True)
        result = self.worker.tick(self.run_id, after_effect=lambda: self.store.cancel(self.run_id))
        self.assertEqual(result["outcome"], "stale_worker_rejected")
        self.assertEqual(self.store.get(self.run_id)["state"], "cancelled")
        self.assertEqual(len(self.sink.receipts(self.run_id)), 1)


if __name__ == "__main__": unittest.main()
