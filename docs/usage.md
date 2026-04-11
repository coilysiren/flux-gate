# Usage

Workflow guide for AI agents operating in CI pipelines or agentic dark factory loops. For CLI flags and environment variable reference, see the [README](../README.md).

## When to run Flux Gate

Run Flux Gate after your existing tests pass and before promoting or merging. It is not a test runner — it assumes the code and its tests share the same blind spots, because they were likely written by the same agent. Running it before promotion adds a second adversarial pass from a model that has no knowledge of how the code was written.

Place it as the final gate in your CI pipeline or agentic loop.

## Set up credentials

Export credentials for both roles before running:

```bash
export FLUX_GATE_OPERATOR_TYPE=openai
export FLUX_GATE_OPERATOR_KEY=sk-...
export FLUX_GATE_ADVERSARY_TYPE=anthropic
export FLUX_GATE_ADVERSARY_KEY=sk-ant-...
```

Using different providers for Operator and Adversary is intentional — model diversity reduces shared blind spots. Default models are `gpt-4o` (OpenAI) and `claude-opus-4-5` (Anthropic).

In CI, set these as secrets. In an agentic loop, they are inherited from the environment.

See the [README](../README.md#llm-configuration) for the full reference table.

## Write guards

Guards define properties the system must never violate. Each guard is a YAML file in `.flux_gate/guards/`.

```yaml
# .flux_gate/guards/task_ownership.yaml
title: Users cannot modify each other's tasks
description: >
  The task API must enforce resource ownership. A user who did not create
  a task must not be able to modify or delete it.
must_hold:
  - A PATCH request by a non-owner is rejected with 403
  - The task body is unchanged after an unauthorized PATCH attempt
  - A GET by the owner after an unauthorized PATCH returns the original data
target_endpoints:
  - POST /tasks
  - PATCH /tasks/{id}
  - GET /tasks/{id}
```

**The train/test split:** `must_hold` properties are never shown to the Operator — only to the holdout evaluator. This means the agent that wrote the code cannot inadvertently write code that passes by knowing what the checks are. Keep `must_hold` statements specific and falsifiable.

Tips:
- One property per file — name the file after the property it protects (e.g. `task_ownership.yaml`)
- `must_hold` statements should describe observable HTTP behavior, not implementation details
- `target_endpoints` scopes the Operator to the relevant API surface

### Actor authentication

If your API uses authentication, create `.flux_gate/actors.yaml` to provide per-actor credentials. Actors omitted from the file fall back to the default `X-Actor: <name>` header.

```yaml
# .flux_gate/actors.yaml
actors:
  alice:
    type: bearer
    token_env: ALICE_TOKEN       # export ALICE_TOKEN=eyJ...
  bob:
    type: api_key
    header: X-API-Key
    key_env: BOB_API_KEY         # export BOB_API_KEY=sk-...
```

See the [README](../README.md#actor-authentication) for supported authentication types.

## Run Flux Gate

### CI pipeline

Run after all tests pass. Treat a non-zero exit code as a build failure — do not promote.

```yaml
# Example GitHub Actions step
- name: Run Flux Gate
  run: flux-gate ${{ env.STAGING_URL }}
  env:
    FLUX_GATE_OPERATOR_TYPE: openai
    FLUX_GATE_OPERATOR_KEY: ${{ secrets.OPENAI_API_KEY }}
    FLUX_GATE_ADVERSARY_TYPE: anthropic
    FLUX_GATE_ADVERSARY_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

### Agentic loop

Run after code is written and a staging environment is running, before marking a task complete:

1. Write code
2. Run existing tests — if they fail, fix and repeat
3. Deploy to staging environment
4. Run `flux-gate <staging-url>`
5. Interpret results (see below) and act

Flux Gate is the final check before declaring work done. Do not skip it.

See the [README](../README.md#cli) for CLI flags and invocation syntax.

## Interpret results and act

Flux Gate outputs a YAML risk report:

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
| `medium` | Attempt fixes, re-run Flux Gate |
| `high` or `critical` | Stop. Do not attempt automated fixes. Surface to a human. |

A `high` or `critical` result means the agent has drifted from the intended behavior. Automated fixes are likely to make things worse. Human realignment is required.

### Accumulate failure knowledge

Save the `confirmed_failures` from each run. Over time this becomes a knowledge base of failure patterns. Reference it when writing new guards and reviewing code — recurring failures indicate systemic gaps in guard coverage.
