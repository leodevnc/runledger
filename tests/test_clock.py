import tempfile
import unittest
from pathlib import Path

from runledger import Store, investigation


class ClockFormatTests(unittest.TestCase):
    def test_integer_clock_preserves_hash_after_sqlite_real_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "runs.sqlite3", clock=lambda: 1000)
            run_id = store.create(investigation(), "integer-clock")
            self.assertTrue(store.get(run_id)["journal_valid"])


if __name__ == "__main__": unittest.main()
