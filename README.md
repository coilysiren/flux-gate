# ⚡🔄🛂 Gauntlet

Gauntlet is a two-role adversarial MCP server that infers software correctness by observing how code behaves under sustained, targeted attack. It's designed as quality control for a dark-factory environment - where code is written by bots and verified by attack.

The name comes from "running the gauntlet": a challenge where you must survive a sustained barrage from all sides. Here, the host Claude Code agent drives the system under test through escalating tiers of adversarial pressure until hidden failure modes become detectable - then gates promotion on whether any signal came through.

AI-written code can look correct - following conventions, passing linting, reading plausibly - while hiding behavioral failures that only surface under real use. Traditional tests don't catch this because the same agent that wrote the code also wrote the tests, sharing the same blind spots. Gauntlet is built for this: the host's Attacker context assumes the code is broken and generates plans the code author never considered, and the `blockers` in each Weapon are never loaded into that context, preserving a real train/test split that prevents the agent from inadvertently writing code that passes by knowing what the tests check.

> An **Attacker** uses a **Weapon** aimed at a **Target** to generate **Plans**. Gauntlet's Drone executes those Plans as a **User**. An **Inspector** watches and surfaces **Findings**. Hidden **Vitals** - externally observable truths about expected system behavior - are checked independently to produce a **Clearance**.

## Operating model

Gauntlet runs **exclusively as an MCP server inside Claude Code**. There is no CLI, no remote CI mode, and no standalone invocation path. The host Claude Code agent plays the Attacker and Inspector roles itself - two prompt contexts it drives in its own loop - and calls Gauntlet's MCP tools for the deterministic pieces: config loading, plan execution against the SUT, weapon assessment, and risk-report assembly.

Because Gauntlet runs inside a Claude Code session, no Anthropic credentials are needed - the host already has auth.

## Install

Gauntlet ships as a Claude Code plugin that bundles the MCP server and the host [skill](skills/gauntlet/SKILL.md) into one install:

```bash
claude plugin install https://github.com/coilysiren/gauntlet
```

Or, for local development against a clone:

```bash
git clone https://github.com/coilysiren/gauntlet
cd your-project
claude --plugin-dir path/to/gauntlet
```

On first invocation, `uv` auto-resolves the Python dependencies for the MCP server. Confirm the plugin is discovered with `/mcp` (for the server) and by trying a trigger phrase like "run gauntlet" (for the skill) inside Claude Code.

### What you get

- **MCP server `gauntlet`** — the deterministic tools listed below.
- **Skill `gauntlet`** — auto-loads on trigger phrases ("run gauntlet", "adversarial test", "check before merging") and walks the host through the role-disciplined loop as the Orchestrator.
- **Skill `gauntlet-author`** — auto-loads on trigger phrases ("author weapons from this spec", "generate gauntlet weapons", "propose weapons for this API") and translates a product spec into Weapon YAMLs in `.gauntlet/weapons/`.
- **Subagents `gauntlet-attacker`, `gauntlet-inspector`, `gauntlet-holdout-evaluator`** — per-role definitions with MCP-tool allowlists that enforce the train/test split at the permission layer. The Orchestrator dispatches them; they cannot reach the tools their role is forbidden from using.

Without the skill, a host could still call the MCP tools ad-hoc, but it would have to re-derive the loop every time and would be far more likely to collapse the train/test split. The plugin delivery is what makes the four pieces stay in sync.

## MCP tools

| Tool | Purpose | Allowed in role |
|---|---|---|
| `list_weapons(weapons_path)` | List attacker-safe `WeaponBrief`s (no blockers) | Orchestrator, Attacker |
| `get_weapon(weapon_id, weapons_path)` | Return full weapon including blockers | Orchestrator, HoldoutEvaluator |
| `execute_plan(url, plan, user_headers)` | Deterministically run a `Plan` against the SUT | Orchestrator, Attacker, HoldoutEvaluator |
| `start_run(weapon_ids)` | Initialize a per-run iteration + holdout buffer; returns an opaque `run_id` | Orchestrator |
| `record_iteration(run_id, weapon_id, iteration_record)` | Append an `IterationRecord` to the run buffer (rejects findings that carry blocker text) | Attacker, Inspector |
| `read_iteration_records(run_id, weapon_id)` | Read prior `IterationRecord`s for one weapon in this run | Attacker, Inspector |
| `record_holdout_result(run_id, weapon_id, holdout_result)` | Append a `HoldoutResult` to the run buffer | HoldoutEvaluator |
| `read_holdout_results(run_id, weapon_id)` | Read prior `HoldoutResult`s for one weapon in this run | Orchestrator |
| `assemble_run_report(run_id, weapon_id)` | Build per-weapon `RiskReport` + `Clearance` | Orchestrator |
| `assemble_final_clearance(run_id, clearance_threshold)` | Aggregate every per-weapon report in the run into one overall `FinalClearance` (pass / conditional / block) | Orchestrator, HoldoutEvaluator |

The train/test split is enforced at the permission layer via MCP-tool allowlists on each per-role subagent — see the [`agents/`](agents/) directory. The Attacker subagent literally cannot call `get_weapon`, the Inspector subagent cannot call `get_weapon` or read holdout results, and the HoldoutEvaluator subagent cannot read the iteration buffer.

## Project config directory

Gauntlet reads configuration from a `.gauntlet/` directory at the root of the project:

```
your-project/
├── .gauntlet/
│   └── weapons/           # one YAML file per Weapon
│       ├── task_ownership.yaml
│       └── task_read_isolation.yaml
└── ...
```

Every MCP tool that reads config accepts an override path argument.

### Weapons

A Weapon defines a reusable attack strategy. The `blockers` are the Weapon's **Vitals** - externally observable truths about expected system behavior - never surfaced in the Attacker context, preserving the train/test separation.

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

### User authentication

If the SUT requires authentication, the orchestrator passes `user_headers` to `execute_plan`: a `dict[str, dict[str, str]]` mapping user names to per-user request headers, e.g. `{"alice": {"Authorization": "Bearer ..."}}`. Users without an entry fall back to the default `X-User: <name>` header.

## Core Model

Gauntlet treats code change correctness as a problem of behavioral observation while under attack.

- Code is assumed untrusted, potentially written by a human but designed to be written by a bot
- Tests are generated dynamically by the host agent
- Confidence emerges from what survives adversarial probing

It asks: "How hard did we try to break this, and what happened when we did?"

## The Two Roles

### The Attacker

Explores the execution space.

- Constructs plausible, production-like plans
- Simulates how the system will actually be used (and misused)
- Explores workflows, edge cases, and state transitions
- Adapts based on what has already been tested

The Attacker is not trying to prove correctness. It is trying to create situations where correctness might fail.

### The Inspector

Applies intelligent pressure.

- Analyzes execution results for weaknesses
- Identifies suspicious passes and untested assumptions
- Forms hypotheses about hidden failure modes
- Forces the next round of plans toward likely breakpoints

The Inspector assumes "This system is broken. I just haven't proven it yet."

### Dynamic Between Them

- The Attacker explores
- The Inspector sharpens
- Execution grounds both

Together, they perform a form of guided adversarial search over the space of possible failures.

## What Makes This Different

Gauntlet is not:

- a test runner
- a code reviewer
- a fuzzing tool

It is an adversarial inference engine for software correctness.

It combines:

- dynamic plan generation (like red teaming)
- execution grounding (like CI)
- adversarial refinement (like security testing)

## Prior Art

These projects occupy the same space - adversarial testing of running services. Gauntlet's distinguishing axis is architectural: the reasoning (plan generation, result analysis) lives in a host Claude Code agent driven by a packaged Skill, while execution and report assembly live in an MCP server. The agent never sees the `blockers`; the MCP server never reasons. That separation is what preserves the train/test split against agent-authored code.

### [RESTler](https://github.com/microsoft/restler-fuzzer)

Stateful REST API fuzzer from Microsoft Research. RESTler generates and executes sequences of HTTP requests against a live service, inferring producer-consumer dependencies between endpoints from the OpenAPI spec to explore deep service states.

Shared ground: attacks a **running HTTP server** with **multi-step request sequences**, finds bugs that only manifest through specific request orderings, targets both security and reliability failures.

Architectural divergence: RESTler is a self-contained process - grammar-based generation from the OpenAPI spec, hardcoded validators (status codes, schema conformance), no separation between generator and checker. Gauntlet splits reasoning out into a host agent (which reads the weapon description and invents plans) and keeps execution + report assembly in a deterministic MCP server; there is no internal grammar. RESTler has no train/test split because it has no second reasoning party to withhold invariants from. Output is pass/fail per sequence, not a risk report with a clearance gate.

### [Schemathesis](https://github.com/schemathesis/schemathesis)

Property-based API testing built on the Hypothesis framework. Generates thousands of test cases from OpenAPI/GraphQL schemas and executes them against a live API to find crashes, schema violations, and stateful workflow bugs.

Shared ground: tests a **live running API**, supports **stateful multi-step workflows** where earlier requests create resources consumed by later ones, is deliberately **adversarial**.

Architectural divergence: Schemathesis is algorithmic (property-based testing from schema); Gauntlet is agent-driven (plans composed by a host LLM reasoning about a weapon description). The two are complementary - Schemathesis exhausts the schema space, Gauntlet targets specific invariants under adversarial pressure. Schemathesis has no Attacker/Inspector separation, no hidden blockers, no agent to withhold anything from; results are deterministic pass/fail, not a confidence-scored risk report.

### [ToolFuzz](https://github.com/eth-sri/ToolFuzz)

LLM-powered fuzzer from ETH Zurich that generates natural-language test prompts and executes them against LLM agent tools, detecting both runtime crashes and semantic correctness failures.

Shared ground: **uses LLMs to generate adversarial inputs**, has **separate generation and evaluation phases**. Conceptually the closest parallel to Gauntlet's Attacker/Inspector split.

Architectural divergence: ToolFuzz runs its own LLM client end-to-end (its own credentials, its own reasoning), targets LLM agent tools (LangChain, Composio), and has no train/test split - its evaluator sees all the context its generator used. Gauntlet inverts all three: reasoning is delegated to the host Claude Code agent (Gauntlet holds no credentials), the target is an arbitrary HTTP API, and the host-side Skill enforces strict separation between the Attacker context (weapon description only) and the HoldoutEvaluator context (blockers). Gauntlet attacks are multi-step chained API sequences bound by per-weapon hidden invariants; ToolFuzz attacks are single prompts judged semantically.

### Where Gauntlet sits

The closest comparison - ToolFuzz - is a self-contained LLM-driven fuzzer for agent tools. Gauntlet is the same idea pushed one layer further: instead of bundling its own LLM, it ships as an MCP server + Skill that a Claude Code host drives. This is the right shape for the dark-factory use case, where the host is already running and already has credentials; it also makes the train/test split a host-side prompt discipline rather than a compile-time check, which is the honest framing given that all three parties (Attacker, Inspector, HoldoutEvaluator) are contexts inside one agent.
