"""MCP server exposing Gauntlet's deterministic primitives.

Gauntlet runs exclusively inside a Claude Code session driven by a
dark-factory orchestrator. Per-role subagents (gauntlet-attacker,
gauntlet-inspector, gauntlet-holdout-evaluator) call this MCP server for
the deterministic pieces: weapon loading, plan execution against the SUT,
run-buffer management, and clearance assembly.

The train/test split is enforced at the Claude Code permission layer via
the subagents' MCP-tool allowlists, plus at the buffer boundary by
``record_iteration`` (which rejects findings carrying blocker text).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from mcp.server.fastmcp import FastMCP

from ._log import configure_logging, log_tool_call
from .executor import Drone
from .http import HttpApi
from .loop import aggregate_final_clearance, build_risk_report
from .models import (
    Clearance,
    ExecutionResult,
    FinalClearance,
    HoldoutResult,
    IterationRecord,
    Plan,
    RiskReport,
    Weapon,
    WeaponReport,
)
from .runs import RunStore

configure_logging()

mcp = FastMCP("gauntlet")

_DEFAULT_WEAPONS_PATH = ".gauntlet/weapons"

# Relative path resolved against cwd at filesystem-access time, so a host that
# chdir's into the project root gets the right buffer location.
_run_store = RunStore()


def _load_weapons_from_dir(path: Path) -> list[Weapon]:
    return [Weapon(**yaml.safe_load(f.read_text())) for f in sorted(path.glob("*.yaml"))]


def _load_weapons(weapons_path: str) -> list[Weapon]:
    path = Path(weapons_path)
    if not path.exists():
        return []
    if path.is_dir():
        return _load_weapons_from_dir(path)
    return [Weapon(**yaml.safe_load(path.read_text()))]


@mcp.tool()
def list_weapons(weapons_path: str = _DEFAULT_WEAPONS_PATH) -> list[dict[str, str | None]]:
    """Return attacker-safe views of available weapons.

    Each entry is ``{id, title, description}`` — ``blockers`` are intentionally
    omitted. Call this in the host's Attacker context to pick a weapon.
    """
    with log_tool_call("list_weapons", weapons_path=weapons_path):
        return [w.attacker_view() for w in _load_weapons(weapons_path)]


@mcp.tool()
def get_weapon(weapon_id: str, weapons_path: str = _DEFAULT_WEAPONS_PATH) -> Weapon:
    """Return the full weapon, including ``blockers``.

    HOST DISCIPLINE: only call this in a HoldoutEvaluator context. Never read
    the result in an Attacker context — doing so collapses the train/test
    split and invalidates the run.
    """
    with log_tool_call("get_weapon", weapon_id=weapon_id, weapons_path=weapons_path):
        for weapon in _load_weapons(weapons_path):
            if weapon.id == weapon_id:
                return weapon
        raise ValueError(f"No weapon with id {weapon_id!r}")


@mcp.tool()
def execute_plan(
    url: str,
    plan: Plan,
    user_headers: dict[str, dict[str, str]] | None = None,
) -> ExecutionResult:
    """Execute a plan against a live HTTP API and return the result.

    ``url`` is the base URL of the SUT. ``user_headers`` maps a user name to
    the request headers that authenticate that user (e.g.
    ``{"alice": {"Authorization": "Bearer ..."}}``). Users without an entry
    fall back to the default ``X-User: <name>`` header.
    """
    with log_tool_call("execute_plan", url=url, plan_name=plan.name):
        drone = Drone(HttpApi(url, user_headers=user_headers or {}))
        return drone.run_plan(plan)


@mcp.tool()
def assemble_run_report(
    run_id: str,
    weapon_id: str,
    clearance_threshold: float = 0.90,
) -> dict[str, Any]:
    """Assemble the final ``RiskReport`` and ``Clearance`` for one weapon.

    Reads the iteration and holdout buffers the server owns and assembles
    the report. Returns ``risk_report`` plus a clearance recommendation
    (``pass``, ``conditional``, or ``block``).
    """
    with log_tool_call("assemble_run_report", run_id=run_id, weapon_id=weapon_id):
        records = _run_store.read_iteration_records(run_id, weapon_id)
        holdouts = [
            hr.execution_result for hr in _run_store.read_holdout_results(run_id, weapon_id)
        ]

        report, clearance = build_risk_report(records, holdouts, clearance_threshold)
        return {
            "risk_report": report.model_dump(),
            "clearance": clearance.model_dump() if clearance else None,
        }


@mcp.tool()
def start_run(weapon_ids: list[str]) -> dict[str, str]:
    """Initialize a new run-scoped buffer and return the opaque ``run_id``.

    Carry the returned ``run_id`` through subsequent ``record_iteration``,
    ``read_iteration_records``, ``record_holdout_result``,
    ``read_holdout_results``, and ``assemble_run_report`` calls. The buffer
    is short-lived: one run, one host session.
    """
    with log_tool_call("start_run", weapon_count=len(weapon_ids)):
        return {"run_id": _run_store.start_run(weapon_ids)}


@mcp.tool()
def record_iteration(
    run_id: str,
    weapon_id: str,
    iteration_record: IterationRecord,
) -> dict[str, str]:
    """Append one ``IterationRecord`` to the weapon's per-run buffer.

    Called by the Attacker (after composing plans + executing them) and by
    the Inspector (after analysing ``ExecutionResult``s into ``Finding``s).
    Findings must have ``violated_blocker=None`` — the Inspector never sees
    blocker text, and the train/test split forbids it from entering this
    buffer.
    """
    with log_tool_call("record_iteration", run_id=run_id, weapon_id=weapon_id):
        _run_store.record_iteration(run_id, weapon_id, iteration_record)
        return {"status": "ok"}


@mcp.tool()
def read_iteration_records(run_id: str, weapon_id: str) -> list[IterationRecord]:
    """Return every ``IterationRecord`` previously appended for this weapon.

    Called by the Attacker (to read its own prior plans + Inspector findings)
    and by the Inspector (to read prior findings). Both reads are train/test
    safe: nothing returned here ever contains blocker text.
    """
    with log_tool_call("read_iteration_records", run_id=run_id, weapon_id=weapon_id):
        return _run_store.read_iteration_records(run_id, weapon_id)


@mcp.tool()
def record_holdout_result(
    run_id: str,
    weapon_id: str,
    holdout_result: HoldoutResult,
) -> dict[str, str]:
    """Append one ``HoldoutResult`` to the weapon's holdout buffer.

    Called only by the HoldoutEvaluator after executing one acceptance plan
    derived from a weapon's blocker. ``HoldoutResult.weapon_id`` must match
    the ``weapon_id`` argument.
    """
    with log_tool_call("record_holdout_result", run_id=run_id, weapon_id=weapon_id):
        _run_store.record_holdout_result(run_id, weapon_id, holdout_result)
        return {"status": "ok"}


@mcp.tool()
def read_holdout_results(run_id: str, weapon_id: str) -> list[HoldoutResult]:
    """Return every ``HoldoutResult`` previously appended for this weapon.

    Called by the Orchestrator when assembling reports. Must NOT be called
    from the Attacker or Inspector role — holdout outcomes carry blocker
    semantics and reading them collapses the train/test split.
    """
    with log_tool_call("read_holdout_results", run_id=run_id, weapon_id=weapon_id):
        return _run_store.read_holdout_results(run_id, weapon_id)


@mcp.tool()
def assemble_final_clearance(
    run_id: str,
    clearance_threshold: float = 0.90,
    weapon_ids: list[str] | None = None,
) -> FinalClearance:
    """Aggregate every per-weapon report in a run into one overall clearance.

    Reads the run buffer for every weapon declared at ``start_run`` time
    (override with ``weapon_ids`` if you only want a subset), assembles a
    per-weapon ``RiskReport`` + ``Clearance`` for each, and reduces them to
    a single ``FinalClearance``.

    Aggregation rules (see :class:`FinalClearance`):

    - ``overall_confidence`` = min over per-weapon confidence_score and
      holdout_satisfaction_score (weakest link dominates).
    - ``max_risk_level`` = max severity across per-weapon risk levels.
    - ``final_recommendation`` = ``pass`` only when threshold is met AND no
      medium- or high-risk weapons; ``conditional`` when threshold is met
      with medium-risk weapons but no high-risk; ``block`` otherwise.

    Allow this tool only in the Orchestrator role. Attacker and Inspector
    contexts must not see per-weapon reports — they carry confirmed-failure
    text that paraphrases blocker semantics.
    """
    with log_tool_call("assemble_final_clearance", run_id=run_id):
        weapons = list(weapon_ids) if weapon_ids is not None else _run_store.list_weapon_ids(run_id)

        per_weapon: list[WeaponReport] = []
        for wid in weapons:
            records = _run_store.read_iteration_records(run_id, wid)
            holdouts = [hr.execution_result for hr in _run_store.read_holdout_results(run_id, wid)]
            report, clearance = build_risk_report(records, holdouts, clearance_threshold)
            per_weapon.append(WeaponReport(weapon_id=wid, risk_report=report, clearance=clearance))

        return aggregate_final_clearance(per_weapon, clearance_threshold)


__all__ = [
    "Clearance",
    "FinalClearance",
    "RiskReport",
    "assemble_final_clearance",
    "assemble_run_report",
    "execute_plan",
    "get_weapon",
    "list_weapons",
    "main",
    "mcp",
    "read_holdout_results",
    "read_iteration_records",
    "record_holdout_result",
    "record_iteration",
    "start_run",
]


def main() -> None:
    """Run the MCP server over stdio (the Claude Code transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
