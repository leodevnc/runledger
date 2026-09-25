"""A worker can disappear after a tool commits but before its checkpoint commits."""
import uuid

from .model import StaleLease
from .tools import pure_tool


class InjectedCrash(BaseException):
    """Deliberately bypass normal error handling, leaving the lease to expire."""


class TransientFailure(Exception):
    pass


class Worker:
    def __init__(self, store, sink, *, owner=None, lease_seconds=10):
        self.store, self.sink = store, sink
        self.owner = owner or "worker-" + uuid.uuid4().hex[:8]
        self.lease_seconds = lease_seconds

    def tick(self, run_id, *, fault=None, after_effect=None):
        if fault not in (None, "transient", "after_effect"):
            raise ValueError("unknown fault injection")
        claim = self.store.claim(run_id, self.owner, lease_seconds=self.lease_seconds)
        if claim is None:
            return {"outcome": "idle", "state": self.store.get(run_id)["state"]}
        try:
            if fault == "transient":
                raise TransientFailure()
            if claim["tool"] == "publish_report":
                output = self.sink.publish(claim["effect_key"], claim["inputs"]["assessment"])
                if after_effect:
                    after_effect()
                if fault == "after_effect":
                    raise InjectedCrash()
            else:
                output = pure_tool(claim["tool"], claim["inputs"])
            self.store.complete(claim, output)
            return {"outcome": "completed", "step": claim["step_id"], "epoch": claim["epoch"]}
        except TransientFailure:
            self.store.fail(claim, retryable=True)
            return {"outcome": "retry_scheduled", "step": claim["step_id"]}
        except StaleLease:
            return {"outcome": "stale_worker_rejected", "step": claim["step_id"]}
        except Exception:
            # Store a stable error code, never arbitrary tool exception text.
            try:
                self.store.fail(claim, retryable=False)
            except StaleLease:
                return {"outcome": "stale_worker_rejected", "step": claim["step_id"]}
            return {"outcome": "failed", "step": claim["step_id"]}
