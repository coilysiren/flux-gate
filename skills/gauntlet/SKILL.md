---
name: gauntlet
description: Adversarial API inspection via the Gauntlet MCP server. Use this skill when the user wants to stress-test a running HTTP API under attack, validate authorization/ownership/input invariants before promoting code, or run Gauntlet's role-disciplined adversarial loop against a SUT. Triggers include "run gauntlet", "adversarial test", "check before merging", "attack this API", "run the hardening loop".
---

# Gauntlet

Gauntlet is an adversarial API inspection loop. **You** are the Orchestrator: you preflight weapons, drive the loop, and assemble the final report. The actual Attacker / Inspector / HoldoutEvaluator work runs inside dedicated **subagents** that the Gauntlet plugin ships alongside this skill, each with an MCP-tool allowlist that physically enforces the train/test split.

Gauntlet's novelty is the train/test split: each Weapon has a `description` (the attack surface, shown to the Attacker) and `blockers` (the expected invariants, withheld from the Attacker and checked only by the HoldoutEvaluator). Violating the split invalidates the run. The subagent allowlists make a violation impossible at the permission layer rather than a matter of prompt discipline.

## The four roles

| Role | Where it runs | MCP tools it can call |
|---|---|---|
| **Orchestrator** | This skill (you) | every Gauntlet tool |
| **Attacker** | `gauntlet-attacker` subagent | `list_weapons`, `execute_plan`, `read_iteration_records`, `record_iteration` |
| **Inspector** | `gauntlet-inspector` subagent | `read_iteration_records`, `record_iteration` |
| **HoldoutEvaluator** | `gauntlet-holdout-evaluator` subagent | `get_weapon`, `execute_plan`, `record_holdout_result` |

The Attacker and Inspector subagents cannot call `get_weapon` even if their prompts told them to — Claude Code's permission layer rejects the call before it reaches the MCP server. Likewise the HoldoutEvaluator cannot read the iteration buffer that holds Attacker plans and Inspector findings; it works from a fresh context informed only by the weapon's blockers.

## Prerequisites

- The Gauntlet plugin is installed; the MCP server is registered. Confirm with `/mcp` (should list `gauntlet` and its tools) and `/agents` (should list `gauntlet-attacker`, `gauntlet-inspector`, `gauntlet-holdout-evaluator`).
- The project has a `.gauntlet/` directory with at least one weapon YAML. If missing, tell the user and stop — or, if the user has a product spec, dispatch the `gauntlet-author` skill (also shipped in this plugin) to generate weapons first.
- A running SUT whose URL the host can reach. If the user hasn't named a URL, ask.
- Existing tests pass. Gauntlet is the final check, not a first-pass linter.

## The loop

### Step 1 — Orchestrator: pick weapons and start the run

1. `list_weapons(weapons_path)` → pick one (or several) by `id`. If the user named a weapon, use it. If not, present the list and ask.
2. `start_run(weapon_ids=[...])` → `{run_id}`. Carry `run_id` through every subsequent dispatch.

LUCA's iteration ladder is fixed at 4 stages: `baseline` → `boundary` → `adversarial_misuse` → `targeted_escalation`. Build the `IterationSpec` list inline; there is no MCP tool to fetch it.

### Step 2 — For each weapon, iterate the train side (typically 4 iterations)

For each `IterationSpec`, dispatch the Attacker subagent, then the Inspector subagent.

**Dispatch the Attacker** — pass `run_id`, `weapon_id`, the iteration spec, the SUT `url`, and `user_headers`. The subagent will:

- read its weapon brief (no blockers),
- read prior iteration records to see what's already been tried,
- compose 2–4 plans, execute them, and append an `IterationRecord` (with empty `findings`) via `record_iteration`,
- return a one-paragraph summary.

**Dispatch the Inspector** — pass `run_id`, `weapon_id`, and the iteration spec. The subagent will:

- read the buffer to find the Attacker's latest record,
- analyse the `ExecutionResult`s into `Finding`s (with `violated_blocker=null`, always — the buffer rejects anything else),
- append a follow-up `IterationRecord` (with empty plans/results, populated findings) via `record_iteration`,
- return a one-paragraph summary.

If the Inspector reports a `high`-severity finding, you may stop iterating early. Note this in the final summary.

### Step 3 — For each weapon, dispatch the HoldoutEvaluator (fresh context)

Critical: do not paste any Attacker plan, Inspector finding, or summary into the HoldoutEvaluator's dispatch prompt. It runs from a fresh context with only `run_id`, `weapon_id`, the SUT `url`, and `user_headers`. The subagent will:

- call `get_weapon(weapon_id)` to read the blockers,
- derive one acceptance plan per blocker, execute each, and append a `HoldoutResult` via `record_holdout_result`,
- return a one-paragraph summary.

### Step 4 — Orchestrator: assemble the per-weapon report

For each weapon: `assemble_run_report(run_id=run_id, weapon_id=weapon_id, clearance_threshold=0.9)` → `{risk_report, clearance}`.

Show the user, per weapon:
- `risk_level` (low | medium | high)
- `confirmed_failures` (the list of violated invariants — safe to show, these are outcomes, not blocker text)
- `clearance.recommendation` (pass | conditional | block) if present
- `confidence_score` and `coverage` summary

If you ran multiple weapons, follow up with `assemble_final_clearance(run_id)` to get the overall pass/fail decision across all of them.

## Acting on results

| `risk_level` | Action |
|---|---|
| `low` | Safe to promote or merge. |
| `medium` | Attempt fixes and re-run the loop. |
| `high` | **Stop.** Surface to a human. Do not attempt automated fixes — the code has drifted from intended behavior and automated fixes typically make things worse. |

Treat a `conditional` clearance as a signal for human review, not a green light.

## Why subagents (and not just role-context switching)

Older versions of this skill drove the loop by switching contexts inside a single host session: "now I'm the Attacker, now I'm the Inspector, now I'm the HoldoutEvaluator." That worked, but the train/test split was held only by prompt discipline — a sloppy summary or a prompt-injection attack could collapse it silently.

The subagent model fixes that structurally:

- The Attacker subagent's tool allowlist does not include `mcp__gauntlet__get_weapon`. It cannot call it. Even if its prompt asks it to.
- The Inspector subagent's allowlist does not include `mcp__gauntlet__get_weapon` or `mcp__gauntlet__read_holdout_results`.
- The HoldoutEvaluator subagent's allowlist does not include `mcp__gauntlet__read_iteration_records`. It runs in fresh context with no carryover.
- The MCP server's `record_iteration` rejects findings with non-null `violated_blocker`, so the Inspector cannot smuggle blocker-shaped data through the iteration buffer either.

That is kernel-level enforcement of the split, not a stylistic recommendation.

## Single-call invocations

Some prompts want a summary, not a full run:

- "What weapons are available?" → `list_weapons()` and format the briefs. No iteration, no run buffer.

Don't launch the full loop unless the user clearly wants a run.
