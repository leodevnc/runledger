# Failure and recovery guide

## Report exists but the run is still running

Use the crash-after-report button after approval. The report service has committed a receipt,
while `publish` remains running with no output checkpoint. Wait for the two-second demo lease and
advance again. Look for `lease_reclaimed`, a second `step_started` with a higher epoch, and a final
`step_completed`. The receipt count must remain one.

If there are two report rows, the downstream idempotency contract is broken. If the old worker can
checkpoint after takeover, ownership fencing is broken. Tests assert these properties separately.

## A transient failure keeps retrying

Inject a transient failure while a step is ready. The first delay is 250 ms, then 500 ms; three
failed attempts make the run terminal. A tool exception's raw text is not stored. Stable reason
codes keep the journal useful without inadvertently persisting arbitrary exception contents.

## An approved action asks for approval again

Compare the proposal digests and resolved inputs. The action may have changed. Approval is bound
to exact arguments and a tool version, not only to the tool name. Never silently copy an old
approval onto a new payload.

## Replaying shows a divergence

Use the changed-policy comparison on INC-1042. Its 12% error rate is above the recorded 5% threshold
but below the candidate 20% threshold. The assessment changes from high severity to low severity.
The report row remains the recorded receipt. No replacement report is published.

## Cancelled run has a report receipt

Cancellation is not a rollback of the world. A report may have committed just before cancellation.
Inspect both the execution journal and independent receipt; decide on compensating action outside
this prototype. Runledger does not fabricate a successful cancellation of an already issued effect.

## Restart the console

Stop and restart `python -m runledger serve` from the same directory. Both database files live in
`.runledger/`. Existing runs should reappear. If a worker vanished while holding a lease, it can be
replaced after expiry. Keep both the workflow and report databases when preserving a demo session.
