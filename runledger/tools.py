"""Synthetic incident tools and a separately committed, idempotent report service."""
import json
import sqlite3
from contextlib import closing

from .model import Conflict, canonical, digest

INCIDENTS = {
    "INC-1042": {"incident_id": "INC-1042", "service": "document-indexer", "error_rate": 0.12,
                 "queue_age_seconds": 420, "evidence": ["checksum-mismatch", "retry-budget-exhausted"]},
    "INC-1043": {"incident_id": "INC-1043", "service": "document-indexer", "error_rate": 0.02,
                 "queue_age_seconds": 15, "evidence": ["brief-upstream-timeout"]},
}


def pure_tool(tool, inputs, *, threshold=0.05):
    if tool == "read_incident":
        return json.loads(canonical(INCIDENTS[inputs["incident_id"]]))
    if tool == "assess_incident":
        incident = inputs["incident"]
        severe = incident["error_rate"] >= threshold
        return {"incident_id": incident["incident_id"], "severity": "high" if severe else "low",
                "recommendation": "pause import and inspect checksums" if severe else "monitor recovery",
                "evidence": incident["evidence"]}
    raise ValueError("not a pure tool")


class ReportSink:
    """Emulates an external API with a stable idempotency key contract.

    This database is intentionally separate from the workflow transaction. It is
    not an external service, and its guarantees do not generalize to arbitrary APIs.
    """
    def __init__(self, path):
        self.path = str(path)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS reports (effect_key TEXT PRIMARY KEY, input_hash TEXT NOT NULL, receipt TEXT NOT NULL)")
            db.commit()

    def publish(self, effect_key, assessment):
        input_hash = digest(assessment)
        with closing(sqlite3.connect(self.path, timeout=10, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = db.execute("SELECT input_hash,receipt FROM reports WHERE effect_key=?", (effect_key,)).fetchone()
                if existing:
                    if existing[0] != input_hash:
                        raise Conflict("effect key reused with changed inputs")
                    receipt = json.loads(existing[1])
                else:
                    receipt = {"report_id": digest(effect_key)[:16], "effect_key": effect_key,
                               "assessment": assessment, "input_hash": input_hash}
                    db.execute("INSERT INTO reports VALUES (?,?,?)", (effect_key, input_hash, canonical(receipt)))
                db.commit()
                return receipt
            except BaseException:
                db.rollback()
                raise

    def receipts(self, run_id):
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT receipt FROM reports WHERE effect_key LIKE ? ORDER BY effect_key", (run_id + ":%",)).fetchall()
            return [json.loads(row[0]) for row in rows]
