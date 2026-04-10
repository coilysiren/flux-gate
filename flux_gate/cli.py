from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flux-gate",
        description=(
            "Adversarial inference engine for software correctness. "
            "Runs a two-agent LLM loop against a locally-running HTTP API and "
            "outputs a risk report."
        ),
    )
    parser.add_argument(
        "url",
        help="Base URL of the running API (e.g. http://localhost:8000)",
    )
    parser.add_argument(
        "--spec",
        default=".flux_gate/spec.yaml",
        metavar="FILE",
        help="Path to a FeatureSpec YAML file (default: .flux_gate/spec.yaml)",
    )
    parser.add_argument(
        "--actors",
        default=".flux_gate/actors.yaml",
        metavar="FILE",
        help="Path to an actors YAML file defining per-actor authentication "
        "(default: .flux_gate/actors.yaml)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.90,
        metavar="N",
        help="Holdout satisfaction score required to recommend merge (default: 0.90)",
    )
    parser.add_argument(
        "--fail-fast",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Stop after the first critical finding "
        "(default: enabled; use --no-fail-fast to run all iterations)",
    )
    return parser


def _try_load(path: str) -> Path | None:
    """Return the path if the file exists, otherwise None."""
    p = Path(path)
    return p if p.exists() else None


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # LLM operator configuration — required env vars.
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
        print(
            f"error: missing required environment variables: {', '.join(missing)}\n"
            f"\n"
            f"Set them before running flux-gate:\n"
            f"  export {_ENV_OPERATOR_TYPE}=openai       # or: anthropic\n"
            f"  export {_ENV_OPERATOR_KEY}=sk-...\n"
            f"  export {_ENV_ADVERSARY_TYPE}=anthropic   # or: openai\n"
            f"  export {_ENV_ADVERSARY_KEY}=sk-ant-...",
            file=sys.stderr,
        )
        sys.exit(1)

    feature_spec: FeatureSpec | None = None
    spec_path = _try_load(args.spec)
    if spec_path is not None:
        feature_spec = FeatureSpec(**yaml.safe_load(spec_path.read_text()))

    actor_headers: dict[str, dict[str, str]] = {}
    actors_path = _try_load(args.actors)
    if actors_path is not None:
        actor_headers = to_actor_headers(ActorsConfig(**yaml.safe_load(actors_path.read_text())))

    executor = DeterministicLocalExecutor(HttpExecutor(args.url, actor_headers=actor_headers))
    runner = FluxGateRunner(
        executor=executor,
        operator=create_operator(operator_type, operator_key),
        adversary=create_adversary(adversary_type, adversary_key),
        spec_assessor=DemoSpecAssessor() if feature_spec else None,
        feature_spec=feature_spec,
        gate_threshold=args.threshold,
        fail_fast_tier=0 if args.fail_fast else None,
        system_under_test=args.url,
        environment="local",
    )

    try:
        run = runner.run()
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(yaml.dump(run.model_dump(), sort_keys=False, allow_unicode=True))

    gate = run.risk_report.merge_gate
    if gate and gate.recommendation == "block":
        print(f"gate: BLOCKED — {gate.rationale}", file=sys.stderr)
        sys.exit(1)
