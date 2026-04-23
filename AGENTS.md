# Agent instructions

See `../AGENTS.md` for workspace-level conventions (git workflow, test/lint autonomy, readonly ops, writing voice, deploy knowledge). This file covers only what's specific to this repo.

---

Developer reference for agents and humans working on this codebase.

## Operating model

Gauntlet runs **exclusively as an MCP server inside Claude Code**. There is no CLI, no standalone invocation. The host Claude Code agent plays the Attacker and Inspector roles; Gauntlet exposes deterministic tools (config loading, plan execution, risk-report assembly) via `gauntlet/server.py`. No Anthropic/OpenAI credentials are needed - the host provides auth.

## Docs

- [Scope](SCOPE.md) - public API surface, internals, non-goals. Read before adding anything to the MCP tool surface, the subagent allowlists, the skill triggers, or the Weapon schema.
- [Architecture](docs/architecture.md) - module map, MCP tool surface, train/test split, design decisions
- [Development](docs/development.md) - setup, tests, linting, Docker, CI
- [Usage](docs/usage.md) - host runbook: the driven loop, interpreting results

## Before every commit

Sync `docs/architecture.md` with the current module structure in `gauntlet/`. Check for new files, removed files, new classes/protocols, and changed abstractions.

## Scope discipline

Before adding, removing, or renaming anything on Gauntlet's public surface (MCP tools, subagent allowlists, skill triggers, Weapon YAML fields), check [SCOPE.md](SCOPE.md). If the change would land under "Non-goals", surface it to the user instead of doing it. Internal refactors don't need this check.

## Approved commands

Any command listed in [docs/development.md](docs/development.md) may be run without requesting user approval.

## Rules

After any code change:

1. Run `docker compose run --rm test` - all tests must pass
2. Run `uv run ruff check . && uv run ruff format --check .` - no lint or format errors
3. Run `uv run mypy gauntlet tests --strict` - no type errors

Pre-commit enforces rules 2 and 3 automatically on `git commit`.
