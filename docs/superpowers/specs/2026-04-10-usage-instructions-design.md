# Usage Instructions Design

**Date:** 2026-04-10  
**Topic:** `docs/usage.md` — workflow-oriented usage guide for AI agents

## Summary

Move the existing usage content from `README.md` into a new `docs/usage.md`, then expand it into a workflow-oriented runbook targeting AI agents as the primary reader. CLI flags and environment variable reference stay in the README. `docs/usage.md` is about *when* and *why* to run Flux Gate and *what to do* with the results.

## Target audience

AI agents operating in agentic or dark factory workflows. The doc is written as a runbook: sequential, action-oriented, with explicit decision rules at each step.

## File

`docs/usage.md` — new file, content moved from README and expanded.

## Structure

### Section 1: When to run Flux Gate

Flux Gate runs *after* existing tests pass and *before* promoting or merging. It is not a test runner — it assumes the code and its tests share the same blind spots. Running it before promotion adds a second pass from an adversary that has no knowledge of how the code was written.

Place it as the final gate in a CI pipeline or agentic loop.

### Section 2: Set up credentials

Export credentials for both roles before running. Using different providers for Operator and Adversary is intentional — model diversity reduces shared blind spots.

Covers:
- The four env vars (`FLUX_GATE_OPERATOR_TYPE`, `FLUX_GATE_OPERATOR_KEY`, `FLUX_GATE_ADVERSARY_TYPE`, `FLUX_GATE_ADVERSARY_KEY`)
- Default models per provider
- Where to put them: CI secrets or inherited environment in an agentic loop

References README for the full env var table.

### Section 3: Write guards

Guards define properties the system must never violate. Each guard is a YAML file in `.flux_gate/guards/`.

Covers:
- Guard fields: `title`, `description`, `must_hold`, `target_endpoints`
- The train/test split mechanic: `must_hold` is hidden from the Operator, so the agent that wrote the code cannot inadvertently write code that passes by knowing what the checks are
- Tips for writing good guards: one property per file, falsifiable `must_hold` statements, file names that match the property (e.g. `task_ownership.yaml`)
- Actor auth config (`.flux_gate/actors.yaml`): per-actor credentials, supported types (`bearer`, `api_key`)

### Section 4: Run Flux Gate

Workflow mechanics for two contexts:

**CI pipeline:** Invoke after tests pass. A non-zero exit code is a build failure — do not promote.

**Agentic loop:** Invoke after code is written and a staging environment is running, before marking a task complete. Flux Gate is the final check before the agent declares work done.

References README for CLI flags and invocation syntax.

### Section 5: Interpret results and act

The most agent-critical section. Explicit decision rules by `risk_level`:

| risk_level | Action |
|---|---|
| `low` | Promote or merge |
| `medium` | Attempt fixes, re-run Flux Gate |
| `high` or `critical` | Stop. Surface to a human. Do not attempt automated fixes. |

A `high` or `critical` result means the agent has drifted from the intended behavior — automated fixes are likely to make things worse. Human realignment is required.

Also covers:
- Output fields: `confidence_score`, `confirmed_failures`, `coverage`, `unexplored_surfaces`
- Accumulate failure output over time: `confirmed_failures` across runs are a knowledge base. Save them to prevent regressions in future work.

## What stays in README

- CLI invocation syntax and all flags (`--guard`, `--actors`, `--threshold`, `--no-fail-fast`)
- Full env var reference table
- Quick start

The README remains the reference. `docs/usage.md` is the runbook.
