# Roadmap

## Implemented: inspectable failure recovery

- Durable submitted plans, per-step outputs, and a transactional event journal
- Lease ownership, stale-worker fencing, bounded retries, and cancellation
- Payload-bound human approvals
- Separately committed report sink with idempotent receipts
- Replay comparison that skips side effects
- Operator console, trace export, CLI, and synthetic failure matrix
- Real subprocess crash and browser interaction tests

## Next milestone: independent worker process

Move tool execution behind a supervised process boundary with automatic heartbeats and hard
execution deadlines. Keep the operator API responsive during a slow tool. Test process termination
before, during, and after external effects, and record scheduler/worker latency independently.

## Subsequent experiments

- Add a real planner adapter and reviewed task fixtures, clearly separating model quality from
  execution reliability. Keep all external calls opt-in.
- Port claims and checkpoints to PostgreSQL; compare contention and throughput on a measured load.
- Add tenant-aware authorization to run lookup, approval, export, and tool dispatch.
- Emit OpenTelemetry spans linked to run, step, attempt, and effect receipts.
- Define retention, migration, and backup policies for workflow and effect stores together.
- Add authenticated external journal anchoring if audit requirements justify it.
