---
name: gauntlet
description: Adversarial API inspection via the Gauntlet MCP server. Use this skill when the user wants to stress-test a running HTTP API under attack, validate authorization/ownership/input invariants before promoting code, or run Gauntlet's two-role adversarial loop against a SUT. Triggers include "run gauntlet", "adversarial test", "check before merging", "attack this API", "run the hardening loop".
---

# Gauntlet

Gauntlet is an adversarial API inspection loop. **You** (the host agent) play both the Attacker and the Inspector roles. The Gauntlet MCP server handles the deterministic pieces: config loading, plan execution against the SUT, weapon assessment, and risk-report assembly.

Gauntlet's novelty is the train/test split: each Weapon has a `description` (the attack surface, shown to the Attacker) and `blockers` (the expected invariants, withheld from the Attacker and checked only by the HoldoutEvaluator). Violating the split is the single most common way to invalidate a run.

## Train/test split (read this first)

| Role context you're in | Safe to read | Forbidden to read |
|---|---|---|
| **Attacker** | `list_weapons` briefs, `list_targets`, own prior `Finding`s, own prior `ExecutionResult`s | `get_weapon` output, any `blockers` text, any `holdout_result` |
| **Inspector** | `ExecutionResult`s, own prior `Finding`s | `get_weapon` output, any `blockers` text, any `holdout_result` |
| **HoldoutEvaluator** | `get_weapon` output including `blockers`, `holdout_result`s | Attacker's plans / Inspector's findings (avoid carryover) |
| **Orchestrator** | Everything except never reads and paraphrases `blockers` back to the Attacker | — |

When you transition between role contexts, **do not carry content over verbatim**. If you must summarize something across roles, strip blocker text. The Attacker never sees a blocker, not even a paraphrase of one.

## Prerequisites

- The Gauntlet MCP server is registered (`claude mcp add gauntlet -- uv run gauntlet-mcp`). Confirm with `/mcp`.
- The project has a `.gauntlet/` directory with at least one weapon YAML. If missing, tell the user and stop.
- A running SUT whose URL the host can reach. If the user hasn't named a URL, ask.
- Existing tests pass. Gauntlet is the final check, not a first-pass linter.

## The loop

### Step 1 — Orchestrator: pick and preflight

1. `list_weapons(weapons_path, arsenal_path)` → pick one by `id`. If the user named a weapon, use it. If not, present the list and ask.
2. `list_targets(targets_path, openapi_path)` → pick a matching target (optional; a weapon can run without one).
3. `assess_weapon(weapon_id, target)` → if `proceed=False`, print the issues and stop. Don't attempt to fix the weapon yourself.
4. `default_iteration_specs()` → the reference 4-stage ladder (baseline → boundary → adversarial_misuse → targeted_escalation). Use verbatim unless the user has said otherwise.

Initialize an empty `iterations: list[IterationRecord]` buffer.

### Step 2 — Attacker + Inspector: iterate (typically 4 times)

For each `IterationSpec`:

**Attacker sub-step** (read only the `WeaponBrief`, the iteration spec, and prior iteration records):

Compose 2-4 `Plan`s probing the weapon surface. Vary categories across plans (`authz`, `crud`, `boundary`, `lifecycle`). Each `Plan` is:

```python
{
  "name": "snake_case_identifier",
  "category": "authz|crud|boundary|lifecycle",
  "goal": "one-sentence description of what this plan tests",
  "steps": [
    {"user": "userA", "request": {"method": "POST", "path": "/tasks", "body": {"title": "..."}}},
    {"user": "userB", "request": {"method": "PATCH", "path": "/tasks/{task_id}", "body": {...}}},
    {"user": "userA", "request": {"method": "GET",   "path": "/tasks/{task_id}"}},
  ],
  "assertions": [
    {"name": "...", "kind": "status_code", "expected": 403, "step_index": 2},
    {"name": "...", "kind": "rule", "rule": "task_not_modified_by_other_user", "step_index": 3},
  ],
}
```

Conventions:
- `{task_id}` is a path template resolved from the `id` field of the first `POST /tasks` response.
- `step_index` is 1-based.
- Assertion `kind: status_code` requires an integer `expected` and `null` for `rule`; `kind: rule` requires a `rule` name and `null` for `expected`.
- Prefer variety over repetition. Later iterations should target suspicious areas surfaced by earlier findings, not rehash them.

**Drone sub-step**:

For each plan: `execute_plan(url, plan, users_path)` → `ExecutionResult`. Collect them.

**Inspector sub-step** (read the `ExecutionResult`s + prior findings; **never** `blockers`):

Produce `Finding`s for real issues. Each:

```python
{
  "issue": "snake_case_identifier",
  "severity": "low|medium|high",             # high = auth bypass, privilege escalation, data corruption; medium = info leak; low = minor
  "confidence": 0.0-1.0,
  "rationale": "why this is a problem",
  "evidence": [{"kind": "request|response|assertion|note", "content": "specific observation"}],
  "reproduction_steps": ["Step 1: POST /tasks as userA ...", "Step 2: PATCH /tasks/{id} as userB — expect 403, got 200"],
  "traces": [...],                          # copy from ExecutionResult.steps
  "violated_blocker": null,                 # you don't know the blocker; leave null
  "next_targets": ["ownership mutation", "list visibility", ...],
  "is_anomaly": false,                       # set true for suspicious but non-violating observations
  "plan_name": "..."
}
```

Treat suspicious-but-not-violating observations as anomalies (`is_anomaly=True`, severity `low`). They show up separately in the risk report and inform future weapon refinement.

**Append** one `IterationRecord(spec, plans, execution_results, findings)` to the buffer.

Optional fail-fast: if a `high` finding appears, you may stop after the current iteration. Note this in the summary.

### Step 3 — HoldoutEvaluator: execute the withheld vitals

Start a fresh internal context. The Attacker/Inspector traces above inform nothing here.

1. `get_weapon(weapon_id)` → full `Weapon` including `blockers`.
2. For each blocker, construct **one structured `Plan`** that tests it. Typical patterns:
   - "A PATCH by a non-owner is rejected with 403" → 3 steps: owner POSTs, non-owner PATCHes (assert status 403), owner GETs (assert rule `task_not_modified_by_other_user`).
   - "A write by a non-owner is rejected with 403 or 404" → same shape, accept either status code.
   - "GET by a non-owner returns 403 or 404" → POST as owner, GET as non-owner, assert status.
3. `execute_plan(url, plan, users_path)` for each → collect `ExecutionResult`s. This is the `holdout_results` list.

### Step 4 — Orchestrator: assemble the report

`assemble_run_report(iterations=iterations, holdout_results=holdout_results, clearance_threshold=0.9)` → `{risk_report, clearance}`.

Show the user:
- `risk_level` (low | medium | high)
- `confirmed_failures` (the list of violated invariants — safe to show, these are outcomes, not blocker text)
- `clearance.recommendation` (pass | conditional | block) if present
- `confidence_score` and `coverage` summary

## Acting on results

| `risk_level` | Action |
|---|---|
| `low` | Safe to promote or merge. |
| `medium` | Attempt fixes and re-run the loop. |
| `high` | **Stop.** Surface to a human. Do not attempt automated fixes — the code has drifted from intended behavior and automated fixes typically make things worse. |

Treat a `conditional` clearance as a signal for human review, not a green light.

## Common mistakes to avoid

- **Collapsing the split.** Loading `get_weapon` output into your Attacker context, even to "inform" plan generation. Blocker text must never reach the Attacker, not even paraphrased.
- **Skipping the holdout phase.** The Inspector's findings are not a substitute for the holdout — the Inspector never saw the blockers.
- **Over-specified plans.** Three broad plans covering three misuse patterns are higher value than one tightly-targeted plan aimed at a specific assertion.
- **Reading `holdout_results` into the Inspector context of a later iteration.** Don't feed holdout outcomes back into the Attacker/Inspector loop.
- **Editing weapon files mid-run.** If blockers change while you're iterating, the holdout is no longer a holdout. Stop and restart.

## Single-call invocations

Some prompts want a summary, not a full run:

- "What weapons are available?" → `list_weapons()` and format the briefs. No iteration.
- "Is this weapon well-formed?" → `assess_weapon(id, target)` and show the result.
- "What's the default iteration ladder?" → `default_iteration_specs()`.

Don't launch the full loop unless the user clearly wants a run.
