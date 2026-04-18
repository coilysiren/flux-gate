# Development Guide

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Docker | any | [docker.com](https://docker.com) |
| uv | latest | `brew install uv` (for local dev / pre-commit) |

## Setup

```bash
git clone git@github.com:coilysiren/gauntlet.git
cd gauntlet
docker compose build
uv run pre-commit install  # install git hooks
```

## Running the demo

```bash
docker compose run --rm demo
```

Starts the demo API (`demo_api/server.py`) and runs `gauntlet` against it.
Outputs a full `GauntletRun` as YAML. The demo API has a seeded authorization
flaw — expect `risk_level: critical`.

## Running the full arsenal locally

```bash
./scripts/run_arsenal.py
```

Runs every weapon in `.gauntlet/weapons/` (including the `owasp-` prefixed set) against the
in-memory demo API. No LLM keys required — uses the deterministic `Demo*`
classes. The demo API has three seeded flaws:

1. **PATCH without ownership check** — any user can modify any task
2. **POST accepts invalid/missing title** — no input validation
3. **GET /tasks leaks all users' data** — no read isolation

All 13 weapons should produce a `BLOCK` clearance. Exit code 1 means at least
one weapon found a flaw (expected). Exit code 0 means everything passed.

## Tests

```bash
# Run tests inside Docker (canonical)
docker compose run --rm test

# Run tests locally (faster iteration)
uv run pytest -m "not docker"

# Run docker integration tests (requires Docker daemon)
uv run pytest -m docker
```

Coverage is printed to the terminal and written to `coverage.xml` after every run.
`coverage.xml` is gitignored.

## Linting & formatting

Pre-commit hooks run automatically on every `git commit`. To run manually:

```bash
uv run ruff check .          # lint
uv run ruff check . --fix    # lint + auto-fix
uv run ruff format .         # format
uv run mypy gauntlet tests demo_api --strict  # type-check
```

## CI

Three jobs run on every push and PR to `main`:

| Job | What it checks |
|---|---|
| `lint` | ruff + mypy |
| `test` | pytest + uploads coverage to Codecov |
| `docker` | `docker compose build` + `docker compose run --rm test` |

See `.github/workflows/ci.yml`.

## Dependency management

Add a runtime dependency:

```bash
uv add <package>
```

Add a dev-only dependency:

```bash
uv add --dev <package>
```

Always commit the updated `uv.lock` alongside `pyproject.toml`.
