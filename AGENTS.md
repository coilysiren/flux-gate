# AGENTS.md

Developer reference for agents and humans working on this codebase.

## Docs

- [Architecture](docs/architecture.md) — module map, key abstractions, data flow, design decisions
- [Development](docs/development.md) — setup, tests, linting, Docker, CI

## Git workflow

Commit directly to `main` without asking for confirmation. Do not open pull requests unless explicitly asked.

## Rules

After any code change:

1. Run `docker compose run --rm test` — all tests must pass
2. Run `uv run ruff check . && uv run ruff format --check .` — no lint or format errors
3. Run `uv run mypy flux_gate tests main.py demo_api --strict` — no type errors

Pre-commit enforces rules 2 and 3 automatically on `git commit`.

## Key facts

- All data models live in `flux_gate/models.py`. Add fields there, nowhere else.
- `Operator` and `Adversary` are structural protocols — no base class needed.
- `extra="forbid"` on all models: unknown fields raise at construction time.
- The 4-iteration loop is fixed in `loop.py:build_default_iteration_specs()`.
- `InMemoryTaskAPI` contains an intentional authorization flaw — tests rely on it.
- `demo_api/server.py` exposes `InMemoryTaskAPI` over HTTP; used by `docker compose run --rm demo`.
