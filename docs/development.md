# Development Guide

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Python | 3.13+ | `pyenv install 3.13` |
| uv | latest | `brew install uv` |
| Docker | any | [docker.com](https://docker.com) |

## Setup

```bash
git clone git@github.com:coilysiren/flux-gate.git
cd flux-gate
uv sync --frozen          # install all deps into .venv
uv run pre-commit install # install git hooks
```

## Running the demo

```bash
uv run python main.py
```

Prints a full `FluxGateRun` as JSON. The demo uses `InMemoryTaskAPI` with a seeded
authorization flaw — expect `risk_level: critical`.

## Tests

```bash
# Unit tests (no Docker required)
uv run pytest -m "not docker"

# Docker integration tests (requires Docker daemon)
uv run pytest -m docker

# Everything
uv run pytest
```

Coverage is printed to the terminal and written to `coverage.xml` after every run.
`coverage.xml` is gitignored.

## Linting & formatting

```bash
uv run ruff check .          # lint
uv run ruff check . --fix    # lint + auto-fix
uv run ruff format .         # format
uv run mypy flux_gate tests main.py --strict  # type-check
```

All three run automatically as pre-commit hooks on every `git commit`.

## Docker Compose

```bash
docker compose build          # build images
docker compose run --rm app   # run the demo inside a container
docker compose run --rm test  # run unit tests inside a container
```

The `test` service runs `pytest -m "not docker"` — docker-in-docker is not set up
and not needed.

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
