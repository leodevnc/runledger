# Learning notes

The most useful question was not whether a workflow could resume, but what had already happened
outside the workflow database when it resumed. Separating the report store exposed a real ambiguity:
an absent checkpoint did not mean the action had not taken place.

Ownership and idempotency solve different parts of that problem. A fresh epoch prevents stale
workers from changing workflow state. A stable effect key prevents the external service from
creating another report when the new worker repeats the action. The tests deliberately exercise
both paths rather than calling the whole system exactly-once.

The approval boundary needed resolved arguments. A decision tied only to a plan step could outlive
a change to its input. The payload-bound digest made that mismatch observable and led to a test
that changes an upstream output and verifies that approval is requested again.

The replay feature forced a precise definition. Recomputing each pure tool against its recorded
inputs answers where an implementation changed. It does not answer how an entirely new execution
would unfold. The UI and exported report retain that distinction.

Cancellation exposed another limit: the server can reject future checkpoints, but cannot retract
a separately committed report. A dedicated boundary test preserves this uncomfortable outcome
instead of representing cancellation as stronger than it is.

The measured failure matrix validates local execution invariants across synthetic scenarios. It
does not measure an agent's task success, reasoning quality, or behavior against a real service.
Those require additional integrations and independent datasets.
