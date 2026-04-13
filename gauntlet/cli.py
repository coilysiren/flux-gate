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
from .models import Arsenal, ExecutionResult, Finding, GauntletRun, Target, Weapon
from .openapi import parse_openapi
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
    path = Path(spec)
    if not path.exists():
        return []
    if path.is_dir():
        return [Weapon(**yaml.safe_load(f.read_text())) for f in sorted(path.glob("*.yaml"))]
    return [Weapon(**yaml.safe_load(path.read_text()))]


def _load_arsenal(spec: str) -> Arsenal:
    path = Path(spec)
    data = yaml.safe_load(path.read_text())
    return Arsenal(**data)


def _load_targets(spec: str) -> list[Target]:
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
    help="Path to a YAML config file. Defaults to .gauntlet/config.yaml if it exists.",
)
@click.option(
    "--arsenal",
    default=None,
    metavar="FILE",
    help="Path to an Arsenal YAML file (a named collection of weapons).",
)
@click.option(
    "--weapon",
    default=None,
    metavar="FILE_OR_DIR",
    help="Path to a Weapon YAML file or directory. [default: .gauntlet/weapons]",
)
@click.option(
    "--target",
    default=None,
    metavar="FILE_OR_DIR",
    help="Path to a Target YAML file or directory. [default: .gauntlet/targets]",
)
@click.option(
    "--users",
    default=None,
    metavar="FILE",
    help="Path to users YAML file. [default: .gauntlet/users.yaml]",
)
@click.option(
    "--threshold",
    type=float,
    default=None,
    metavar="N",
    help="Holdout satisfaction score required to recommend merge. [default: 0.90]",
)
@click.option(
    "--openapi",
    default=None,
    metavar="FILE",
    help="Path to an OpenAPI 3.x YAML/JSON spec. Auto-generates Target objects.",
)
@click.option(
    "--fail-fast/--no-fail-fast",
    default=None,
    help="Stop after the first critical finding. [default: True]",
)
def main(
    url: str | None,
    config_path: str | None,
    arsenal: str | None,
    weapon: str | None,
    target: str | None,
    users: str | None,
    threshold: float | None,
    openapi: str | None,
    fail_fast: bool | None,
) -> None:
    file_cfg = _load_config_file(config_path)
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

    if arsenal:
        loaded_arsenal = _load_arsenal(arsenal)
        weapons = loaded_arsenal.weapons
    else:
        weapons = _load_weapons(weapon_val)
    targets = _load_targets(target_val)

    if openapi:
        targets = parse_openapi(openapi) + targets

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

            _print_one_line_summary(run)
            _print_progression_metrics(run)
            _print_findings_formatted(run)

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


def _print_one_line_summary(run: GauntletRun) -> None:
    clearance = run.clearance
    if clearance is None:
        click.echo("PASS — no clearance gate configured")
        return
    label = clearance.recommendation.upper()
    all_findings = [f for record in run.iterations for f in record.findings]
    if not all_findings:
        click.echo(f"{label} — no findings detected")
        return
    worst = _worst_finding(all_findings)
    method = _dominant_method(run)
    blocker_part = f" {worst.violated_blocker} violated" if worst.violated_blocker else ""
    method_part = f" via unauthorized {method}" if method else ""
    click.echo(f"{label} —{blocker_part}{method_part}")


def _worst_finding(findings: list[Finding]) -> Finding:
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return min(findings, key=lambda f: severity_order.get(f.severity, 4))


def _dominant_method(run: GauntletRun) -> str:
    methods: list[str] = []
    for record in run.iterations:
        for finding in record.findings:
            for trace in finding.traces:
                methods.append(trace.request.method)
    if not methods:
        return ""
    return max(set(methods), key=methods.count)


def _print_progression_metrics(run: GauntletRun) -> None:
    iterations_run = len(run.iterations)
    total_plans = sum(len(record.plans) for record in run.iterations)
    total_findings = sum(len(record.findings) for record in run.iterations)
    escalations = sum(
        1
        for record in run.iterations
        for finding in record.findings
        if finding.severity in ("high", "critical")
    )
    click.echo(
        f"--- PROGRESSION: {iterations_run} iterations | "
        f"{total_plans} plans | "
        f"{total_findings} findings | "
        f"{escalations} escalations ---"
    )


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


def _print_findings_formatted(run: GauntletRun) -> None:
    for record in run.iterations:
        for finding in record.findings:
            if finding.severity in ("critical", "high"):
                click.echo(f"\u274c {finding.issue} [{finding.severity}]")
            else:
                click.echo(f"\u26a0\ufe0f {finding.issue} [{finding.severity}]")
