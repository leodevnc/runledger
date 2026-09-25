"""Durable serial workflows, fenced leases, approvals, and a hash-linked event log."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from .model import APPROVAL_TOOLS, TERMINAL, VERSIONS, Conflict, StaleLease, canonical, digest, validate_plan


class Store:
    def __init__(self, path: str | Path, *, clock=time.time):
        self.path = str(path)
        self.clock = clock
        if self.path == ":memory:":
            raise ValueError("a file-backed database is required")
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
        with self.tx(write=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                state TEXT NOT NULL, plan TEXT NOT NULL, plan_hash TEXT NOT NULL,
                versions TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS steps (
                run_id TEXT NOT NULL REFERENCES runs(id), ordinal INTEGER NOT NULL,
                id TEXT NOT NULL, tool TEXT NOT NULL, args TEXT NOT NULL, state TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0, epoch INTEGER NOT NULL DEFAULT 0,
                owner TEXT, lease_until REAL, ready_at REAL NOT NULL DEFAULT 0,
                inputs TEXT, output TEXT, error TEXT, approval_hash TEXT, approval_state TEXT,
                PRIMARY KEY(run_id,id), UNIQUE(run_id,ordinal))""")
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                run_id TEXT NOT NULL REFERENCES runs(id), seq INTEGER NOT NULL,
                at REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL,
                previous_hash TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(run_id,seq))""")

    @contextmanager
    def tx(self, *, write=False):
        with closing(sqlite3.connect(self.path, isolation_level=None, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _event(self, db, run_id, kind, data):
        row = db.execute("SELECT seq,hash FROM events WHERE run_id=? ORDER BY seq DESC LIMIT 1", (run_id,)).fetchone()
        seq, previous = (row["seq"] + 1, row["hash"]) if row else (1, "0" * 64)
        at = float(self.clock())
        event_hash = digest({"run_id": run_id, "seq": seq, "at": at, "kind": kind, "data": data, "previous_hash": previous})
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (run_id, seq, at, kind, canonical(data), previous, event_hash))

    def create(self, plan, request_id: str) -> str:
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("request_id must contain 1 to 128 characters")
        plan = validate_plan(plan)
        plan_hash = digest({"plan": plan, "versions": VERSIONS})
        with self.tx(write=True) as db:
            existing = db.execute("SELECT id,plan_hash FROM runs WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                if existing["plan_hash"] != plan_hash:
                    raise Conflict("request_id is already bound to a different plan")
                return existing["id"]
            run_id, now = uuid.uuid4().hex, self.clock()
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)", (run_id, request_id, plan["name"], "queued", canonical(plan), plan_hash, canonical(VERSIONS), now, now))
            for ordinal, step in enumerate(plan["steps"]):
                db.execute("INSERT INTO steps(run_id,ordinal,id,tool,args,state) VALUES (?,?,?,?,?,'pending')", (run_id, ordinal, step["id"], step["tool"], canonical(step["args"])))
            self._event(db, run_id, "run_created", {"plan_hash": plan_hash, "step_count": len(plan["steps"])})
            return run_id

    @staticmethod
    def _run(db, run_id):
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise LookupError("run not found")
        return row

    def list_runs(self):
        with self.tx() as db:
            return [dict(row) for row in db.execute("SELECT id,name,state,created,updated FROM runs ORDER BY created DESC,id LIMIT 100")]

    def get(self, run_id):
        with self.tx() as db:
            run = dict(self._run(db, run_id))
            run["plan"], run["versions"] = json.loads(run["plan"]), json.loads(run["versions"])
            steps = [dict(row) for row in db.execute("SELECT * FROM steps WHERE run_id=? ORDER BY ordinal", (run_id,))]
            for step in steps:
                for field in ("args", "inputs", "output"):
                    step[field] = json.loads(step[field]) if step[field] is not None else None
            events = [dict(row) for row in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY seq", (run_id,))]
            for event in events:
                event["data"] = json.loads(event["data"])
            run.update(steps=steps, events=events)
            run["journal_valid"] = verify_events(run_id, events)
            return run

    def _set_state(self, db, run_id, state):
        db.execute("UPDATE runs SET state=?,updated=? WHERE id=?", (state, self.clock(), run_id))

    def claim(self, run_id, owner: str, *, lease_seconds=10, max_attempts=3):
        if not isinstance(owner, str) or not owner or len(owner) > 100:
            raise ValueError("invalid worker identity")
        if not 0 < lease_seconds <= 300 or type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise ValueError("invalid lease or attempt limit")
        with self.tx(write=True) as db:
            run = self._run(db, run_id)
            if run["state"] in TERMINAL:
                return None
            if json.loads(run["versions"]) != VERSIONS:
                raise Conflict("tool versions changed; resume requires the recorded versions")
            step = db.execute("SELECT * FROM steps WHERE run_id=? AND state!='succeeded' ORDER BY ordinal LIMIT 1", (run_id,)).fetchone()
            if step is None:
                return None
            now = self.clock()
            if step["state"] == "waiting" or step["ready_at"] > now:
                return None
            if step["state"] == "running" and step["lease_until"] > now:
                return None
            if step["attempt"] >= max_attempts:
                db.execute("UPDATE steps SET state='failed',error='attempts_exhausted' WHERE run_id=? AND id=?", (run_id, step["id"]))
                self._set_state(db, run_id, "failed")
                self._event(db, run_id, "step_failed", {"step": step["id"], "reason": "attempts_exhausted"})
                return None
            outputs = {r["id"]: json.loads(r["output"]) for r in db.execute("SELECT id,output FROM steps WHERE run_id=? AND state='succeeded'", (run_id,))}
            inputs = {key: outputs[value["$step"]] if isinstance(value, dict) and "$step" in value else value for key, value in json.loads(step["args"]).items()}
            approval_hash = digest({"plan_hash": run["plan_hash"], "step": step["id"], "inputs": inputs, "tool_version": VERSIONS[step["tool"]]})
            if step["tool"] in APPROVAL_TOOLS and not (step["approval_state"] == "approved" and step["approval_hash"] == approval_hash):
                db.execute("UPDATE steps SET state='waiting',inputs=?,approval_hash=?,approval_state='pending' WHERE run_id=? AND id=?", (canonical(inputs), approval_hash, run_id, step["id"]))
                self._set_state(db, run_id, "waiting")
                self._event(db, run_id, "approval_requested", {"step": step["id"], "approval_hash": approval_hash, "tool": step["tool"]})
                return None
            epoch = step["epoch"] + 1
            if step["state"] == "running":
                self._event(db, run_id, "lease_reclaimed", {"step": step["id"], "previous_epoch": step["epoch"], "new_epoch": epoch})
            db.execute("UPDATE steps SET state='running',attempt=attempt+1,epoch=?,owner=?,lease_until=?,inputs=?,error=NULL WHERE run_id=? AND id=?", (epoch, owner, now + lease_seconds, canonical(inputs), run_id, step["id"]))
            self._set_state(db, run_id, "running")
            self._event(db, run_id, "step_started", {"step": step["id"], "tool": step["tool"], "worker": owner, "epoch": epoch, "attempt": step["attempt"] + 1, "input_hash": digest(inputs)})
            return {"run_id": run_id, "step_id": step["id"], "tool": step["tool"], "inputs": inputs, "epoch": epoch, "owner": owner, "attempt": step["attempt"] + 1, "lease_until": now + lease_seconds, "effect_key": f"{run_id}:{step['id']}"}

    def _owned(self, db, claim):
        step = db.execute("SELECT * FROM steps WHERE run_id=? AND id=?", (claim["run_id"], claim["step_id"])).fetchone()
        if step is None or step["state"] != "running" or step["epoch"] != claim["epoch"] or step["owner"] != claim["owner"] or step["lease_until"] <= self.clock():
            raise StaleLease("worker no longer owns a live lease")
        return step

    def renew(self, claim, *, lease_seconds=10):
        if not 0 < lease_seconds <= 300:
            raise ValueError("invalid lease duration")
        with self.tx(write=True) as db:
            self._owned(db, claim)
            until = self.clock() + lease_seconds
            db.execute("UPDATE steps SET lease_until=? WHERE run_id=? AND id=?", (until, claim["run_id"], claim["step_id"]))
            self._event(db, claim["run_id"], "lease_renewed", {"step": claim["step_id"], "epoch": claim["epoch"], "lease_until": until})
            return until

    def complete(self, claim, output):
        encoded = canonical(output)
        if len(encoded.encode()) > 128_000:
            raise ValueError("output exceeds 128 KB")
        with self.tx(write=True) as db:
            self._owned(db, claim)
            db.execute("UPDATE steps SET state='succeeded',output=?,owner=NULL,lease_until=NULL WHERE run_id=? AND id=?", (encoded, claim["run_id"], claim["step_id"]))
            self._event(db, claim["run_id"], "step_completed", {"step": claim["step_id"], "epoch": claim["epoch"], "output_hash": digest(output)})
            remaining = db.execute("SELECT COUNT(*) FROM steps WHERE run_id=? AND state!='succeeded'", (claim["run_id"],)).fetchone()[0]
            self._set_state(db, claim["run_id"], "queued" if remaining else "succeeded")
            if not remaining:
                self._event(db, claim["run_id"], "run_completed", {})

    def fail(self, claim, *, retryable: bool, max_attempts=3, delay=0.25):
        with self.tx(write=True) as db:
            step = self._owned(db, claim)
            retry = retryable and step["attempt"] < max_attempts
            state = "pending" if retry else "failed"
            ready = self.clock() + min(delay * 2 ** (step["attempt"] - 1), 30)
            db.execute("UPDATE steps SET state=?,error=?,ready_at=?,owner=NULL,lease_until=NULL WHERE run_id=? AND id=?", (state, "transient_tool_error" if retryable else "tool_error", ready, claim["run_id"], claim["step_id"]))
            self._set_state(db, claim["run_id"], "queued" if retry else "failed")
            self._event(db, claim["run_id"], "retry_scheduled" if retry else "step_failed", {"step": claim["step_id"], "attempt": step["attempt"], "ready_at": ready, "reason": "transient_tool_error" if retryable else "tool_error"})

    def decide(self, run_id, step_id, approval_hash, *, approve: bool, actor="local-operator"):
        if type(approve) is not bool or not isinstance(actor, str) or not 1 <= len(actor) <= 100:
            raise ValueError("invalid approval decision")
        decision = "approved" if approve else "rejected"
        with self.tx(write=True) as db:
            run = self._run(db, run_id)
            step = db.execute("SELECT * FROM steps WHERE run_id=? AND id=?", (run_id, step_id)).fetchone()
            if not step or not step["approval_hash"] or step["approval_hash"] != approval_hash:
                raise Conflict("approval does not match the proposed action")
            if step["approval_state"] == decision:
                return
            if run["state"] in TERMINAL or step["state"] != "waiting":
                raise Conflict("action is not awaiting approval")
            db.execute("UPDATE steps SET state=?,approval_state=? WHERE run_id=? AND id=?", ("pending" if approve else "failed", decision, run_id, step_id))
            self._set_state(db, run_id, "queued" if approve else "failed")
            self._event(db, run_id, f"approval_{decision}", {"step": step_id, "actor": actor, "approval_hash": approval_hash})

    def cancel(self, run_id):
        with self.tx(write=True) as db:
            run = self._run(db, run_id)
            if run["state"] in TERMINAL:
                return
            db.execute("UPDATE steps SET state='cancelled',epoch=epoch+1 WHERE run_id=? AND state!='succeeded'", (run_id,))
            self._set_state(db, run_id, "cancelled")
            self._event(db, run_id, "run_cancelled", {})


def verify_events(run_id, events):
    previous = "0" * 64
    for seq, event in enumerate(events, 1):
        expected = {key: event[key] for key in ("run_id", "seq", "at", "kind", "data", "previous_hash")}
        if event["run_id"] != run_id or event["seq"] != seq or event["previous_hash"] != previous or event["hash"] != digest(expected):
            return False
        previous = event["hash"]
    return bool(events)
