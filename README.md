# ⚡🔄🛂 Gauntlet

Gauntlet is a two-agent adversarial loop that infers software correctness by observing how code behaves under sustained, targeted attack. It's designed as quality control for a dark factory environment — where code is written by bots and verified by attack.

The name comes from "running the gauntlet": a challenge where you must survive a sustained barrage from all sides. Here, the Inspector drives the system under test through escalating tiers of adversarial pressure until hidden failure modes become detectable — then gates promotion on whether any signal came through.

AI-written code can look correct — following conventions, passing linting, reading plausibly — while hiding behavioral failures that only surface under real use. Traditional tests don't catch this because the same agent that wrote the code also wrote the tests, sharing the same blind spots. Gauntlet is built for this: the Inspector assumes the code is broken and generates plans the code author never considered, and the `blockers` in each Weapon are never shown to the Attacker, preserving a real train/test split that prevents the agent from inadvertently writing code that passes by knowing what the tests check.

> An **Attacker** uses a **Weapon** aimed at a **Target** to generate **Plans**. A **Drone** executes those Plans as a **User**. An **Inspector** watches and surfaces **Findings**. Hidden **Vitals** — externally observable truths about expected system behavior — are checked independently to produce a **Clearance**.

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

Two valid configurations:

- **Cross-provider (recommended for CI and standalone runs).** Attacker and Inspector on different providers. Model diversity reduces shared blind spots. This is the default posture.
- **Single-provider (recommended for agentic-loop consumers).** Both roles on the same provider. Appropriate when the consumer is an orchestrator running against a single subscription (e.g., a dark-factory-style pipeline running entirely within one Claude Code auth context). Simpler integration, one credential, one MCP surface. Trades some blind-spot coverage for integration simplicity.

### CLI

```
gauntlet [url] [--config FILE] [--arsenal FILE] [--weapon FILE_OR_DIR] [--target FILE_OR_DIR] [--openapi FILE] [--users FILE] [--threshold N] [--no-fail-fast] [--format yaml|json]
```

| Argument | Default | Description |
|---|---|---|
| `url` | from config or required | Base URL of the running API |
| `--config` | `.gauntlet/config.yaml` | Path to a YAML config file; CLI flags override config values |
| `--arsenal` | none | Path to an Arsenal YAML file (a named collection of weapons) |
| `--weapon` | `.gauntlet/weapons` | Path to a [Weapon YAML](#weapons) file, or a directory of YAML files (one weapon per file) |
| `--target` | `.gauntlet/targets` | Path to a [Target YAML](#targets) file, or a directory of YAML files (one target per file) |
| `--openapi` | none | Path to an OpenAPI 3.x YAML/JSON spec; auto-generates Target objects |
| `--users` | `.gauntlet/users.yaml` | Path to an [users YAML](#user-authentication) file |
| `--threshold` | `0.90` | Holdout satisfaction score required to recommend merge |
| `--fail-fast` / `--no-fail-fast` | enabled | Stop at the first critical finding; use `--no-fail-fast` to run all iterations |
| `--format` | `yaml` | Output format for the run report: `yaml` or `json` |

```bash
gauntlet http://localhost:8000
gauntlet http://localhost:8000 --no-fail-fast
gauntlet http://localhost:8000 --openapi openapi.yaml
gauntlet http://localhost:8000 --arsenal .gauntlet/arsenal.yaml
gauntlet http://localhost:8000 --format json
gauntlet --config .gauntlet/config.yaml
```

Output defaults to YAML; pass `--format json` for machine-friendly JSON:

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

A Weapon defines a reusable attack strategy. The `blockers` are the Weapon's **Vitals** — externally observable truths about expected system behavior — never shown to the Attacker, preserving the train/test separation.

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

These projects occupy the same space — adversarial testing of running services.

### [RESTler](https://github.com/microsoft/restler-fuzzer)

Stateful REST API fuzzer from Microsoft Research. RESTler generates and executes sequences of HTTP requests against a live service, inferring producer-consumer dependencies between endpoints from the OpenAPI spec to explore deep service states.

Shared ground: attacks a **running HTTP server** with **multi-step request sequences**, finds bugs that only manifest through specific request orderings, and checks for both security and reliability failures.

Architectural divergence: RESTler uses grammar-based fuzzing derived from the OpenAPI spec, not LLM reasoning. Validation is hardcoded checkers (status codes, schema conformance), not an Inspector that reasons about what looks suspicious. There is no train/test split — all validation rules are visible to the generation logic. Output is boolean pass/fail per sequence, not a probabilistic confidence score.

### [Schemathesis](https://github.com/schemathesis/schemathesis)

Property-based API testing built on the Hypothesis framework. Generates thousands of test cases from OpenAPI/GraphQL schemas and executes them against a live API to find crashes, schema violations, and stateful workflow bugs.

Shared ground: tests a **live running API**, supports **stateful multi-step workflows** where earlier requests create resources consumed by later ones, and is deliberately **adversarial** — generating edge cases, boundary conditions, and invalid inputs to break the API.

Architectural divergence: generation is algorithmic (property-based testing), not LLM-driven. There is no Attacker/Inspector separation — generation and validation are unified. No hidden blockers or train/test split. Results are deterministic pass/fail, not probabilistic confidence.

### [ToolFuzz](https://github.com/eth-sri/ToolFuzz)

LLM-powered fuzzer from ETH Zurich that generates natural-language test prompts and executes them against LLM agent tools, detecting both runtime crashes and semantic correctness failures.

Shared ground: **uses LLMs to generate adversarial inputs** and has **separate generation and evaluation phases** — prompts are generated, executed against the target, and then an LLM judges whether outputs are semantically correct. This is the closest architectural parallel to the Attacker/Drone/Inspector pipeline.

Architectural divergence: targets LLM agent tools (LangChain, Composio) rather than arbitrary HTTP APIs. No hidden blockers or train/test split — the evaluator sees all context. Attacks are individual prompts, not multi-step chained API call sequences. No probabilistic confidence scoring.
