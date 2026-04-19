# Usage

Workflow guide for a host Claude Code agent driving the Gauntlet MCP server. For the full tool reference, see the [README](../README.md).

## Prerequisites

- Gauntlet installed and registered as an MCP server in the Claude Code project. See [README - Install](../README.md#install).
- A running SUT (URL the host can reach).
- A `.gauntlet/` directory at the project root with at least one weapon YAML.

No Anthropic or OpenAI credentials are needed. Gauntlet never calls an LLM itself; the host already has auth.

## When to invoke Gauntlet

Invoke Gauntlet after existing tests pass and before promoting or merging. It is not a test runner - it assumes the code and its tests share the same blind spots because they were likely written by the same agent. Running Gauntlet before promotion adds a second inspection pass from the host acting as a deliberate Attacker, with `blockers` held back via the train/test split.

Place the invocation as the final checkpoint in the host's pipeline.

## The host-driven loop

Gauntlet exposes 13 MCP tools (see the [README](../README.md#mcp-tools) for the full table). The host drives them in roughly this order:

1. **Orchestrator**: pick weapons, start the run.
   ```
   list_weapons()                 → list[dict]   # {id, title, description}
   start_run(weapon_ids=[...])    → {run_id}
   ```

2. **Per iteration** (typically four - baseline → boundary → adversarial_misuse → targeted_escalation), with per-role subagents appending to the run buffer via `record_iteration` / `read_iteration_records`:
   - **Attacker context** (reads the attacker view of the weapon — `{id, title, description}` only): compose one or more `Plan`s targeting the weapon's surface, drawing on prior iteration results.
   - **Drone** (via MCP): `execute_plan(url, plan, user_headers)` → `ExecutionResult`. Repeat per plan.
   - **Inspector context** (reads `ExecutionResult`s, not blockers): produce `Finding`s. Optionally mark some as `is_anomaly=True`.
   - Append an `IterationRecord` bundling the spec, plans, results, and findings.

3. **HoldoutEvaluator context** (reads full `Weapon` including `blockers`, appends via `record_holdout_result`):
   ```
   get_weapon(id) → Weapon
   ```
   Derive acceptance plans from each blocker - one structured `Plan` per blocker. Execute each with `execute_plan` and record the outcome.

4. **Orchestrator**:
   ```
   assemble_run_report(run_id, weapon_id, clearance_threshold=0.9)
   → { risk_report, clearance }
   assemble_final_clearance(run_id, clearance_threshold=0.9)
   → FinalClearance
   ```

Optional Orchestrator-side tools round out the loop: `mutate_plans` (deterministic plan variants between iterations), `replay_finding` (re-execute a stored finding's `ReplayBundle` — useful for "did the fix work" loops), and `recurring_failures` (cross-run issues that have repeated in the last N runs).

The train/test split is enforced at the permission layer via the per-role subagents' MCP-tool allowlists, plus at the buffer boundary by `record_iteration` (which rejects findings carrying blocker text).

## Writing weapons

Weapons define attack strategies reusable across API surfaces. Each is a YAML file in `.gauntlet/weapons/`.

```yaml
# .gauntlet/weapons/task_ownership.yaml
title: Users cannot modify each other's tasks
description: >
  The task API must enforce resource ownership. A user who did not create
  a task must not be able to modify or delete it.
blockers:
  - A PATCH request by a non-owner is rejected with 403
  - The task body is unchanged after an unauthorized PATCH attempt
  - A GET by the owner after an unauthorized PATCH returns the original data
```

**The train/test split:** `blockers` are not surfaced by `list_weapons`. Only `get_weapon` returns them, and the host must only read `get_weapon` output in its HoldoutEvaluator context. If blocker text appears in an Attacker-role prompt, the split is broken and the run is invalid.

Tips:
- One weapon per file, named for the property it protects (e.g. `task_ownership.yaml`).
- Write blockers as falsifiable statements about what the system does, not how.

## User authentication

If the SUT requires authentication, the orchestrator passes `user_headers` to `execute_plan`: a `dict[str, dict[str, str]]` mapping user names to per-user request headers, e.g. `{"alice": {"Authorization": "Bearer ..."}}`. Users without an entry fall back to the default `X-User: <name>` header.

A dark-factory orchestrator already holds these credentials (it provisioned them during deployment), so it passes them in directly — no `.gauntlet/users.yaml` file needed.

## Interpreting results and acting

`assemble_run_report` returns a dict of shape:

```python
{
  "risk_report": {
    "confidence_score": 0.06,
    "risk_level": "high",
    "confirmed_failures": ["unauthorized_cross_user_modification"],
    "coverage": ["GET /tasks/42", "PATCH /tasks/42", "POST /tasks"],
    "conclusion": "System fails under adversarial pressure ...",
    ...
  },
  "clearance": {
    "passed": False,
    "recommendation": "block",
    "holdout_satisfaction_score": 0.0,
    "threshold": 0.9,
    "rationale": "...",
  } | None,
}
```

Act based on `risk_level`:

| risk_level | Action |
|---|---|
| `low` | Promote or merge |
| `medium` | Attempt fixes, re-run |
| `high` | Stop. Do not attempt automated fixes. Surface to a human. |

A `high` result means the agent has drifted from intended behavior. Automated fixes are likely to make things worse; human realignment is required.

