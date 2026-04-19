# Architecture

## Operating context

Gauntlet runs exclusively as an MCP server inside a Claude Code session. There is no CLI, no GitHub-Actions entry point, no standalone invocation. The host Claude Code agent is the Attacker and the Inspector; Gauntlet provides the deterministic primitives.

Gauntlet does not call any LLM itself and requires no Anthropic/OpenAI credentials. The host already holds its own auth; Gauntlet just runs the deterministic pieces it is asked to run.

## Module map

```
gauntlet/
├── models.py    # Pydantic data models - the shared vocabulary with the host
│                #   (Action/Observation wrap HttpRequest/HttpResponse for
│                #   surface-agnostic execution; HoldoutResult wraps an
│                #   ExecutionResult with the blocker it tested)
├── auth.py      # user authentication config (BearerAuth, ApiKeyAuth, UsersConfig)
├── openapi.py   # OpenAPI 3.x spec parser - produces Target objects
├── roles.py     # WeaponAssessor protocol + DemoWeaponAssessor
├── adapters/    # Adapter protocol + concrete implementations
│   ├── __init__.py   # Adapter protocol (send + execute)
│   ├── http.py       # HttpApi (real HTTP) + InMemoryHttpApi (demo)
│   ├── cli.py        # CliAdapter (stub)
│   └── webdriver.py  # WebDriverAdapter (stub)
├── executor.py  # Drone - runs plans via Adapter.execute(Action) → Observation
├── loop.py      # build_default_iteration_specs + build_risk_report helpers
├── runs.py      # RunStore - per-run iteration + holdout buffer (filesystem)
├── store.py     # PlanStore and FindingsStore - disk-backed knowledge indexed by weapon ID
├── schemas/     # JSON Schema files for weapon / target / users / arsenal
└── server.py    # FastMCP server exposing the gauntlet tools
```

Dependency order:

```
models  ←  auth
models  ←  adapters (http, cli, webdriver, __init__)
models  ←  openapi
models  ←  roles
models  ←  store
models  ←  runs
models + adapters  ←  executor
models  ←  loop
models + auth + openapi + roles + executor + loop + adapters + runs  ←  server
```

Nothing imports from `server.py`. The MCP entry point (`main()` in `server.py`) runs `FastMCP.run()` which speaks stdio to the Claude Code process that launched it.

### Plugin layout

```
.claude-plugin/plugin.json       # MCP server registration + plugin manifest
agents/                          # per-role subagent definitions
├── gauntlet-attacker.md
├── gauntlet-inspector.md
└── gauntlet-holdout-evaluator.md
skills/                          # host-side skills
├── gauntlet/SKILL.md            # the Orchestrator loop
└── gauntlet-author/SKILL.md     # spec → weapons authoring skill
```

The skills are pure prose (no executable code); they encode role discipline that the host follows when dispatching MCP calls and subagents.

## MCP tool surface

| Tool | Returns | Side effect |
|---|---|---|
| `list_weapons(weapons_path, arsenal_path)` | `list[WeaponBrief]` (no blockers) | reads YAML from disk |
| `get_weapon(weapon_id, ...)` | `Weapon` (with blockers) | reads YAML from disk |
| `list_targets(targets_path, openapi_path)` | `list[Target]` | reads YAML / OpenAPI spec from disk |
| `execute_plan(url, plan, users_path)` | `ExecutionResult` | sends real HTTP requests to the SUT |
| `assess_weapon(weapon_id, target, ...)` | `WeaponAssessment` | reads YAML from disk |
| `start_run(weapon_ids, runs_path)` | `{run_id}` | creates `runs_path/<run_id>/` |
| `record_iteration(run_id, weapon_id, iteration_record, runs_path)` | `{status: ok}` | appends one `IterationRecord` to the buffer |
| `read_iteration_records(run_id, weapon_id, runs_path)` | `list[IterationRecord]` | reads from the buffer |
| `record_holdout_result(run_id, weapon_id, holdout_result, runs_path)` | `{status: ok}` | appends one `HoldoutResult` to the buffer |
| `read_holdout_results(run_id, weapon_id, runs_path)` | `list[HoldoutResult]` | reads from the buffer |
| `assemble_run_report(run_id, weapon_id, ... \| iterations, holdout_results, threshold)` | `dict` with `risk_report` + `clearance` | reads from the buffer (or accepts explicit lists) |
| `assemble_final_clearance(run_id, clearance_threshold, weapon_ids?)` | `FinalClearance` | reads every per-weapon report from the buffer and aggregates |
| `default_iteration_specs()` | `list[IterationSpec]` | none |

### Run-scoped buffer

`start_run` initializes a per-run filesystem buffer under `runs_path/<run_id>/`
(default `.gauntlet/runs/<run_id>/`). Each weapon gets its own subdirectory
with two append-only JSONL files: `iterations.jsonl` (one `IterationRecord`
per line) and `holdouts.jsonl` (one `HoldoutResult` per line). `record_*`
calls append; `read_*` calls read the whole file. JSONL is chosen so that
multiple subagent processes — possibly fronted by separate Claude Code
sessions — can append concurrently without coordinating on a lock.

The buffer is short-lived: one run, one host session. Nothing depends on
state surviving across runs. If a run crashes, restart from `start_run`.

`record_iteration` rejects any `IterationRecord` whose findings carry a
non-null `violated_blocker`. The Inspector context never sees blocker text,
so a populated `violated_blocker` would mean a train/test split violation;
the schema enforces this at the buffer boundary.

## Train/test split

The split is enforced at two layers:

1. **MCP-tool allowlists on per-role subagents.** The plugin ships three subagent definitions in `agents/`:
   - `gauntlet-attacker` — allowlist excludes `get_weapon`, `read_holdout_results`, `record_holdout_result`. Can read its own prior plans + Inspector findings via `read_iteration_records`.
   - `gauntlet-inspector` — allowlist excludes `get_weapon`, `read_holdout_results`, `record_holdout_result`, and even the SUT-execution tools. Reads execution results via the iteration buffer; emits findings via `record_iteration`.
   - `gauntlet-holdout-evaluator` — allowlist includes `get_weapon` and `record_holdout_result`. Excludes `read_iteration_records` so prior Attacker/Inspector traces cannot leak in. Runs from fresh context per weapon.

   These allowlists are enforced by Claude Code's permission layer before the MCP server sees a call; a subagent that tries to use a forbidden tool fails at the permission check. This is structural enforcement of the split, not prompt discipline.

2. **Schema enforcement at the buffer.** `record_iteration` rejects any `IterationRecord` whose findings carry a non-null `violated_blocker`. The Inspector never sees blocker text, so a populated value would mean a contamination event.

The Orchestrator role (the host skill itself) retains every tool but is responsible for not paraphrasing blockers back into Attacker/Inspector dispatch prompts. That is the only remaining discipline-level rule, and it is bounded — the Orchestrator only reads `get_weapon` output if it explicitly asks for it, which it should not need to do.


## Host-driven loop shape

```
(Orchestrator: host agent in a Claude Code session, runs the gauntlet skill)
│
├── list_weapons() → pick weapons
│   list_targets() → pick targets
│   assess_weapon(id, target) → optional preflight
│   default_iteration_specs() → reference ladder
│   start_run(weapon_ids=[...]) → run_id
│
├── For each weapon, for each iteration spec (typically 4):
│   ├── dispatch gauntlet-attacker subagent (run_id, weapon_id, spec, url)
│   │     → composes plans, executes them, appends IterationRecord
│   │
│   └── dispatch gauntlet-inspector subagent (run_id, weapon_id, spec)
│         → reads buffer, emits Findings, appends IterationRecord (findings only)
│
├── For each weapon, dispatch gauntlet-holdout-evaluator subagent (run_id, weapon_id, url)
│     → fresh context, reads weapon blockers, derives acceptance plans,
│       executes them, appends one HoldoutResult per blocker
│
└── For each weapon: assemble_run_report(run_id, weapon_id) → RiskReport + Clearance
```

## Deterministic vs non-deterministic segments

**Deterministic (no network, no LLM):**

- `InMemoryHttpApi` - in-memory REST API with three seeded flaws: (1) PATCH without ownership check, (2) POST accepts invalid data types for title and missing required fields, (3) GET /tasks leaks all tasks regardless of ownership. Ships with the library as a working example SUT.
- `Drone` - resolves path templates, calls the adapter, evaluates assertions.
- Assertion evaluation, risk-report assembly, weapon assessment - all pure Python.

**Non-deterministic (network):**

- `HttpApi` - sends real HTTP requests; outcome depends on the running server.

The host itself is non-deterministic (it's an LLM agent), but Gauntlet doesn't run the host. Gauntlet's own code is deterministic end-to-end.

## Design decisions

**Why MCP only?** Gauntlet's consumer is the dark-factory pipeline, which runs inside Claude Code. Keeping CLI + MCP + library surfaces in parallel multiplied integration cost without adding value for the one consumer that actually uses it. MCP is the one surface that lets the host drive Gauntlet as a tool inside its own loop.

**Why Pydantic?** All interchange objects are `BaseModel` subclasses with `extra="forbid"`. This catches schema drift early and makes JSON serialization/deserialization free - including over the MCP tool boundary.

**Why Protocols instead of ABCs?** Structural subtyping lets callers pass any object that has the right methods without importing from `gauntlet`. Only `WeaponAssessor` remains as a protocol now that Attacker/Inspector are host-driven.

**Why separate auth.py?** User credentials involve secret resolution from env vars. Isolating this in `auth.py` keeps the rest of the codebase free of secret-handling logic.

**Why Action/Observation instead of passing HttpRequest/HttpResponse directly?** The adversarial loop should not be coupled to a single execution surface. Action wraps an HttpRequest today and will wrap CLI commands or WebDriver interactions in the future; Observation wraps the corresponding response. The Drone converts between the two layers.

**Why host-driven Attacker/Inspector?** Because Gauntlet runs inside Claude Code, the host already has an LLM ready to play both roles. Re-invoking a separate Anthropic or OpenAI client from Gauntlet's own process would require credentials Gauntlet doesn't have a clean way to acquire, and would duplicate reasoning capacity the host already provides.

**Why Arsenals?** Individual weapons test one property at a time, which is the right granularity for authoring and debugging. An Arsenal groups related weapons under one YAML file so the host can select an entire attack class (authorization, input validation, OWASP top-10) as a unit.
