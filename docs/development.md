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
uv sync
uv run pre-commit install  # install git hooks
```

## Running the MCP server locally

```bash
uv run gauntlet-mcp
```

This speaks stdio.

## Exercising the plugin end-to-end

The repo doubles as a Claude Code plugin. Point Claude Code at it during development:

```bash
cd your-test-project
claude --plugin-dir /absolute/path/to/gauntlet
```

This loads the plugin from disk for the current session - no install, no cache. Claude Code will:

- register the `gauntlet` MCP server via `uv run --project ${CLAUDE_PLUGIN_ROOT} gauntlet-mcp`
- auto-discover the skill at `skills/gauntlet/SKILL.md`

Verify:
- `/mcp` lists `gauntlet` with its 7 tools
- Typing a trigger phrase like "run gauntlet" loads the skill

To install the plugin permanently (for non-development use):

```bash
claude plugin install /absolute/path/to/gauntlet
```

Files the plugin system reads:
- `.claude-plugin/plugin.json` - manifest (MCP server declaration, metadata)
- `skills/gauntlet/SKILL.md` - auto-discovered skill

Both paths are load-bearing. Moving either breaks the plugin; update `plugin.json` if you relocate a file.

## Tests

```bash
# Run tests inside Docker (canonical)
docker compose run --rm test

# Run tests locally (faster iteration)
uv run pytest -m "not docker"

# Run docker integration tests (requires Docker daemon)
uv run pytest -m docker
```

Coverage is printed to the terminal and written to `coverage.xml` after every run. `coverage.xml` is gitignored.

## Linting & formatting

Pre-commit hooks run automatically on every `git commit`. To run manually:

```bash
uv run ruff check .          # lint
uv run ruff check . --fix    # lint + auto-fix
uv run ruff format .         # format
uv run mypy gauntlet tests --strict  # type-check
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
