# Testing and failure experiments

## Local runtime

- Python 3.11.15 / SQLite 3.37.2
- Node.js 22.23.2 / Playwright 1.63.0 / Chromium 153
- Linux under WSL; standard-library Python runtime; no external model or service calls

## Unit, integration, and real process tests

```bash
python3 -m unittest discover -s tests -v
```

Tests cover one-owner contention, lease renewal and expiration, stale completion rejection,
bounded retries, cancellation, payload-bound approval, tool allowlisting, version drift,
concurrent duplicate report requests, event mutation detection, and HTTP origin/host restrictions.

A subprocess exits with status 23 immediately after committing a report. A replacement worker
recovers the same run after lease expiration, and the report count remains one. Another test makes
journal append fail after a state mutation and verifies that the entire checkpoint transaction
rolls back. A cancellation test confirms that an already committed external effect is not undone.

## Browser interaction

```bash
npm ci
npx playwright install chromium
npm run test:e2e
```

The browser creates a run, advances tools, approves the exact action, injects worker loss after
the report write, waits for the lease, recovers, and compares a changed replay policy. It verifies
three checkpoints, four tool attempts, one report, a lease-reclaimed event, and zero replay effects.
A rejection test checks that no report is published; layout checks cover keyboard entry and narrow viewports.

The browser suite generates desktop and mobile screenshots, checks document overflow, and captures
JavaScript page errors. The journal keeps horizontal scrolling for dense rows. Linux hosts need
Chromium system libraries; Playwright's `--with-deps` installer requires suitable host permissions.

## Repeated failure matrix

```bash
python -m runledger.evaluate --repeats 10
```

[Raw results](evaluation-results.json): 50 runs, 50 expected outcomes, zero duplicate effects.
Each of five scenarios is repeated ten times: healthy, post-effect worker loss, transient failure,
approval rejection, and cancellation. Lease timing is controlled by an injected clock. Local
median scenario duration was 120.49 ms and maximum 359.65 ms, including SQLite operations.
These timings are observations from this machine, not production throughput or latency targets.

## Packaging

Build a wheel with `python -m pip wheel --no-build-isolation --no-deps .`.
Static assets are included in package data. The source checkout requires no Python
dependency installation to run the CLI, server, or Python tests.

## Limits

These tests establish the modeled execution invariants. They do not test real LLM quality, real
remote APIs, power loss, multi-host clocks, authenticated users, sustained traffic, or a sandbox for
arbitrary tool code. A hash-valid journal is not cryptographic proof against a malicious DB owner.

The workflow under `ci/` is a template, not an active hosted check. No GitHub Actions pass is claimed.
