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

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from mcp.server.fastmcp import FastMCP

from ._findings_store import DEFAULT_FINDINGS_PATH, FindingsStore
from ._log import configure_logging, log_tool_call
from ._mutator import mutate_plans as _mutate_plans
from ._plausibility import check_holdout_plausibility
from .executor import Drone
from .http import HttpApi
from .loop import aggregate_final_clearance, build_risk_report
from .models import (
    Assertion,
    Clearance,
    ExecutionResult,
    FinalClearance,
    HoldoutResult,
    IterationRecord,
    Plan,
    PlanStep,
    RiskReport,
    Weapon,
    WeaponReport,
)
from .runs import RunStore

configure_logging()

mcp = FastMCP("gauntlet")

_DEFAULT_WEAPONS_PATH = ".gauntlet/weapons"

_LOG = logging.getLogger(__name__)

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

    Side effect: confirmed-failure ``Finding``s from the iteration buffer
    are persisted to the cross-run ``FindingsStore`` so
    ``recurring_failures`` can surface repeated issues. Store writes are
    wrapped in a try/except and logged; a failure to write never aborts
    the report call.
    """
    with log_tool_call("assemble_run_report", run_id=run_id, weapon_id=weapon_id):
        records = _run_store.read_iteration_records(run_id, weapon_id)
        holdouts = [
            hr.execution_result for hr in _run_store.read_holdout_results(run_id, weapon_id)
        ]

        report, clearance = build_risk_report(records, holdouts, clearance_threshold)

        try:
            store = FindingsStore(DEFAULT_FINDINGS_PATH)
            confirmed_issues = set(report.confirmed_failures)
            for record in records:
                for finding in record.findings:
                    if finding.is_anomaly:
                        continue
                    if finding.issue not in confirmed_issues:
                        continue
                    store.record(weapon_id, run_id, finding)
        except Exception as exc:  # pragma: no cover - defensive
            _LOG.warning(
                "Failed to persist findings to cross-run store (run_id=%s weapon_id=%s): %s",
                run_id,
                weapon_id,
                exc,
            )

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


_DETAIL_EXPECTED_RE = re.compile(r"expected(?:\s+status)?\s+(\d{3})")


def _plan_from_holdout(holdout_result: HoldoutResult) -> Plan:
    """Reconstruct a Plan from the HoldoutResult's ExecutionResult.

    ``HoldoutResult`` stores the executed ``ExecutionResult`` rather than the
    original ``Plan``; the plausibility checker's signature takes a ``Plan``.
    We rebuild one here with enough fidelity for the heuristics: per-step
    ``user`` + ``request``, and ``Assertion.expected`` parsed out of
    ``AssertionResult.detail`` (emitted by the Drone in a stable form).
    """
    er = holdout_result.execution_result
    steps = [PlanStep(user=step.user, request=step.request) for step in er.steps]
    assertions: list[Assertion] = []
    for ar in er.assertions:
        expected: int | None = None
        match = _DETAIL_EXPECTED_RE.search(ar.detail)
        if match:
            expected = int(match.group(1))
        assertions.append(
            Assertion(
                kind="status_code",
                name=ar.name,
                expected=expected,
                # step_index is required; default to 1 if the detail didn't
                # preserve it. Plausibility only reads assertion.expected.
                step_index=1,
            )
        )
    return Plan(
        name=er.plan_name,
        category=er.category,
        goal=er.goal,
        steps=steps,
        assertions=assertions,
    )


@mcp.tool()
def record_holdout_result(
    run_id: str,
    weapon_id: str,
    holdout_result: HoldoutResult,
) -> dict[str, Any]:
    """Append one ``HoldoutResult`` to the weapon's holdout buffer.

    Called only by the HoldoutEvaluator after executing one acceptance plan
    derived from a weapon's blocker. ``HoldoutResult.weapon_id`` must match
    the ``weapon_id`` argument.

    Returns ``{status, warnings}`` where ``warnings`` is a (possibly empty)
    list of human-readable strings flagged by heuristic plausibility checks
    against the plan. Warnings fire when the blocker references cross-user
    behavior, a specific HTTP status code, or an HTTP method that the plan
    doesn't obviously exercise. False positives are expected; the host
    decides whether to surface them.
    """
    with log_tool_call("record_holdout_result", run_id=run_id, weapon_id=weapon_id):
        _run_store.record_holdout_result(run_id, weapon_id, holdout_result)
        warnings: list[str] = []
        if holdout_result.blocker:
            reconstructed = _plan_from_holdout(holdout_result)
            warnings = check_holdout_plausibility(holdout_result.blocker, reconstructed)
        return {"status": "ok", "warnings": warnings}


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
def recurring_failures(
    weapon_id: str,
    lookback: int = 5,
    findings_path: str = DEFAULT_FINDINGS_PATH,
) -> list[dict[str, Any]]:
    """Return issues seen in ≥ 2 of the last ``lookback`` runs for a weapon.

    Reads ``<findings_path>/<weapon_id>.jsonl`` (populated as a side effect
    of ``assemble_run_report``) and groups findings by ``issue`` across the
    most recent ``lookback`` distinct run ids. Returns one entry per
    recurring issue: ``{issue, occurrences, run_ids}``, sorted by
    occurrence count descending then issue ascending.

    Intended for the Orchestrator (host skill) to surface "this same
    confirmed_failure showed up in 3 of the last 5 runs" signal. Not
    allowlisted for any per-role subagent.
    """
    return FindingsStore(findings_path).recurring(weapon_id, lookback=lookback)


@mcp.tool()
def mutate_plans(
    run_id: str,
    weapon_id: str,
    max_variants: int = 4,
) -> list[Plan]:
    """Return deterministic plan variants derived from prior iterations.

    Reads every ``IterationRecord`` previously appended for ``(run_id,
    weapon_id)``, collects the unique plans across them (by ``name``), and
    runs them through the internal mutator. The mutator applies four
    strategies (drop a body field, rotate users, negate expected status,
    reverse step order) and returns up to ``max_variants`` plan variants
    whose names are suffixed with ``:mut-<strategy>``.

    The Attacker subagent calls this between iterations to explore variants
    of plans that have already landed, without spending LLM tokens on the
    mutation step. The mutator sees only what the Attacker has already
    seen, so there is no train/test split risk.
    """
    records = _run_store.read_iteration_records(run_id, weapon_id)
    seen: dict[str, Plan] = {}
    for record in records:
        for plan in record.plans:
            seen.setdefault(plan.name, plan)
    seed_plans = list(seen.values())
    return _mutate_plans(seed_plans, max_variants=max_variants)


@mcp.tool()
def replay_finding(
    run_id: str,
    weapon_id: str,
    finding_index: int,
    url: str,
    user_headers: dict[str, dict[str, str]] | None = None,
) -> ExecutionResult:
    """Re-execute the ``ReplayBundle`` of a stored finding against the SUT.

    Walks the weapon's iteration records in append order, flattens their
    findings, and picks the ``finding_index``-th entry (0-indexed). The
    finding must carry a populated ``replay_bundle`` — ``ReplayBundle.steps``
    are converted 1:1 into a ``Plan`` with ``category="replay"`` and no
    assertions, then executed through the normal Drone path.

    Useful for "did the fix actually work" loops: the host picks a stored
    finding, calls ``replay_finding`` against a patched SUT, and checks
    whether the reproduced ``ExecutionResult`` still shows the failure.

    Raises ``ValueError`` if the index is out of range or the targeted
    finding has no ``replay_bundle``.
    """
    records = _run_store.read_iteration_records(run_id, weapon_id)
    findings = [finding for record in records for finding in record.findings]
    if finding_index < 0 or finding_index >= len(findings):
        raise ValueError(
            f"finding_index {finding_index} out of range; "
            f"only {len(findings)} findings recorded for weapon {weapon_id!r}"
        )
    finding = findings[finding_index]
    if finding.replay_bundle is None:
        raise ValueError(
            f"Finding {finding.issue!r} has no replay_bundle; cannot replay. "
            "The Inspector should populate replay_bundle on every finding "
            "by copying ReplayStep data from the offending ExecutionStepResult(s)."
        )
    plan = Plan(
        name=f"replay:{finding.issue}",
        category="replay",
        goal="reproduce finding",
        steps=[PlanStep(user=s.user, request=s.request) for s in finding.replay_bundle.steps],
        assertions=[],
    )
    drone = Drone(HttpApi(url, user_headers=user_headers or {}))
    return drone.run_plan(plan)


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
    "mutate_plans",
    "read_holdout_results",
    "read_iteration_records",
    "record_holdout_result",
    "record_iteration",
    "recurring_failures",
    "replay_finding",
    "start_run",
]


def main() -> None:
    """Run the MCP server over stdio (the Claude Code transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
