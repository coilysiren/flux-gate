from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

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

_DEFAULT_SPEC = ".flux_gate/spec.yaml"
_DEFAULT_ACTORS = ".flux_gate/actors.yaml"


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
        "--name",
        default=None,
        help="Human-readable name for the system under test (default: URL hostname)",
    )
    parser.add_argument(
        "--env",
        default="local",
        help="Environment label included in the report (default: local)",
    )
    parser.add_argument(
        "--spec",
        default=None,
        metavar="FILE",
        help=(f"Path to a FeatureSpec YAML file (default: {_DEFAULT_SPEC} if it exists)"),
    )
    parser.add_argument(
        "--actors",
        default=None,
        metavar="FILE",
        help=(
            f"Path to an actors YAML file defining per-actor authentication "
            f"(default: {_DEFAULT_ACTORS} if it exists)"
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.90,
        metavar="N",
        help="Holdout satisfaction score required to recommend merge (default: 0.90)",
    )
    parser.add_argument(
        "--fail-fast-tier",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Stop after the first iteration at tier >= N that finds a critical issue "
            "(default: disabled — all iterations always run)"
        ),
    )
    return parser


def _load_file(explicit: str | None, default: str) -> Path | None:
    """Return the path to use for a config file, or None if it should be skipped.

    If an explicit path was given and does not exist, exits 1 with an error.
    If no explicit path was given, silently skips when the default is absent.
    """
    if explicit is not None:
        p = Path(explicit)
        if not p.exists():
            print(f"error: file not found: {explicit}", file=sys.stderr)
            sys.exit(1)
        return p
    p = Path(default)
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

    name: str = args.name or urlparse(args.url).netloc or args.url

    feature_spec: FeatureSpec | None = None
    spec_path = _load_file(args.spec, _DEFAULT_SPEC)
    if spec_path is not None:
        feature_spec = FeatureSpec(**yaml.safe_load(spec_path.read_text()))

    actor_headers: dict[str, dict[str, str]] = {}
    actors_path = _load_file(args.actors, _DEFAULT_ACTORS)
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
        fail_fast_tier=args.fail_fast_tier,
        system_under_test=name,
        environment=args.env,
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
