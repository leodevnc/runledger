# Design decisions

## 001 — Make the ambiguous effect window visible

Putting the report and checkpoint in one database transaction would make the example easier but
erase the failure we want to study. The report sink therefore uses a separate database. A named
failpoint and a real subprocess-exit test stop execution between the two commits.

## 002 — Epochs for ownership, keys for effects

Lease expiry alone cannot stop a paused worker from returning later. Epoch checks fence its
checkpoint. Fencing a checkpoint does not prevent duplicate external requests, so the downstream
sink separately checks a stable idempotency key and payload digest. Neither mechanism replaces the other.

## 003 — Bind approval to resolved arguments

An approval for a generic tool name is too broad. The digest includes what the tool will actually
receive after references are resolved, plus the plan and version. Any material change needs another
decision. The demo makes the digest inspectable beside the payload.

## 004 — Explicit plans before model integration

This experiment studies execution semantics. Its three tools use synthetic data and deterministic
logic, keeping injected failures reproducible. The interface accepts explicit plans rather than
pretending the deterministic fixture is a reasoning model. Future planner adapters must preserve
server-owned tool schemas, approval policy, and execution identity.

## 005 — Replay pure operations only

Replaying a report write to compare an assessment would create a real duplicate risk. The replay
engine has no sink reference and cannot call it. Effect rows show recorded receipts. Changed-policy
comparison reuses recorded inputs, so it must not be described as a full counterfactual workflow.

## 006 — SQLite and a small HTTP surface

One-host operation makes transactions and crash boundaries inspectable without managed services.
The cost is writer serialization and no distributed failover. The standard-library HTTP server is
for local demonstration, not internet serving. There is no production-scale claim.

## 007 — Preserve evidence without claiming tamper-proof storage

Hash linkage catches accidental edits in retained events and makes export review straightforward.
An administrator can rewrite the database and recompute hashes. External anchors and signatures
would be needed for a stronger audit requirement.
