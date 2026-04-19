# TODO

## Wire up `ReplayBundle` so attack patterns are deterministically reproducible

`Finding` carries an optional `replay_bundle: ReplayBundle | None` field, but nothing populates it today. The model exists; the wiring doesn't.

Attack patterns are supposed to be reproducible deterministic steps — that's the difference between Gauntlet's findings and a manual bug report. Without a populated `ReplayBundle`, the only reproduction path is for a human to read `evidence` + `reproduction_steps` (free-form English) and manually re-derive the request sequence. That defeats the point.

What needs to happen:

- The Inspector subagent should populate `replay_bundle` on every `Finding` it emits, copying the `ReplayStep`s from the `ExecutionResult.steps` that produced the finding.
- The Inspector's [`SKILL`-style prose](agents/gauntlet-inspector.md) should mandate it (currently silent on the field).
- A schema-level enforcement at the buffer boundary, similar to the `violated_blocker is None` check, would be ideal — `record_iteration` could reject findings without a `replay_bundle`. Risk: this adds a hard requirement that may surprise consumers; consider a softer first pass (warning, not rejection) until the discipline is bedded in.
- Once populated, add a `replay_finding(finding_id_or_run_id_pair, url, user_headers)` MCP tool that takes a stored finding and re-executes its `ReplayBundle` against the SUT — useful for "did the fix actually work" loops.

Open questions:

- Path-template handling: `ReplayBundle.steps` carry raw `HttpRequest`s. Dynamic IDs (`{task_id}` resolved from a prior POST response) need either to be re-resolved at replay time or baked in at capture time. The current Drone resolves them at run time; the replay bundle should probably do the same.
- What identifies a finding for replay? `(weapon_id, issue, run_id)`? A separate finding id?
