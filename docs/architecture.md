# Architecture

## Operating context

Gauntlet runs exclusively as an MCP server inside a Claude Code session. There is no CLI, no GitHub-Actions entry point, no standalone invocation. The host Claude Code agent is the Attacker and the Inspector; Gauntlet provides the deterministic primitives.

Gauntlet does not call any LLM itself and requires no Anthropic/OpenAI credentials. The host already holds its own auth; Gauntlet just runs the deterministic pieces it is asked to run.

## Module map

```
gauntlet/
├── models.py    # Pydantic data models - the shared vocabulary with the host
│                #   (HoldoutResult wraps an ExecutionResult with the blocker
│                #   it tested)
├── http.py      # HttpApi — real HTTP requests via `requests`
├── executor.py  # Drone - runs plans by calling HttpApi.send per step
├── loop.py      # build_risk_report + aggregate_final_clearance helpers
├── runs.py      # RunStore - per-run iteration + holdout buffer (filesystem)
├── _log.py      # Private. JSON stderr logging + log_tool_call contextmgr
└── server.py    # FastMCP server exposing the gauntlet tools
```

Dependency order:

```
models  ←  http
models  ←  runs
models + http  ←  executor
models  ←  loop
_log + models + executor + loop + http + runs  ←  server
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
| `list_weapons(weapons_path)` | `list[dict]` of `{id, title, description}` (no blockers) | reads YAML from disk |
| `get_weapon(weapon_id, weapons_path)` | `Weapon` (with blockers) | reads YAML from disk |
| `execute_plan(url, plan, user_headers)` | `ExecutionResult` | sends real HTTP requests to the SUT |
| `start_run(weapon_ids)` | `{run_id}` | creates `.gauntlet/runs/<run_id>/` |
| `record_iteration(run_id, weapon_id, iteration_record)` | `{status: ok}` | appends one `IterationRecord` to the buffer |
| `read_iteration_records(run_id, weapon_id)` | `list[IterationRecord]` | reads from the buffer |
| `record_holdout_result(run_id, weapon_id, holdout_result)` | `{status: ok}` | appends one `HoldoutResult` to the buffer |
| `read_holdout_results(run_id, weapon_id)` | `list[HoldoutResult]` | reads from the buffer |
| `assemble_run_report(run_id, weapon_id, threshold)` | `dict` with `risk_report` + `clearance` | reads from the buffer |
| `assemble_final_clearance(run_id, clearance_threshold, weapon_ids?)` | `FinalClearance` | reads every per-weapon report from the buffer and aggregates |

### Run-scoped buffer

`start_run` initializes a per-run filesystem buffer under `.gauntlet/runs/<run_id>/`
(resolved against the host's cwd). Each weapon gets its own subdirectory
with two append-only JSONL files: `iterations.jsonl` (one `IterationRecord`
per line) and `holdouts.jsonl` (one `HoldoutResult` per line). `record_*`
calls append; `read_*` calls read the whole file. JSONL is chosen so that
multiple subagent processes — possibly fronted by separate Claude Code
sessions — can append concurrently. On POSIX, each append takes an
`fcntl.flock` to serialize writers and prevent byte interleaving.

On read, corrupt JSONL lines are skipped with a logged warning and tallied
in `RunStore.corrupt_record_counts()`; the host can surface the counts if
it cares about partial buffers. The manifest carries a `schema_version`
field (current value: `gauntlet.runs.SCHEMA_VERSION`) so future layout
changes have something to key off; readers tolerate old buffers that
predate the field.

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
│   start_run(weapon_ids=[...]) → run_id
│   build the inline 4-stage IterationSpec list
│
├── For each weapon, for each iteration spec (4):
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

- `Drone` - resolves path templates, calls the adapter, evaluates assertions.
- Assertion evaluation, risk-report assembly, weapon assessment - all pure Python.

**Non-deterministic (network):**

- `HttpApi` - sends real HTTP requests; outcome depends on the running server.

The host itself is non-deterministic (it's an LLM agent), but Gauntlet doesn't run the host. Gauntlet's own code is deterministic end-to-end.

## Design decisions

**Why MCP only?** Gauntlet's consumer is the dark-factory pipeline, which runs inside Claude Code. Keeping CLI + MCP + library surfaces in parallel multiplied integration cost without adding value for the one consumer that actually uses it. MCP is the one surface that lets the host drive Gauntlet as a tool inside its own loop.

**Why Pydantic?** All interchange objects are `BaseModel` subclasses with `extra="forbid"`. This catches schema drift early and makes JSON serialization/deserialization free - including over the MCP tool boundary.

**Why host-driven Attacker/Inspector?** Because Gauntlet runs inside Claude Code, the host already has an LLM ready to play both roles. Re-invoking a separate Anthropic or OpenAI client from Gauntlet's own process would require credentials Gauntlet doesn't have a clean way to acquire, and would duplicate reasoning capacity the host already provides.
