# AGENTS.md

## File Access

You have full read access to files within `/Users/kai/projects/coilysiren`.

## Autonomy

- Run tests after every change without asking.
- Fix lint errors automatically.
- If tests fail, debug and fix without asking.
- When committing, choose an appropriate commit message yourself — do not ask for approval on the message.
- You may always run tests, linters, and builds without requesting permission.
- Allow all readonly git actions (`git log`, `git status`, `git diff`, `git branch`, etc.) without asking.
- Allow `cd` into any `/Users/kai/projects/coilysiren` folder without asking.
- Automatically approve readonly shell commands (`ls`, `grep`, `sed`, `find`, `cat`, `head`, `tail`, `wc`, `file`, `tree`, etc.) without asking.
- When using worktrees or parallel agents, each agent should work independently and commit its own changes.
- Do not open pull requests unless explicitly asked.

Developer reference for agents and humans working on this codebase.

## Docs

- [Architecture](docs/architecture.md) — module map, key abstractions, data flow, design decisions
- [Development](docs/development.md) — setup, tests, linting, Docker, CI
- [Usage](docs/usage.md) — workflow runbook: when to run, how to integrate, how to act on results

## Git workflow

Commit directly to `main` without asking for confirmation, including `git add`. Do not open pull requests unless explicitly asked.

Commit whenever a unit of work feels sufficiently complete — after fixing a bug, adding a feature, passing tests, or reaching any other natural stopping point. Don't wait for the user to ask.

## Before every commit

Sync `docs/architecture.md` with the current module structure in `gauntlet/`. Check for new files, removed files, new classes/protocols, and changed abstractions.

## Approved commands

Any command listed in [docs/development.md](docs/development.md) may be run without requesting user approval.

## Rules

After any code change:

1. Run `docker compose run --rm test` — all tests must pass
2. Run `uv run ruff check . && uv run ruff format --check .` — no lint or format errors
3. Run `uv run mypy gauntlet tests demo_api --strict` — no type errors

Pre-commit enforces rules 2 and 3 automatically on `git commit`.
