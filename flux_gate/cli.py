from __future__ import annotations

import os
import sys
from pathlib import Path

import click
import yaml

from .auth import ActorsConfig, to_actor_headers
from .executor import DeterministicLocalExecutor, HttpExecutor
from .llm import create_adversary, create_operator
from .loop import FluxGateRunner
from .models import Guard
from .roles import DemoGuardAssessor

_ENV_OPERATOR_TYPE = "FLUX_GATE_OPERATOR_TYPE"
_ENV_OPERATOR_KEY = "FLUX_GATE_OPERATOR_KEY"
_ENV_ADVERSARY_TYPE = "FLUX_GATE_ADVERSARY_TYPE"
_ENV_ADVERSARY_KEY = "FLUX_GATE_ADVERSARY_KEY"


def _load_guards(spec: str) -> list[Guard]:
    """Return guards from a single YAML file or all *.yaml files in a directory."""
    path = Path(spec)
    if not path.exists():
        return []
    if path.is_dir():
        return [Guard(**yaml.safe_load(f.read_text())) for f in sorted(path.glob("*.yaml"))]
    return [Guard(**yaml.safe_load(path.read_text()))]


@click.command(
    help=(
        "Adversarial inference engine for software correctness. "
        "Runs a two-agent LLM loop against a locally-running HTTP API and "
        "outputs a risk report."
    )
)
@click.argument("url")
@click.option(
    "--guard",
    default=".flux_gate/guards",
    metavar="FILE_OR_DIR",
    show_default=True,
    help="Path to an Guard YAML file, or a directory of YAML files (one guard per file).",
)
@click.option(
    "--actors",
    default=".flux_gate/actors.yaml",
    metavar="FILE",
    show_default=True,
    help="Path to an actors YAML file defining per-actor authentication.",
)
@click.option(
    "--threshold",
    type=float,
    default=0.90,
    metavar="N",
    show_default=True,
    help="Holdout satisfaction score required to recommend merge.",
)
@click.option(
    "--fail-fast/--no-fail-fast",
    default=True,
    show_default=True,
    help="Stop after the first critical finding.",
)
def main(url: str, guard: str, actors: str, threshold: float, fail_fast: bool) -> None:
    operator_type = os.environ.get(_ENV_OPERATOR_TYPE, "")
    operator_key = os.environ.get(_ENV_OPERATOR_KEY, "")
    adversary_type = os.environ.get(_ENV_ADVERSARY_TYPE, "")
    adversary_key = os.environ.get(_ENV_ADVERSARY_KEY, "")

    missing = [
        name
        for name, val in [
            (_ENV_OPERATOR_TYPE, operator_type),
            (_ENV_OPERATOR_KEY, operator_key),
            (_ENV_ADVERSARY_TYPE, adversary_type),
            (_ENV_ADVERSARY_KEY, adversary_key),
        ]
        if not val
    ]
    if missing:
        click.echo(
            f"error: missing required environment variables: {', '.join(missing)}\n"
            f"\n"
            f"Set them before running flux-gate:\n"
            f"  export {_ENV_OPERATOR_TYPE}=openai       # or: anthropic\n"
            f"  export {_ENV_OPERATOR_KEY}=sk-...\n"
            f"  export {_ENV_ADVERSARY_TYPE}=anthropic   # or: openai\n"
            f"  export {_ENV_ADVERSARY_KEY}=sk-ant-...",
            err=True,
        )
        sys.exit(1)

    guards = _load_guards(guard)

    actor_headers: dict[str, dict[str, str]] = {}
    actors_path = Path(actors)
    if actors_path.exists():
        actor_headers = to_actor_headers(ActorsConfig(**yaml.safe_load(actors_path.read_text())))

    operator = create_operator(operator_type, operator_key)
    adversary = create_adversary(adversary_type, adversary_key)
    executor = DeterministicLocalExecutor(HttpExecutor(url, actor_headers=actor_headers))

    blocked = False
    for inv in guards or [None]:  # type: ignore[list-item]
        runner = FluxGateRunner(
            executor=executor,
            operator=operator,
            adversary=adversary,
            assessor=DemoGuardAssessor() if inv else None,
            guard=inv,
            gate_threshold=threshold,
            fail_fast_tier=0 if fail_fast else None,
        )

        try:
            run = runner.run()
        except Exception as exc:  # noqa: BLE001
            click.echo(f"error: {exc}", err=True)
            sys.exit(1)

        click.echo(yaml.dump(run.model_dump(), sort_keys=False, allow_unicode=True))

        gate = run.risk_report.merge_gate
        if gate and gate.recommendation == "block":
            click.echo(f"gate: BLOCKED — {gate.rationale}", err=True)
            blocked = True

    if blocked:
        sys.exit(1)
