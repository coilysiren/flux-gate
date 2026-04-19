# TODO

Bounded follow-ups, ordered top (highest signal) to bottom (most speculative). Anything here is contingent on the constraint in [SCOPE.md](SCOPE.md): the public surface is frozen, internals are free. If a TODO would expand the public surface (new MCP tool, new subagent, new Weapon field), it needs an explicit reason that overrides SCOPE.md.

## Drone path-template generalization

The Drone has a hardcoded `if request.method == "POST" and request.path == "/tasks" and "id" in response.body: context["task_id"] = response.body["id"]`. That's a demo-API leftover masquerading as path templating — for any non-`/tasks` API, the `{task_id}` substitution is broken.

Real fix: add an optional `extract: dict[str, str]` field to `PlanStep` that declares which response field(s) to capture into the path-template context. Drone applies generically:

```python
{
  "user": "userA",
  "request": {"method": "POST", "path": "/orders"},
  "extract": {"order_id": "id"}   # capture response.body["id"] as {order_id}
}
```

Backward compatible — existing plans that rely on the `/tasks` special case keep working until they're rewritten. Internal change; no new MCP tool. Plan model gets one new optional field. ~50 lines.

This is a real correctness bug, not a speculative improvement. Filed at the top because it's the only entry that's actually broken today.

## Richer `ExecutionResult` population

We capture `status_code` and `body`. We're throwing away response time, response size, response headers (Server, X-Powered-By, Content-Type, Set-Cookie, security headers), and connection state (timeout vs reset vs clean close). The Inspector subagent's analysis quality is bounded by what we record.

Concretely, extend `HttpApi.send` and `ExecutionStepResult` to capture:

- `duration_ms`: float, time from request send to response received
- `response_size_bytes`: int, raw body size
- `response_headers`: `dict[str, str]`, filtered to interesting keys (the host can ignore what it doesn't care about; we keep the surface to a stable subset)
- `outcome`: literal `"ok" | "timeout" | "connection_reset" | "dns_failure"` etc., to disambiguate "no response" cases that currently surface as exceptions

The Inspector then has signal for: side-channel timing leaks, info-disclosure via headers, suspicious response-size patterns, infrastructure flakiness. Internal change — `ExecutionStepResult` gains optional fields, no MCP tool changes. ~150 lines.

## Wire up `ReplayBundle` so attack patterns are deterministically reproducible

`Finding` carries an optional `replay_bundle: ReplayBundle | None` field, but nothing populates it today. The model exists; the wiring doesn't. Attack patterns are supposed to be reproducible deterministic steps — that's the difference between Gauntlet's findings and a manual bug report. Without a populated `ReplayBundle`, the only reproduction path is for a human to read `evidence` + `reproduction_steps` (free-form English) and re-derive the request sequence by hand.

Concretely:

- The Inspector subagent should populate `replay_bundle` on every `Finding` it emits, copying the `ReplayStep`s from the `ExecutionResult.steps` that produced the finding.
- Update [`agents/gauntlet-inspector.md`](agents/gauntlet-inspector.md) to mandate it (currently silent on the field).
- Schema-level enforcement at the buffer boundary, similar to the `violated_blocker is None` check, would be ideal — `record_iteration` could reject findings without a `replay_bundle`. Risk: this adds a hard requirement that may surprise consumers; consider a softer first pass (warning, not rejection) until the discipline is bedded in.
- Add a `replay_finding(finding_id_or_run_id_pair, url, user_headers)` MCP tool that takes a stored finding and re-executes its `ReplayBundle` against the SUT. Useful for "did the fix actually work" loops. **This adds a public MCP tool — coordinate with SCOPE.md before shipping.**

Open questions:

- Path-template handling: `ReplayBundle.steps` carry raw `HttpRequest`s. Dynamic IDs (`{task_id}` resolved from a prior POST response) need either to be re-resolved at replay time or baked in at capture time. The current Drone resolves them at run time; the replay bundle should probably do the same. (Depends on the path-template generalization above.)
- What identifies a finding for replay? `(weapon_id, issue, run_id)`? A separate finding id?

## Risk-report intelligence in `loop.py`

`_confidence_score` is honestly ~30 lines of weighted math, and the rest of `build_risk_report` is sorting. This is supposed to be where the deterministic "intelligence" of Gauntlet lives — and it's nearly empty.

Concrete additions, each independently shippable:

- **Failure clustering**: 8 findings on the same endpoint are 1 issue, not 8. Group by `(endpoint, method, severity)` + dedupe representative finding. Surface cluster size in the report so the host can prioritize.
- **Coverage gap analysis**: track which response status codes appeared and didn't. A weapon that only ever saw 200/403 has weaker signal than one that saw 200/400/403/500. Add a `coverage_gaps: list[str]` to `RiskReport`.
- **Same-response-fingerprint detection**: hash response status + body shape + size. Two distinct attack patterns producing identical responses is signal — either both miss the same gap, or there's a common vulnerable code path. Surface the fingerprint clusters.
- **Statistical anomalies**: response-time drift (a request that took 10× the median for that endpoint), suspiciously templated bodies (every 500 returns the same string), status-code distributions that look hand-coded. Pure Python on the recorded `ExecutionResult`s; no LLM in the loop.

Each piece is ~50-150 lines. Together: ~400-500 lines. All internal — `RiskReport` gains optional fields, no MCP tool changes. Existing fields stay populated the same way.

## Buffer robustness

Today `record_iteration` does a plain `open(..., "a")`. Concurrent subagent processes (a real possibility — that's the whole point of JSONL over a single JSON file) can interleave bytes if both writes happen mid-flush. Latent bug.

Fixes:

- Atomic write-and-rename: write each new line to a temp file, rename into place. Slower per write but correct under concurrency. Or: file-locking via `fcntl.flock`. Or: a single process-local lock that serializes writes (cheap, but breaks the concurrent-subagent promise).
- Per-line validation on read: if a JSONL line is corrupt, `model_validate_json` throws and the whole `read_iteration_records` fails. Skip-with-warning instead, plus a counter the host can read for "how many corrupt records were skipped."
- Schema versioning: add `schema_version: int = 1` to the manifest and to each record file's first line. Old buffers survive model changes via a migration step on read.

Boring but underpins a promise the architecture already makes. ~100 lines.

## In-flight structured logging

Gauntlet emits no observability today — no per-tool latency, no per-run timings, no error counts. The host has to wrap MCP calls itself if it wants any of that.

First pass: structured logs to stderr via Python's `logging` module with a JSON formatter. The host pipes stderr wherever it wants (terminal, file, log aggregator). One log line per MCP tool call, with `tool`, `run_id`, `weapon_id`, `duration_ms`, and `status` fields. Errors include the exception type and message.

Specifically NOT in scope for the first pass:

- A summary file written at end-of-run. Add this only if a consumer asks for it.
- A separate per-call timings JSONL alongside the buffers. Same reason.
- OpenTelemetry / tracing. The host owns its own observability stack; Gauntlet shouldn't pick a vendor.

The first pass is "stderr lines a human or `jq` can read." Anything beyond that needs a real consumer asking for it.

## Richer assertion expressiveness

Today `Assertion.expected` is a single value compared with `==`. The Attacker can't express "any 4xx is fine" or "anything except 200" without writing multiple plans.

Without re-introducing the `rule` kind (which we deliberately deleted), `expected` could carry richer matchers:

- `expected: [403, 404]` — any-of
- `expected: {"min": 400, "max": 499}` — range
- `expected: {"not": 200}` — negation
- `expected: {"in": [403, 404]}` — explicit any-of with the same shape as range

All still status-code based, all internal. The Plan model's `expected: Any | None` field already accepts these shapes; the Drone needs the matcher logic. ~80 lines + a small expansion of the assertion-evaluator. The Attacker subagent's prose in [`agents/gauntlet-attacker.md`](agents/gauntlet-attacker.md) gets a section on the matcher options.

## Property-based tests for `_confidence_score` and `aggregate_final_clearance`

The math in `loop.py` is whatever ad-hoc cases I wrote when seeding the tests. There's no test today that says "the math is internally consistent."

Useful invariants to assert via Hypothesis or similar:

- `_confidence_score`: monotonicity — adding a passing iteration never decreases the score. Boundedness — output stays in [0, 1] for any input.
- `aggregate_final_clearance`: any per-weapon `high` ⟹ overall `block`. Monotonicity — removing a per-weapon report never makes overall confidence worse. The `pass` recommendation requires every per-weapon clearance pass.
- `build_risk_report`: empty input ⟹ deterministic empty-shaped output, never crashes. `confirmed_failures` is always sorted and deduped.

~150 lines. Pure quality investment, no behavior change.

## Holdout plan plausibility checks

The HoldoutEvaluator subagent composes plans from blockers in-prompt. Currently nothing in the Python validates that the holdout plan actually tests the blocker — the LLM could write a plan that doesn't test what it should.

A Python-level check that flags obvious mismatches:

- Blocker mentions cross-user behavior ("non-owner is rejected") but the plan has only one `user` across all steps.
- Blocker mentions a status code (e.g. "403") but no assertion in the plan checks for it.
- Blocker mentions a method (e.g. "DELETE") but no step in the plan uses that method.

Heuristic — false positives expected, false negatives certain. Returned as warnings on `record_holdout_result`, not errors; the host decides whether to surface them. ~100 lines.

Marginal value, but cheap. Below the others because the LLM is usually capable of writing reasonable holdout plans, and the heuristics could become noise.

## Across-iteration plan mutation

Today the Attacker subagent re-derives plans from scratch each iteration by re-prompting against the iteration buffer. A deterministic Python mutator could take a plan that landed in iteration N and produce variants for iteration N+1 (drop a field, swap users, change expected status). Wins: determinism, no LLM tokens for the mutation step. Losses: re-introduces a Python "intelligence" layer that competes with the Attacker subagent, for a marginal token saving since each iteration only generates 2-4 plans.

**Defer until at least one production loop has battle-tested the in-prompt approach.** If the Attacker subagent's regenerate-from-scratch loop turns out to under-explore (the same baseline plan keeps getting repeated, edge cases never surface), revisit. Until then, don't.

If revisited:

- Mutator reads only what the Attacker has already seen (`read_iteration_records`); no train/test split risk.
- Lives behind a new MCP tool the Attacker can call, not as a hidden replacement for in-prompt generation. **New MCP tool — coordinate with SCOPE.md.**
- Stays within a single run.

## Cross-run failure correlation

Each Gauntlet run is fully ephemeral today — `.gauntlet/runs/<run_id>/` is wiped between runs in practice, and there's no cross-run aggregator. If a project re-runs Gauntlet across days/weeks of iterations, knowing "this same `confirmed_failure` showed up in 3 of the last 5 runs" is genuinely useful signal.

Out of scope until a real consumer asks for it. Shape would be a re-introduced `FindingsStore` keyed by `weapon_id`, plus an MCP tool like `recurring_failures(weapon_id, lookback=5)`. Both were deleted; both could come back if the use case materializes.

**New MCP tool + reverses an explicit cut. Coordinate with SCOPE.md.**
