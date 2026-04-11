# ⚡🔄🛂 Gauntlet

Gauntlet is a two-agent adversarial loop that infers software correctness by observing how code behaves under sustained, targeted attack. It's designed as quality control for a dark factory environment — where code is written by bots and verified by attack.

The name comes from "running the gauntlet": a challenge where you must survive a sustained barrage from all sides. Here, the Inspector drives the system under test through escalating tiers of adversarial pressure until hidden failure modes become detectable — then gates promotion on whether any signal came through.

AI-written code can look correct — following conventions, passing linting, reading plausibly — while hiding behavioral failures that only surface under real use. Traditional tests don't catch this because the same agent that wrote the code also wrote the tests, sharing the same blind spots. Gauntlet is built for this: the Inspector assumes the code is broken and generates plans the code author never considered, and the `blockers` in each Weapon are never shown to the Attacker, preserving a real train/test split that prevents the agent from inadvertently writing code that passes by knowing what the tests check.

> An **Attacker** uses a **Weapon** aimed at a **Target** to generate **Plans**. A **User** performs those Plans using a **Drone**. An **Inspector** watches and surfaces **Findings**. This produces a **Vitals** readout that's checked against the **Blockers** to determine the **Clearance**.

## Quick start

Set your LLM credentials, then point Gauntlet at a running API:

```bash
export GAUNTLET_ATTACKER_TYPE=openai
export GAUNTLET_ATTACKER_KEY=sk-...
export GAUNTLET_INSPECTOR_TYPE=anthropic
export GAUNTLET_INSPECTOR_KEY=sk-ant-...

git clone git@github.com:coilysiren/gauntlet.git
cd gauntlet
docker compose run --rm demo
```

That starts the demo API and runs the full adversarial loop against it.

## Installation

```bash
pip install gauntlet
# or: uv add gauntlet
```

## Usage

For workflow guidance (when to run, how to integrate, how to act on results), see [docs/usage.md](docs/usage.md).

### LLM configuration

Gauntlet requires one LLM for the Attacker role and one for the Inspector role. Configure
each with a pair of environment variables:

| Variable | Description |
|---|---|
| `GAUNTLET_ATTACKER_TYPE` | LLM provider for the Attacker: `openai` or `anthropic` |
| `GAUNTLET_ATTACKER_KEY` | API key for the Attacker's provider |
| `GAUNTLET_INSPECTOR_TYPE` | LLM provider for the Inspector: `openai` or `anthropic` |
| `GAUNTLET_INSPECTOR_KEY` | API key for the Inspector's provider |

The default models are `gpt-4o` for OpenAI and `claude-opus-4-5` for Anthropic.
Using different providers for each role is intentional — model diversity reduces blind spots.

### CLI

```
gauntlet <url> [--weapon FILE_OR_DIR] [--target FILE_OR_DIR] [--users FILE] [--threshold N] [--no-fail-fast]
```

| Argument | Default | Description |
|---|---|---|
| `url` | required | Base URL of the running API |
| `--weapon` | `.gauntlet/weapons` | Path to a [Weapon YAML](#weapons) file, or a directory of YAML files (one weapon per file) |
| `--target` | `.gauntlet/targets` | Path to a [Target YAML](#targets) file, or a directory of YAML files (one target per file) |
| `--users` | `.gauntlet/users.yaml` | Path to an [users YAML](#user-authentication) file |
| `--threshold` | `0.90` | Holdout satisfaction score required to recommend merge |
| `--fail-fast` / `--no-fail-fast` | enabled | Stop at the first critical finding; use `--no-fail-fast` to run all iterations |

```bash
gauntlet http://localhost:8000
gauntlet http://localhost:8000 --no-fail-fast
gauntlet http://localhost:8000 --weapon /path/to/weapons/ --target /path/to/targets/ --users /path/to/users.yaml
gauntlet http://localhost:8000 --weapon /path/to/single_weapon.yaml --target /path/to/single_target.yaml
```

Output is YAML:

```yaml
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

Place your Gauntlet config files in a `.gauntlet/` directory at the root of your project.
The CLI discovers them automatically — no flags needed for the common case:

```
your-project/
├── .gauntlet/
│   ├── weapons/            # one YAML file per Weapon — all loaded automatically
│   │   ├── task_ownership.yaml
│   │   └── task_read_isolation.yaml
│   ├── targets/            # one YAML file per Target — all loaded automatically
│   │   └── task_endpoints.yaml
│   └── users.yaml         # User auth — loaded automatically if present
└── ...
```

Override any path with `--weapon FILE_OR_DIR`, `--target FILE_OR_DIR`, or `--users FILE`.

### Weapons

A Weapon defines a reusable attack strategy. The `blockers` are never shown to the Attacker —
only to the holdout evaluator — preserving the train/test separation.

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

### Targets

A Target defines the API surface a Weapon is tested against. One target per YAML file.
Point multiple targets at the same weapon to test the same attack across different API surfaces.

```yaml
# .gauntlet/targets/task_endpoints.yaml
title: Task ownership endpoints
endpoints:
  - POST /tasks
  - PATCH /tasks/{id}
  - GET /tasks/{id}
```

### User authentication

Create `.gauntlet/users.yaml` to provide per-user credentials. Secret values are
never stored in the file — each entry names an environment variable that holds the
actual credential. Users omitted from the file fall back to the default `X-User: <name>` header.

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

Supported authentication types:

| Type | Fields | Header sent |
|---|---|---|
| `bearer` | `token_env` | `Authorization: Bearer <$token_env>` |
| `api_key` | `header`, `key_env` | `<header>: <$key_env>` |

## Core Model

Gauntlet treats code change correctness as a problem of behavioral observation while under attack.

* Code is assumed to be untrusted, potentially written but a human - but designed to be written by a bot
* Tests are generated dynamically
* Confidence emerges from what survives adversarial probing

It asks: "How hard did we try to break this, and what happened when we did?"

## The Two Roles

### The Attacker

Explores the execution space

* Constructs plausible, production-like plans
* Simulates how the system will actually be used (and misused)
* Explores workflows, edge cases, and state transitions
* Adapts based on what has already been tested

The Attacker is not trying to prove correctness. It is trying to create situations where correctness might fail.

### The Inspector

Applies intelligent pressure

* Analyzes execution results for weaknesses
* Identifies suspicious passes and untested assumptions
* Forms hypotheses about hidden failure modes
* Forces the next round of plans toward likely breakpoints

The Inspector assumes "This system is broken. I just haven't proven it yet."

### Dynamic Between Them

* The Attacker explores
* The Inspector sharpens
* Execution grounds both

Together, they perform a form of guided adversarial search over the space of possible failures.

## What Makes This Different

Gauntlet is not:

* a test runner
* a code reviewer
* a fuzzing tool

It is an adversarial inference engine for software correctness.

It combines:

* dynamic plan generation (like red teaming)
* execution grounding (like CI)
* adversarial refinement (like security testing)

## Prior Art

These projects informed Gauntlet's design.

### [StrongDM Software Factory](https://factory.strongdm.ai/)

Production dark factory — code is written and reviewed entirely by agents.

Key architectural ideas adopted: **satisfaction metrics** (probabilistic 0–1 scores, not boolean pass/fail) and the principle that **plans live outside the codebase** to prevent reward-hacking (Gauntlet uses `.gauntlet/spec.yaml`).

Architectural divergence: the Software Factory maintains a Digital Twin Universe — behavioral clones of third-party services that agents test against without hitting real infrastructure. Gauntlet has no twin layer; it requires a running HTTP server and sends real requests. The Software Factory is also a full code-generation pipeline; Gauntlet is only a verifier. It verifies code already written, it does not write code.

### [OctopusGarden](https://github.com/foundatron/octopusgarden)

Open-source autonomous development platform with a convergence-based loop.

Key architectural ideas adopted: **stratified plan difficulty** (Gauntlet's four tiers: baseline → boundary → adversarial → targeted) and the **attractor loop** concept — iterating until a threshold is met (Gauntlet's `--threshold` flag).

Architectural divergence: OctopusGarden's loop is dynamic — it runs until satisfaction converges, with stall recovery via high-temperature "wonder" phases and model escalation (cheap model first, premium model after non-improving iterations). Gauntlet's loop is fixed-depth: four tiers, one pass. There is no stall detection, no wonder phase, and no model escalation. The tradeoff is predictability and cost over adaptive thoroughness.

### [Fabro](https://github.com/fabro-sh/fabro)

Open-source dark factory orchestrator with a graph-based workflow engine.

Key architectural ideas adopted: **human-in-the-loop gates** — Fabro's hexagon gates are checkpoints where a human can block promotion. Gauntlet's `Clearance` plays the same role, but the decision is made by the Inspector LLM rather than a human.

Architectural divergence: Fabro models workflows as directed graphs (Graphviz DOT files, version-controlled alongside code). Gauntlet has no graph engine — its pipeline is a fixed linear sequence of iterations. Fabro also supports multi-model routing via CSS-like stylesheets and per-stage Git checkpointing. Gauntlet has neither: model assignment is static (one Attacker, one Inspector) and there is no checkpointing between iterations.
