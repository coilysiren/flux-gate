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
from .models import FeatureSpec
from .roles import DemoSpecAssessor

_ENV_OPERATOR_TYPE = "FLUX_GATE_OPERATOR_TYPE"
_ENV_OPERATOR_KEY = "FLUX_GATE_OPERATOR_KEY"
_ENV_ADVERSARY_TYPE = "FLUX_GATE_ADVERSARY_TYPE"
_ENV_ADVERSARY_KEY = "FLUX_GATE_ADVERSARY_KEY"


@click.command(
    help=(
        "Adversarial inference engine for software correctness. "
        "Runs a two-agent LLM loop against a locally-running HTTP API and "
        "outputs a risk report."
    )
)
@click.argument("url")
@click.option(
    "--spec",
    default=".flux_gate/spec.yaml",
    metavar="FILE",
    show_default=True,
    help="Path to a FeatureSpec YAML file.",
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
def main(url: str, spec: str, actors: str, threshold: float, fail_fast: bool) -> None:
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

    feature_spec: FeatureSpec | None = None
    spec_path = Path(spec)
    if spec_path.exists():
        feature_spec = FeatureSpec(**yaml.safe_load(spec_path.read_text()))

    actor_headers: dict[str, dict[str, str]] = {}
    actors_path = Path(actors)
    if actors_path.exists():
        actor_headers = to_actor_headers(ActorsConfig(**yaml.safe_load(actors_path.read_text())))

    executor = DeterministicLocalExecutor(HttpExecutor(url, actor_headers=actor_headers))
    runner = FluxGateRunner(
        executor=executor,
        operator=create_operator(operator_type, operator_key),
        adversary=create_adversary(adversary_type, adversary_key),
        spec_assessor=DemoSpecAssessor() if feature_spec else None,
        feature_spec=feature_spec,
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
        sys.exit(1)
