# Scope

What Gauntlet is, what it isn't, and what counts as a public-API change.

The point of this file is to make scope creep deliberate. If a future change adds, removes, or alters anything under "Public surface", it needs an explicit reason that overrides this document, not just an offhand "while I was in here." Internals can move freely.

## Public surface (changes are API-breaking; review with care)

These are the contracts a host orchestrator binds to. They cannot move silently.

### MCP tools

The 13 tools exposed by `gauntlet/server.py`:

| Tool | Signature shape |
|---|---|
| `list_trials(trials_path)` | → `list[dict]` of `{id, title, description}` |
| `get_trial(trial_id, trials_path)` | → `Trial` |
| `execute_plan(url, plan, user_headers)` | → `ExecutionResult` |
| `start_run(trial_ids)` | → `{run_id}` |
| `record_iteration(run_id, trial_id, iteration_record)` | → `{status}` |
| `read_iteration_records(run_id, trial_id)` | → `list[IterationRecord]` |
| `record_holdout_result(run_id, trial_id, holdout_result)` | → `{status}` |
| `read_holdout_results(run_id, trial_id)` | → `list[HoldoutResult]` |
| `assemble_run_report(run_id, trial_id, clearance_threshold)` | → `dict` (also persists confirmed-failure findings to the cross-run store as a side effect) |
| `assemble_final_clearance(run_id, clearance_threshold, trial_ids?)` | → `FinalClearance` |
| `replay_finding(run_id, trial_id, finding_index, url, user_headers)` | → `ExecutionResult` |
| `mutate_plans(run_id, trial_id, max_variants)` | → `list[Plan]` |
| `recurring_failures(trial_id, lookback, findings_path)` | → `list[dict]` of `{issue, occurrences, run_ids}` |

Adding, renaming, removing, or changing the parameter set of any of these is a breaking change.

### Subagent allowlists

Each of `agents/gauntlet-attacker.md`, `agents/gauntlet-inspector.md`, `agents/gauntlet-holdout-evaluator.md` declares an MCP-tool allowlist in YAML frontmatter. The allowlists are the train/test split. Adding a tool to a subagent's allowlist that the role isn't supposed to access (e.g., giving the Attacker `get_trial`) collapses the split. `tests/test_subagents.py` enforces these; changes to those tests need explicit justification.

### Skill trigger phrases

Each of `skills/gauntlet/SKILL.md`, `skills/gauntlet-author/SKILL.md` carries trigger phrases in its frontmatter. Hosts auto-discover skills by phrase match; renaming or dropping triggers breaks discovery for existing prompt patterns.

### Trial YAML schema

`Trial` (in `gauntlet/models.py`) is what users author into `.gauntlet/trials/*.yaml`. Required fields (`title`, `description`, `blockers`) and the snake_case `id` constraint are part of the contract. Adding optional fields is non-breaking; renaming or removing existing fields is breaking.

## Internals (free to change without ceremony)

- File layout under `gauntlet/`
- Implementation of any MCP tool (as long as the signature and observable behavior don't change)
- Storage layout under `.gauntlet/runs/` (it's an implementation detail; nothing outside Gauntlet should read from it)
- Dependency choices (`requests`, `pyyaml`, etc.)
- Test layout, factories, helpers
- Prose wording inside skill/agent files (as long as triggers and allowlists hold)
- Docstring content
- Docs

## Non-goals (do not implement without revisiting this doc)

These are the things you'll be tempted to add and shouldn't, because they re-introduce architectural mistakes we've already paid to remove or scope-creep beyond what the host needs.

- **CLI entry point.** Gauntlet runs only as an MCP server inside Claude Code. No `gauntlet` shell command, no `argparse`, no standalone Python invocation path.
- **Multi-surface execution.** No CLI adapter, no WebDriver adapter, no browser automation. HTTP only. The Adapter protocol was deleted for a reason.
- **A real-time dashboard, web UI, or report renderer.** Gauntlet returns structured data; the host renders it however it wants.
- **Multi-provider LLM abstraction.** Gauntlet does not call an LLM. The host provides the reasoning.
- **A trial-coverage scorer or test-coverage analyzer.** The `gauntlet-author` skill is a one-shot translator; coverage scoring is a future feature with no present consumer.
- **Cross-target generalization** (run one trial against many SUTs in one call). The host loops; Gauntlet does one trial at a time.
- **A CI gate, GitHub Action, or pre-commit hook.** Hosts that want one wrap Gauntlet themselves.
- **Authentication beyond "pass me a header dict".** No OAuth flows, no env-var indirection, no token refresh.
- **Built-in retry/backoff or rate limiting** on `execute_plan`. The SUT's flakiness is the host's problem to model.

## When to revisit

Open this file when:
- A user (not a future Kai with an idea) requests a feature that lives under "Non-goals."
- A second consumer beyond the dark-factory orchestrator appears and the integration story changes.
- A real cross-run pattern emerges in production usage that the run-scoped buffer can't represent.

Otherwise, this doc holds.
