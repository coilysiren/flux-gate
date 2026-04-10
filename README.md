# Flux Gate

Flux Gate is a two-agent adversarial loop that infers software correctness by observing how code behaves under sustained, targeted attack, and is designed as quality control for a dark factory environment.

## Quick start

```bash
git clone git@github.com:coilysiren/flux-gate.git
cd flux-gate
docker compose run --rm demo
```

That starts the demo API and runs `flux-gate` against it.

## Installation

To use Flux Gate against your own API:

```bash
pip install flux-gate
# or: uv add flux-gate
```

Then point it at your locally-running service:

```bash
flux-gate http://localhost:8000
```

Output is YAML:

```yaml
system_under_test: localhost:8000
environment: local
iterations:
  - spec:
      index: 1
      name: broad_baseline
      ...
risk_report:
  confidence_score: 0.06
  risk_level: critical
  confirmed_failures:
    - unauthorized_cross_user_modification
  coverage:
    - GET /tasks/1
    - PATCH /tasks/1
    - POST /tasks
  conclusion: >-
    System fails under adversarial pressure and should not be promoted
    without remediation.
```

## Usage

### CLI

```
flux-gate <url> [--name NAME] [--env ENV]
```

| Argument | Default | Description |
|---|---|---|
| `url` | required | Base URL of the running API |
| `--name` | URL hostname | Label for the system under test in the report |
| `--env` | `local` | Environment label (e.g. `staging`, `ci`) |

```bash
flux-gate http://localhost:8000
flux-gate http://localhost:8000 --name "Task API" --env staging
```

### Actor authentication

Flux Gate runs scenarios as named actors (e.g. `userA`, `userB`). By default it
passes the actor name in an `X-Actor` header. To use real credentials, pass
`actor_headers` when constructing `HttpExecutor` directly:

```python
from flux_gate import DeterministicLocalExecutor, HttpExecutor, FluxGateRunner
from flux_gate.roles import DemoAdversary, DemoOperator

runner = FluxGateRunner(
    executor=DeterministicLocalExecutor(
        HttpExecutor(
            "http://localhost:8000",
            actor_headers={
                "userA": {"Authorization": "Bearer token-a"},
                "userB": {"Authorization": "Bearer token-b"},
            },
        )
    ),
    operator=DemoOperator(),
    adversary=DemoAdversary(),
)
run = runner.run()
```

### Custom Operator and Adversary

Implement either protocol to plug in an LLM-backed agent:

```python
from flux_gate import (
    ExecutionResult,
    Finding,
    FluxGateRunner,
    IterationRecord,
    IterationSpec,
    Scenario,
)

class LLMOperator:
    def generate_scenarios(
        self, spec: IterationSpec, previous_iterations: list[IterationRecord]
    ) -> list[Scenario]:
        # call your LLM, parse response into Scenario objects
        ...

class LLMAdversary:
    def analyze(
        self, spec: IterationSpec, execution_results: list[ExecutionResult]
    ) -> list[Finding]:
        # call your LLM, parse response into Finding objects
        ...
```

### Reading the report

```python
run = runner.run()

print(run.risk_report.risk_level)         # "low" | "medium" | "high" | "critical"
print(run.risk_report.confidence_score)   # float 0.0–1.0
print(run.risk_report.confirmed_failures) # list of issue identifiers
print(run.risk_report.coverage)           # ["GET /tasks/1", "PATCH /tasks/1", ...]

for iteration in run.iterations:
    for finding in iteration.findings:
        print(finding.severity, finding.issue, finding.rationale)
```

## Core Model

Flux Gate treats code change correctness as a problem of behavioral observation while under attack.

* Code is assumed to be untrusted, potentially written but a human - but designed to be written by a bot
* Tests are generated dynamically
* Confidence emerges from what survives adversarial probing

It asks: "How hard did we try to break this, and what happened when we did?"

## The Two Roles (Personified)

### The Operator (ChatGPT)

Explores the execution sphere

* Constructs plausible, production-like scenarios
* Simulates how the system will actually be used (and misused)
* Explores workflows, edge cases, and state transitions
* Adapts based on what has already been tested

The Operator is not trying to prove correctness, It is trying to create situations where correctness might fail.

### The Adversary (Claude)

Applies intelligent pressure

* Analyzes execution results for weaknesses
* Identifies suspicious passes and untested assumptions
* Forms hypotheses about hidden failure modes
* Forces the next round of scenarios toward likely breakpoints

The Adversary assumes "This system is broken. I just haven’t proven it yet."

### Dynamic Between Them

* The Operator explores
* The Adversary sharpens
* Execution grounds both

Together, they perform a form of guided adversarial search over the space of possible failures

## Concrete Flow (v0, fixed 4 iterations)

```yaml
flux_gate_run:
  system_under_test: REST API
  environment: deterministic_local

  roles:
    operator: ChatGPT
    Adversary: Claude

  loop:
    iterations: 4

    iteration_1:
      goal: broad_baseline
      operator: generate diverse CRUD + lifecycle scenarios
      execute: run scenarios
      Adversary: identify anomalies and weak coverage

    iteration_2:
      goal: boundary_and_invariants
      operator: target edge cases, missing fields, schema drift
      execute: run refined scenarios
      Adversary: escalate invariant violations

    iteration_3:
      goal: adversarial_misuse
      operator: simulate auth violations, invalid transitions, cross-user access
      execute: run attack scenarios
      Adversary: identify security and logic failures

    iteration_4:
      goal: targeted_followup
      operator: focus only on suspicious areas
      execute: confirm and expand blast radius
      Adversary: finalize failure model

  output:
    confidence_score: probabilistic
    risk_profile:
      - confirmed_failures
      - suspicious_patterns
      - unexplored_surfaces
```

### Scenario Shape (Example)

```yaml
scenario:
  name: user_cannot_modify_other_users_task
  category: authz

  steps:
    - actor: userA
      request:
        method: POST
        path: /tasks
        body:
          title: "private task"

    - actor: userB
      request:
        method: PATCH
        path: /tasks/{id}
        body:
          completed: true

    - actor: userA
      request:
        method: GET
        path: /tasks/{id}

  assertions:
    - type: status_code
      expected: 403

    - type: invariant
      rule: task_not_modified_by_other_user
```

### Execution Output (Example)

```yaml
execution_result:
  scenario: user_cannot_modify_other_users_task

  steps:
    - step: 1
      status: 201

    - step: 2
      status: 200   # suspicious

    - step: 3
      status: 200
      body:
        completed: true

  assertions:
    - name: unauthorized_patch_blocked
      result: fail
```

### Adversary Finding (Example)

```yaml
finding:
  issue: unauthorized_cross_user_modification
  severity: critical
  confidence: 0.94

  rationale: >
    userB successfully modified userA's resource.
    violation reproduced deterministically.

  next_targets:
    - ownership mutation
    - list endpoint visibility
```

### Final Output

```yaml
risk_report:
  confidence_score: 0.27
  risk_level: critical

  summary:
    - cross-user write vulnerability
    - destructive patch semantics
    - invariant violations under partial updates

  coverage:
    endpoints_tested:
      - POST /tasks
      - PATCH /tasks/{id}
      - GET /tasks/{id}

  conclusion: >
    system fails under moderate adversarial pressure.
    not safe for production deployment.
```

## What Makes This Different

Flux Gate is not:

* a test runner
* a code reviewer
* a fuzzing tool

It is an adversarial inference engine for software correctness

It combines:

* dynamic scenario generation (like red teaming)
* execution grounding (like CI)
* adversarial refinement (like security testing)
