# Development Guide

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Docker | any | [docker.com](https://docker.com) |
| uv | latest | `brew install uv` (for local dev / pre-commit) |

## Setup

```bash
git clone git@github.com:coilysiren/flux-gate.git
cd flux-gate
docker compose build
uv run pre-commit install  # install git hooks
```

## Running the demo

```bash
docker compose run --rm demo
```

Starts the demo API (`demo_api/server.py`) and runs `flux-gate` against it.
Outputs a full `FluxGateRun` as YAML. The demo API has a seeded authorization
flaw — expect `risk_level: critical`.

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
uv run mypy flux_gate tests main.py demo_api --strict  # type-check
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
