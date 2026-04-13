# Usage

Workflow guide for AI agents operating in CI pipelines or agentic dark factory loops. For CLI flags and environment variable reference, see the [README](../README.md).

## When to run Gauntlet

Run Gauntlet after your existing tests pass and before promoting or merging. It is not a test runner — it assumes the code and its tests share the same blind spots, because they were likely written by the same agent. Running it before promotion adds a second inspection pass from a model that has no knowledge of how the code was written.

Place it as the final checkpoint in your CI pipeline or agentic loop.

## Set up credentials

Export credentials for both roles before running:

```bash
export GAUNTLET_ATTACKER_TYPE=openai
export GAUNTLET_ATTACKER_KEY=sk-...
export GAUNTLET_INSPECTOR_TYPE=anthropic
export GAUNTLET_INSPECTOR_KEY=sk-ant-...
```

Using different providers for Attacker and Inspector is intentional — model diversity reduces shared blind spots. Default models are `gpt-4o` (OpenAI) and `claude-opus-4-5` (Anthropic).

In CI, set these as secrets. In an agentic loop, they are inherited from the environment.

See the [README](../README.md#llm-configuration) for the full reference table.

## Write weapons

Weapons define attack strategies that are reusable across API surfaces. Each weapon is a YAML file in `.gauntlet/weapons/`.

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

**The train/test split:** `blockers` are never shown to the Attacker — only to the holdout evaluator. This means the agent that wrote the code cannot inadvertently write code that passes by knowing what the checks are. Keep `blockers` statements specific and falsifiable.

Tips:
- One weapon per file — name the file after the property it protects (e.g. `task_ownership.yaml`)
- `blockers` are Vitals — externally observable truths about system behavior. Write them as falsifiable statements about what the system does, not how it does it, and not in terms specific to any execution surface

## Write targets

Targets define the API surface a weapon is tested against. Each target is a YAML file in `.gauntlet/targets/`.

```yaml
# .gauntlet/targets/task_endpoints.yaml
title: Task ownership endpoints
endpoints:
  - POST /tasks
  - PATCH /tasks/{id}
  - GET /tasks/{id}
```

One weapon can be paired with many targets — the runner executes one pass per weapon/target combination. If no targets are configured, each weapon runs without a specific target.

### User authentication

If your API uses authentication, create `.gauntlet/users.yaml` to provide per-user credentials. Users omitted from the file fall back to the default `X-User: <name>` header.

```yaml
# .gauntlet/users.yaml
users:
  alice:
    type: bearer
    token_env: ALICE_TOKEN       # export ALICE_TOKEN=eyJ...
  bob:
    type: api_key
    header: X-API-Key
    key_env: BOB_API_KEY         # export BOB_API_KEY=sk-...
```

See the [README](../README.md#user-authentication) for supported authentication types.

## Configuration file

All CLI options can be specified in a YAML config file. By default, Gauntlet loads `.gauntlet/config.yaml` if it exists. Use `--config` to point to a different file.

```yaml
# .gauntlet/config.yaml
url: http://localhost:8000
weapon: .gauntlet/weapons
target: .gauntlet/targets
users: .gauntlet/users.yaml
threshold: 0.90
fail_fast: true
```

CLI flags always override values from the config file. For example, to use a config file but override the threshold:

```bash
gauntlet --threshold 0.50
```

If both a config file and a positional URL are provided, the positional URL takes precedence.

## Run Gauntlet

### CI pipeline

Run after all tests pass. Treat a non-zero exit code as a build failure — do not promote.

```yaml
# Example GitHub Actions step
- name: Run Gauntlet
  run: gauntlet ${{ env.STAGING_URL }}
  env:
    GAUNTLET_ATTACKER_TYPE: openai
    GAUNTLET_ATTACKER_KEY: ${{ secrets.OPENAI_API_KEY }}
    GAUNTLET_INSPECTOR_TYPE: anthropic
    GAUNTLET_INSPECTOR_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

### Agentic loop

Run after code is written and a staging environment is running, before marking a task complete:

1. Write code
2. Run existing tests — if they fail, fix and repeat
3. Deploy to staging environment
4. Run `gauntlet <staging-url>`
5. Interpret results (see below) and act

Gauntlet is the final check before declaring work done. Do not skip it.

See the [README](../README.md#cli) for CLI flags and invocation syntax.

## Interpret results and act

Gauntlet outputs a YAML risk report:

```yaml
risk_report:
  confidence_score: 0.06
  risk_level: critical
  confirmed_failures:
    - unauthorized_cross_user_modification
  coverage:
    - GET /tasks/42
    - PATCH /tasks/42
    - POST /tasks
  conclusion: >-
    System fails under adversarial pressure and should not be promoted
    without remediation.
```

Act based on `risk_level`:

| risk_level | Action |
|---|---|
| `low` | Promote or merge |
| `medium` | Attempt fixes, re-run Gauntlet |
| `high` or `critical` | Stop. Do not attempt automated fixes. Surface to a human. |

A `high` or `critical` result means the agent has drifted from the intended behavior. Automated fixes are likely to make things worse. Human realignment is required.

### Accumulate failure knowledge

Save the `confirmed_failures` from each run. Over time this becomes a knowledge base of failure patterns. Reference it when writing new weapons and reviewing code — recurring failures indicate systemic gaps in weapon coverage.
