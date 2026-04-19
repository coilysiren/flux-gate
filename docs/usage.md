# Usage

Workflow guide for a host Claude Code agent driving the Gauntlet MCP server. For the full tool reference, see the [README](../README.md).

## Prerequisites

- Gauntlet installed and registered as an MCP server in the Claude Code project. See [README - Install and register](../README.md#install-and-register).
- A running SUT (URL the host can reach).
- A `.gauntlet/` directory at the project root with at least one weapon and one target.

No Anthropic or OpenAI credentials are needed. Gauntlet never calls an LLM itself; the host already has auth.

## When to invoke Gauntlet

Invoke Gauntlet after existing tests pass and before promoting or merging. It is not a test runner - it assumes the code and its tests share the same blind spots because they were likely written by the same agent. Running Gauntlet before promotion adds a second inspection pass from the host acting as a deliberate Attacker, with `blockers` held back via the train/test split.

Place the invocation as the final checkpoint in the host's pipeline.

## The host-driven loop

Gauntlet exposes seven MCP tools. The host drives them in roughly this order:

1. **Orchestrator**: pick a weapon and target.
   ```
   list_weapons()         → list[WeaponBrief]
   list_targets()         → list[Target]
   assess_weapon(id, t)   → WeaponAssessment   # optional preflight
   default_iteration_specs() → list[IterationSpec]
   ```

2. **Per iteration** (typically four - baseline → boundary → adversarial_misuse → targeted_escalation):
   - **Attacker context** (reads `WeaponBrief` only): compose one or more `Plan`s targeting the weapon's surface, drawing on prior iteration results.
   - **Drone** (via MCP): `execute_plan(url, plan, users_path)` → `ExecutionResult`. Repeat per plan.
   - **Inspector context** (reads `ExecutionResult`s, not blockers): produce `Finding`s. Optionally mark some as `is_anomaly=True`.
   - Append an `IterationRecord` bundling the spec, plans, results, and findings.

3. **HoldoutEvaluator context** (reads full `Weapon` including `blockers`):
   ```
   get_weapon(id) → Weapon
   ```
   Derive acceptance plans from each blocker - one structured `Plan` per blocker. Execute each with `execute_plan`. Collect the `ExecutionResult` list as `holdout_results`.

4. **Orchestrator**:
   ```
   assemble_run_report(iterations, holdout_results, clearance_threshold=0.9)
   → { risk_report, clearance }
   ```

The host is responsible for preserving the train/test split in its own prompts. Gauntlet enforces it structurally only for `list_weapons` (which returns `WeaponBrief` - no blockers field exists to leak). For every other tool, leakage is a prompt-discipline matter.

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

## Writing targets

Targets define the API surface a weapon is tested against. One target per YAML file in `.gauntlet/targets/`.

```yaml
# .gauntlet/targets/task_endpoints.yaml
title: Task ownership endpoints
endpoints:
  - POST /tasks
  - PATCH /tasks/{id}
  - GET /tasks/{id}
```

One weapon can be paired with many targets - run the loop once per weapon/target combination.

## User authentication

If your API uses authentication, create `.gauntlet/users.yaml` with per-user credentials. Credentials themselves stay in env vars; the YAML just names them. See [README - User authentication](../README.md#user-authentication).

```yaml
# .gauntlet/users.yaml
users:
  alice:
    type: bearer
    token_env: ALICE_TOKEN
  bob:
    type: api_key
    header: X-API-Key
    key_env: BOB_API_KEY
```

Users omitted from the file fall back to the default `X-User: <name>` header.

## Arsenals

An Arsenal bundles related weapons under one YAML file so the host can load an entire attack class (authorization, input validation, OWASP top-10) in one call.

```yaml
# .gauntlet/authz_arsenal.yaml
name: authz
description: Authorization and ownership enforcement weapons
weapons:
  - id: identity_swap
    title: Users cannot access or modify each other's resources
    description: >
      The API must enforce resource ownership at every endpoint.
    blockers:
      - A write request by a non-owner is rejected with 403 or 404
      - A read request by a non-owner returns 403 or 404
```

Pass `arsenal_path` to `list_weapons`, `get_weapon`, or `assess_weapon` to load from an arsenal.

## OpenAPI-driven targets

If your API has an OpenAPI 3.x spec, pass `openapi_path` to `list_targets` to auto-generate `Target` objects from the spec instead of writing them by hand. Targets parsed from the spec are prepended to any manually-defined targets read from `targets_path`.

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

