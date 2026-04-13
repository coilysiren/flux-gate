from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import click
import yaml

from .adapters import HttpApi
from .auth import UsersConfig, to_user_headers
from .executor import Drone
from .llm import create_attacker, create_inspector
from .loop import GauntletRunner
from .models import ExecutionResult, Target, Weapon
from .roles import DemoWeaponAssessor

_ENV_ATTACKER_TYPE = "GAUNTLET_ATTACKER_TYPE"
_ENV_ATTACKER_KEY = "GAUNTLET_ATTACKER_KEY"
_ENV_INSPECTOR_TYPE = "GAUNTLET_INSPECTOR_TYPE"
_ENV_INSPECTOR_KEY = "GAUNTLET_INSPECTOR_KEY"

_DEFAULT_CONFIG_PATH = ".gauntlet/config.yaml"

_OPTION_DEFAULTS: dict[str, Any] = {
    "weapon": ".gauntlet/weapons",
    "target": ".gauntlet/targets",
    "users": ".gauntlet/users.yaml",
    "threshold": 0.90,
    "fail_fast": True,
}


def _load_config_file(path: str | None) -> dict[str, Any]:
    """Load a YAML config file and return its contents as a dict.

    If *path* is ``None``, the default config path is tried silently.
    If an explicit *path* is given and the file does not exist, an error is
    raised via :func:`click.echo`.
    """
    if path is None:
        default = Path(_DEFAULT_CONFIG_PATH)
        if default.exists():
            raw: Any = yaml.safe_load(default.read_text())
            return dict(raw) if isinstance(raw, dict) else {}
        return {}

    p = Path(path)
    if not p.exists():
        click.echo(f"error: config file not found: {path}", err=True)
        sys.exit(1)
    raw = yaml.safe_load(p.read_text())
    return dict(raw) if isinstance(raw, dict) else {}


def _load_weapons(spec: str) -> list[Weapon]:
    """Return weapons from a single YAML file or all *.yaml files in a directory."""
    path = Path(spec)
    if not path.exists():
        return []
    if path.is_dir():
        return [Weapon(**yaml.safe_load(f.read_text())) for f in sorted(path.glob("*.yaml"))]
    return [Weapon(**yaml.safe_load(path.read_text()))]


def _load_targets(spec: str) -> list[Target]:
    """Return targets from a single YAML file or all *.yaml files in a directory."""
    path = Path(spec)
    if not path.exists():
        return []
    if path.is_dir():
        return [Target(**yaml.safe_load(f.read_text())) for f in sorted(path.glob("*.yaml"))]
    return [Target(**yaml.safe_load(path.read_text()))]


@click.command(
    help=(
        "Adversarial inference engine for software correctness. "
        "Runs a two-agent LLM loop against a locally-running HTTP API and "
        "outputs a risk report."
    )
)
@click.argument("url", required=False, default=None)
@click.option(
    "--config",
    "config_path",
    default=None,
    metavar="FILE",
    help=(
        "Path to a YAML config file. "
        "Defaults to .gauntlet/config.yaml if it exists. "
        "CLI flags override values from the config file."
    ),
)
@click.option(
    "--weapon",
    default=None,
    metavar="FILE_OR_DIR",
    help=(
        "Path to a Weapon YAML file, or a directory of YAML files "
        "(one weapon per file). [default: .gauntlet/weapons]"
    ),
)
@click.option(
    "--target",
    default=None,
    metavar="FILE_OR_DIR",
    help=(
        "Path to a Target YAML file, or a directory of YAML files "
        "(one target per file). [default: .gauntlet/targets]"
    ),
)
@click.option(
    "--users",
    default=None,
    metavar="FILE",
    help=(
        "Path to an users YAML file defining per-user authentication. "
        "[default: .gauntlet/users.yaml]"
    ),
)
@click.option(
    "--threshold",
    type=float,
    default=None,
    metavar="N",
    help="Holdout satisfaction score required to recommend merge. [default: 0.90]",
)
@click.option(
    "--fail-fast/--no-fail-fast",
    default=None,
    help="Stop after the first critical finding. [default: True]",
)
def main(
    url: str | None,
    config_path: str | None,
    weapon: str | None,
    target: str | None,
    users: str | None,
    threshold: float | None,
    fail_fast: bool | None,
) -> None:
    # --- resolve configuration: defaults < config file < CLI flags ---
    file_cfg = _load_config_file(config_path)

    # Normalise fail-fast: YAML uses underscore key, CLI uses fail_fast param
    if "fail-fast" in file_cfg:
        file_cfg.setdefault("fail_fast", file_cfg.pop("fail-fast"))

    resolved_url: str = url or file_cfg.get("url", "")
    if not resolved_url:
        click.echo(
            "error: URL is required. Provide it as a positional argument or via config file.",
            err=True,
        )
        sys.exit(1)

    def _resolve(name: str, cli_val: Any) -> Any:
        if cli_val is not None:
            return cli_val
        return file_cfg.get(name, _OPTION_DEFAULTS[name])

    weapon_val: str = _resolve("weapon", weapon)
    target_val: str = _resolve("target", target)
    users_val: str = _resolve("users", users)
    threshold_val: float = float(_resolve("threshold", threshold))
    fail_fast_val: bool = bool(_resolve("fail_fast", fail_fast))

    operator_type = os.environ.get(_ENV_ATTACKER_TYPE, "")
    operator_key = os.environ.get(_ENV_ATTACKER_KEY, "")
    adversary_type = os.environ.get(_ENV_INSPECTOR_TYPE, "")
    adversary_key = os.environ.get(_ENV_INSPECTOR_KEY, "")

    missing = [
        name
        for name, val in [
            (_ENV_ATTACKER_TYPE, operator_type),
            (_ENV_ATTACKER_KEY, operator_key),
            (_ENV_INSPECTOR_TYPE, adversary_type),
            (_ENV_INSPECTOR_KEY, adversary_key),
        ]
        if not val
    ]
    if missing:
        click.echo(
            f"error: missing required environment variables: {', '.join(missing)}\n"
            f"\n"
            f"Set them before running gauntlet:\n"
            f"  export {_ENV_ATTACKER_TYPE}=openai       # or: anthropic\n"
            f"  export {_ENV_ATTACKER_KEY}=sk-...\n"
            f"  export {_ENV_INSPECTOR_TYPE}=anthropic   # or: openai\n"
            f"  export {_ENV_INSPECTOR_KEY}=sk-ant-...",
            err=True,
        )
        sys.exit(1)

    weapons = _load_weapons(weapon_val)
    targets = _load_targets(target_val)

    user_headers: dict[str, dict[str, str]] = {}
    users_path = Path(users_val)
    if users_path.exists():
        user_headers = to_user_headers(UsersConfig(**yaml.safe_load(users_path.read_text())))

    attacker = create_attacker(operator_type, operator_key)
    inspector = create_inspector(adversary_type, adversary_key)
    executor = Drone(HttpApi(resolved_url, user_headers=user_headers))

    blocked = False
    for inv in weapons or [None]:  # type: ignore[list-item]
        for tgt in targets or [None]:  # type: ignore[list-item]
            runner = GauntletRunner(
                executor=executor,
                attacker=attacker,
                inspector=inspector,
                assessor=DemoWeaponAssessor() if inv else None,
                weapon=inv,
                target=tgt,
                clearance_threshold=threshold_val,
                fail_fast_tier=0 if fail_fast_val else None,
            )

            try:
                run = runner.run()
            except Exception as exc:  # noqa: BLE001
                click.echo(f"error: {exc}", err=True)
                sys.exit(1)

            clearance = run.clearance
            if clearance:
                label = clearance.recommendation.upper()
                click.echo(f"--- GAUNTLET CLEARANCE: {label} ---")

            if run.holdout_results:
                _print_holdout_summary(run.holdout_results)

            click.echo(yaml.dump(run.model_dump(), sort_keys=False, allow_unicode=True))

            if clearance and clearance.recommendation == "block":
                click.echo(f"clearance: BLOCKED — {clearance.rationale}", err=True)
                blocked = True
            elif clearance and clearance.recommendation == "conditional":
                click.echo(f"clearance: CONDITIONAL — {clearance.rationale}", err=True)

    if blocked:
        sys.exit(1)


def _print_holdout_summary(holdout_results: list[ExecutionResult]) -> None:
    total = len(holdout_results)
    passed = sum(1 for r in holdout_results if r.satisfaction_score == 1.0)
    failed = total - passed
    status = "ALL PASSED" if failed == 0 else f"{failed} FAILED"
    click.echo(f"--- HIDDEN VITALS: {passed}/{total} passed ({status}) ---")
    click.echo(
        f"    {total} acceptance criteria evaluated against unseen holdout vitals\n"
        f"    (withheld from attacker — independent verification)"
    )
