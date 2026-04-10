# ⚡🔄🛂 Flux Gate

Flux Gate is a two-agent adversarial loop that infers software correctness by observing how code behaves under sustained, targeted attack. It's designed as quality control for a dark factory environment — where code is written by bots and verified by attack.

## Quick start

Set your LLM credentials, then point Flux Gate at a running API:

```bash
export FLUX_GATE_OPERATOR_TYPE=openai
export FLUX_GATE_OPERATOR_KEY=sk-...
export FLUX_GATE_ADVERSARY_TYPE=anthropic
export FLUX_GATE_ADVERSARY_KEY=sk-ant-...

git clone git@github.com:coilysiren/flux-gate.git
cd flux-gate
docker compose run --rm demo
```

That starts the demo API and runs the full adversarial loop against it.

## Installation

```bash
pip install flux-gate
# or: uv add flux-gate
```

## Usage

### LLM configuration

Flux Gate requires one LLM for the Operator role and one for the Adversary role. Configure
each with a pair of environment variables:

| Variable | Description |
|---|---|
| `FLUX_GATE_OPERATOR_TYPE` | LLM provider for the Operator: `openai` or `anthropic` |
| `FLUX_GATE_OPERATOR_KEY` | API key for the Operator's provider |
| `FLUX_GATE_ADVERSARY_TYPE` | LLM provider for the Adversary: `openai` or `anthropic` |
| `FLUX_GATE_ADVERSARY_KEY` | API key for the Adversary's provider |

The default models are `gpt-4o` for OpenAI and `claude-opus-4-5` for Anthropic.
Using different providers for each role is intentional — model diversity reduces blind spots.

### CLI

```
flux-gate <url> [--name NAME] [--env ENV] [--spec FILE] [--actors FILE] [--threshold N] [--fail-fast-tier N]
```

| Argument | Default | Description |
|---|---|---|
| `url` | required | Base URL of the running API |
| `--name` | URL hostname | Label for the system under test in the report |
| `--env` | `local` | Environment label (e.g. `staging`, `ci`) |
| `--spec` | — | Path to a [FeatureSpec YAML](#feature-spec) file; enables holdout evaluation and merge gate |
| `--actors` | — | Path to an [actors YAML](#actor-authentication) file for per-actor credentials |
| `--threshold` | `0.90` | Holdout satisfaction score required to recommend merge |
| `--fail-fast-tier` | disabled | Stop after the first tier ≥ N that finds a critical issue |

```bash
flux-gate http://localhost:8000
flux-gate http://localhost:8000 --name "Task API" --env staging
flux-gate http://localhost:8000 --spec /path/to/spec.yaml --actors /path/to/actors.yaml
```

Output is YAML:

```yaml
system_under_test: Task API (staging)
environment: local
risk_report:
  confidence_score: 0.06
  risk_level: critical
  confirmed_failures:
    - unauthorized_cross_user_modification   # userB rewrote userA's task
  coverage:
    - GET /tasks/42
    - PATCH /tasks/42
    - POST /tasks
  conclusion: >-
    System fails under adversarial pressure and should not be promoted
    without remediation.
```

### Project config directory

Place your Flux Gate config files in a `.flux_gate/` directory at the root of your project.
The CLI discovers them automatically — no flags needed for the common case:

```
your-project/
├── .flux_gate/
│   ├── spec.yaml      # FeatureSpec — loaded automatically
│   └── actors.yaml    # Actor auth — loaded automatically if present
└── ...
```

Override either path with `--spec FILE` or `--actors FILE`.

### Feature spec

A FeatureSpec tells Flux Gate what you're testing and defines the holdout acceptance criteria.
Acceptance criteria are never shown to the Operator — only to the holdout evaluator — preserving
the train/test separation.

```yaml
# .flux_gate/spec.yaml
title: Users cannot modify each other's tasks
description: >
  The task API must enforce resource ownership. A user who did not create
  a task must not be able to modify or delete it.
acceptance_criteria:
  - A PATCH request by a non-owner is rejected with 403
  - The task body is unchanged after an unauthorized PATCH attempt
  - A GET by the owner after an unauthorized PATCH returns the original data
target_endpoints:
  - POST /tasks
  - PATCH /tasks/{id}
  - GET /tasks/{id}
```

### Actor authentication

Create `.flux_gate/actors.yaml` to provide per-actor credentials. Secret values are
never stored in the file — each entry names an environment variable that holds the
actual credential. Actors omitted from the file fall back to the default `X-Actor: <name>` header.

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

Supported authentication types:

| Type | Fields | Header sent |
|---|---|---|
| `bearer` | `token_env` | `Authorization: Bearer <$token_env>` |
| `api_key` | `header`, `key_env` | `<header>: <$key_env>` |

---

## Core Model

Flux Gate treats code change correctness as a problem of behavioral observation while under attack.

* Code is assumed to be untrusted, potentially written but a human - but designed to be written by a bot
* Tests are generated dynamically
* Confidence emerges from what survives adversarial probing

It asks: "How hard did we try to break this, and what happened when we did?"

## The Two Roles

### The Operator

Explores the execution space

* Constructs plausible, production-like scenarios
* Simulates how the system will actually be used (and misused)
* Explores workflows, edge cases, and state transitions
* Adapts based on what has already been tested

The Operator is not trying to prove correctness. It is trying to create situations where correctness might fail.

### The Adversary

Applies intelligent pressure

* Analyzes execution results for weaknesses
* Identifies suspicious passes and untested assumptions
* Forms hypotheses about hidden failure modes
* Forces the next round of scenarios toward likely breakpoints

The Adversary assumes "This system is broken. I just haven't proven it yet."

### Dynamic Between Them

* The Operator explores
* The Adversary sharpens
* Execution grounds both

Together, they perform a form of guided adversarial search over the space of possible failures.

## Concrete Flow (v0, fixed 4 iterations)

```yaml
flux_gate_run:
  system_under_test: REST API
  environment: deterministic_local

  loop:
    iterations: 4

    iteration_1:
      goal: broad_baseline
      operator: generate diverse CRUD + lifecycle scenarios
      execute: run scenarios
      adversary: identify anomalies and weak coverage

    iteration_2:
      goal: boundary_and_invariants
      operator: target edge cases, missing fields, schema drift
      execute: run refined scenarios
      adversary: escalate invariant violations

    iteration_3:
      goal: adversarial_misuse
      operator: simulate auth violations, invalid transitions, cross-user access
      execute: run attack scenarios
      adversary: identify security and logic failures

    iteration_4:
      goal: targeted_followup
      operator: focus only on suspicious areas
      execute: confirm and expand blast radius
      adversary: finalize failure model
```

### Scenario Shape (Example)

```yaml
scenario:
  name: user_cannot_modify_other_users_task
  category: authz

  steps:
    - actor: alice                      # alice creates a task
      request:
        method: POST
        path: /tasks
        body:
          title: "Q3 budget review"

    - actor: bob                        # bob tries to check it off
      request:
        method: PATCH
        path: /tasks/{id}
        body:
          completed: true

    - actor: alice                      # alice checks her task
      request:
        method: GET
        path: /tasks/{id}

  assertions:
    - type: status_code
      expected: 403                     # bob should have been stopped

    - type: invariant
      rule: task_not_modified_by_other_user
```

### Execution Output (Example)

```yaml
execution_result:
  scenario: user_cannot_modify_other_users_task

  steps:
    - step: 1       # alice creates task — 201 Created, looks fine
      status: 201

    - step: 2       # bob's patch went through — this is wrong
      status: 200

    - step: 3       # alice sees bob's changes — the task was corrupted
      status: 200
      body:
        completed: true
        last_modified_by: bob

  assertions:
    - name: unauthorized_patch_blocked
      result: fail   # bob should never have gotten a 200
```

### Adversary Finding (Example)

```yaml
finding:
  issue: unauthorized_cross_user_modification
  severity: critical
  confidence: 0.94

  rationale: >
    Bob patched Alice's task and got a 200. Alice's subsequent GET confirms
    the mutation stuck. No ownership check is being enforced on PATCH.
    Reproduced deterministically across all four iterations.

  next_targets:
    - ownership check on DELETE (probably also missing)
    - task list endpoint — can bob see alice's tasks too?
    - partial update invariants under concurrent writes
```

### Final Output

```yaml
risk_report:
  confidence_score: 0.27
  risk_level: critical

  summary:
    - any authenticated user can overwrite any other user's task
    - PATCH applies writes without checking resource ownership
    - invariants broken under partial update — completed flips silently

  coverage:
    endpoints_tested:
      - POST /tasks
      - PATCH /tasks/{id}
      - GET /tasks/{id}

  conclusion: >
    System fails under moderate adversarial pressure.
    Ownership enforcement is absent on mutating endpoints.
    Do not promote to production.
```

## Prior Art

These projects informed Flux Gate's design:

- **[StrongDM Software Factory](https://factory.strongdm.ai/)** — Production dark factory. Introduced "satisfaction metrics" (probabilistic, not boolean), the Digital Twin Universe (behavioral clones of third-party services), and the principle that scenarios live outside the codebase to prevent reward-hacking.

- **[OctopusGarden](https://github.com/foundatron/octopusgarden)** — Open-source autonomous development platform. Introduced the attractor loop (iterative convergence to a satisfaction threshold), stratified scenario difficulty, stall recovery via high-temperature "wonder" phases, and model escalation (cheap → premium after non-improving iterations).

- **[Fabro](https://github.com/fabro-sh/fabro)** — Open-source dark factory orchestrator. Introduced workflow-as-graph (Graphviz DOT, version-controlled), multi-model routing via CSS-like stylesheets, human-in-the-loop hexagon gates, and per-stage Git checkpointing.

## What Makes This Different

Flux Gate is not:

* a test runner
* a code reviewer
* a fuzzing tool

It is an adversarial inference engine for software correctness.

It combines:

* dynamic scenario generation (like red teaming)
* execution grounding (like CI)
* adversarial refinement (like security testing)
