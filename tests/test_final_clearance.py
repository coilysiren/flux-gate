from __future__ import annotations

from pathlib import Path

import pytest

from gauntlet import (
    Clearance,
    HoldoutResult,
    HttpRequest,
    IterationRecord,
    Plan,
    PlanStep,
    RiskReport,
    RunStore,
    WeaponReport,
    aggregate_final_clearance,
    build_default_iteration_specs,
)
from gauntlet.server import (
    assemble_final_clearance,
    record_holdout_result,
    record_iteration,
    start_run,
)

from ._factories import make_execution_result

# ---------------------------------------------------------------------------
# Direct aggregator (loop-level)
# ---------------------------------------------------------------------------


def _wr(
    weapon_id: str,
    *,
    confidence: float,
    risk_level: str = "low",
    confirmed: list[str] | None = None,
    holdout_score: float | None = None,
) -> WeaponReport:
    report = RiskReport(
        confidence_score=confidence,
        risk_level=risk_level,  # type: ignore[arg-type]
        summary=confirmed or ["no confirmed failures detected"],
        confirmed_failures=confirmed or [],
        suspicious_patterns=[],
        unexplored_surfaces=[],
        anomalies=[],
        coverage=[],
        conclusion="ok" if not confirmed else "fail",
    )
    clearance: Clearance | None
    if holdout_score is not None:
        clearance = Clearance(
            passed=holdout_score >= 0.9,
            holdout_satisfaction_score=holdout_score,
            threshold=0.9,
            recommendation="pass" if holdout_score >= 0.9 else "block",
            rationale="test",
        )
    else:
        clearance = None
    return WeaponReport(weapon_id=weapon_id, risk_report=report, clearance=clearance)


def test_pass_when_high_confidence_and_low_risk_only() -> None:
    final = aggregate_final_clearance(
        [
            _wr("a", confidence=0.95, risk_level="low", holdout_score=1.0),
            _wr("b", confidence=0.92, risk_level="low", holdout_score=0.95),
        ],
        clearance_threshold=0.9,
    )
    assert final.final_recommendation == "pass"
    assert final.max_risk_level == "low"
    assert final.overall_confidence == 0.92  # min of all signals
    assert final.all_confirmed_failures == []


def test_conditional_when_threshold_met_but_medium_findings_present() -> None:
    final = aggregate_final_clearance(
        [
            _wr("a", confidence=0.95, risk_level="low", holdout_score=1.0),
            _wr(
                "b",
                confidence=0.91,
                risk_level="medium",
                confirmed=["weak_validation"],
                holdout_score=0.95,
            ),
        ],
        clearance_threshold=0.9,
    )
    assert final.final_recommendation == "conditional"
    assert final.max_risk_level == "medium"
    assert "weak_validation" in final.all_confirmed_failures


def test_block_when_any_high_severity() -> None:
    final = aggregate_final_clearance(
        [
            _wr("a", confidence=0.99, risk_level="low", holdout_score=1.0),
            _wr(
                "b",
                confidence=0.99,
                risk_level="high",
                confirmed=["auth_bypass"],
                holdout_score=1.0,
            ),
        ],
        clearance_threshold=0.9,
    )
    assert final.final_recommendation == "block"
    assert final.max_risk_level == "high"
    assert "auth_bypass" in final.all_confirmed_failures


def test_block_when_below_threshold_even_with_low_risk() -> None:
    final = aggregate_final_clearance(
        [_wr("a", confidence=0.5, risk_level="low", holdout_score=0.5)],
        clearance_threshold=0.9,
    )
    assert final.final_recommendation == "block"
    assert final.overall_confidence == 0.5


def test_empty_run_blocks() -> None:
    final = aggregate_final_clearance([], clearance_threshold=0.9)
    assert final.final_recommendation == "block"
    assert final.per_weapon_reports == []


def test_overall_confidence_is_weakest_link() -> None:
    final = aggregate_final_clearance(
        [
            _wr("a", confidence=0.99, holdout_score=1.0),
            _wr("b", confidence=0.7, holdout_score=1.0),
        ],
        clearance_threshold=0.9,
    )
    assert final.overall_confidence == 0.7


def test_holdout_score_drags_overall_below_confidence() -> None:
    """Even with high confidence, a low holdout score blocks."""
    final = aggregate_final_clearance(
        [_wr("a", confidence=0.99, holdout_score=0.4)],
        clearance_threshold=0.9,
    )
    assert final.overall_confidence == 0.4
    assert final.final_recommendation == "block"


# ---------------------------------------------------------------------------
# MCP tool integration with the run buffer
# ---------------------------------------------------------------------------


_AUTHZ_PLAN = Plan(
    name="user_cannot_modify_other_users_task",
    category="authz",
    goal="cross-user modification should be rejected",
    steps=[
        PlanStep(
            user="userA",
            request=HttpRequest(method="POST", path="/tasks", body={"title": "private task"}),
        ),
    ],
    assertions=[],
)


def _seed_weapon(store: RunStore, run_id: str, weapon_id: str) -> None:
    execution = make_execution_result(plan_name=_AUTHZ_PLAN.name)
    record = IterationRecord(
        spec=build_default_iteration_specs()[0],
        plans=[_AUTHZ_PLAN],
        execution_results=[execution],
        findings=[],
    )
    record_iteration(
        run_id=run_id,
        weapon_id=weapon_id,
        iteration_record=record,
        runs_path=str(store._root),  # internal access OK in test
    )
    record_holdout_result(
        run_id=run_id,
        weapon_id=weapon_id,
        holdout_result=HoldoutResult(weapon_id=weapon_id, execution_result=execution),
        runs_path=str(store._root),
    )


def test_assemble_final_clearance_via_mcp(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    out = start_run(weapon_ids=["weapon_a", "weapon_b"], runs_path=str(tmp_path))
    run_id = out["run_id"]
    _seed_weapon(store, run_id, "weapon_a")
    _seed_weapon(store, run_id, "weapon_b")

    final = assemble_final_clearance(
        run_id=run_id, clearance_threshold=0.9, runs_path=str(tmp_path)
    )

    assert len(final.per_weapon_reports) == 2
    assert {wr.weapon_id for wr in final.per_weapon_reports} == {"weapon_a", "weapon_b"}
    # _AUTHZ_PLAN has no assertions so satisfaction_score=1.0; reports should pass
    # the holdout but the iterations have no findings so risk_level stays low.
    assert final.max_risk_level == "low"


def test_assemble_final_clearance_subset(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    out = start_run(weapon_ids=["weapon_a", "weapon_b"], runs_path=str(tmp_path))
    run_id = out["run_id"]
    _seed_weapon(store, run_id, "weapon_a")

    final = assemble_final_clearance(
        run_id=run_id,
        clearance_threshold=0.9,
        weapon_ids=["weapon_a"],
        runs_path=str(tmp_path),
    )
    assert len(final.per_weapon_reports) == 1
    assert final.per_weapon_reports[0].weapon_id == "weapon_a"


def test_assemble_final_clearance_unknown_run_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No run"):
        assemble_final_clearance(run_id="nope", runs_path=str(tmp_path))
