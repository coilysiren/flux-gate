from __future__ import annotations

from pathlib import Path

import pytest

from gauntlet import (
    Clearance,
    HoldoutResult,
    HttpRequest,
    IterationRecord,
    IterationSpec,
    Plan,
    PlanStep,
    RiskReport,
    TrialReport,
    aggregate_final_clearance,
)
from gauntlet.server import (
    assemble_final_clearance,
    record_holdout_result,
    record_iteration,
    start_run,
)

from ._factories import make_execution_result


def _spec(name: str = "baseline") -> IterationSpec:
    return IterationSpec(index=1, name=name, goal=name)


# ---------------------------------------------------------------------------
# Direct aggregator (loop-level)
# ---------------------------------------------------------------------------


def _wr(
    trial_id: str,
    *,
    confidence: float,
    risk_level: str = "low",
    confirmed: list[str] | None = None,
    holdout_score: float | None = None,
) -> TrialReport:
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
    return TrialReport(trial_id=trial_id, risk_report=report, clearance=clearance)


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
    assert final.per_trial_reports == []


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


def _seed_trial(run_id: str, trial_id: str) -> None:
    execution = make_execution_result(plan_name=_AUTHZ_PLAN.name)
    record = IterationRecord(
        spec=_spec(),
        plans=[_AUTHZ_PLAN],
        execution_results=[execution],
        findings=[],
    )
    record_iteration(run_id=run_id, trial_id=trial_id, iteration_record=record)
    record_holdout_result(
        run_id=run_id,
        trial_id=trial_id,
        holdout_result=HoldoutResult(trial_id=trial_id, execution_result=execution),
    )


def test_assemble_final_clearance_via_mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    out = start_run(trial_ids=["trial_a", "trial_b"])
    run_id = out["run_id"]
    _seed_trial(run_id, "trial_a")
    _seed_trial(run_id, "trial_b")

    final = assemble_final_clearance(run_id=run_id, clearance_threshold=0.9)

    assert len(final.per_trial_reports) == 2
    assert {wr.trial_id for wr in final.per_trial_reports} == {"trial_a", "trial_b"}
    # _AUTHZ_PLAN has no assertions so satisfaction_score=1.0; reports should pass
    # the holdout but the iterations have no findings so risk_level stays low.
    assert final.max_risk_level == "low"


def test_assemble_final_clearance_subset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    out = start_run(trial_ids=["trial_a", "trial_b"])
    run_id = out["run_id"]
    _seed_trial(run_id, "trial_a")

    final = assemble_final_clearance(
        run_id=run_id,
        clearance_threshold=0.9,
        trial_ids=["trial_a"],
    )
    assert len(final.per_trial_reports) == 1
    assert final.per_trial_reports[0].trial_id == "trial_a"


def test_assemble_final_clearance_unknown_run_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="No run"):
        assemble_final_clearance(run_id="nope")
