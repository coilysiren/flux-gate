"""Property-based invariants for ``gauntlet.loop``.

Hypothesis-driven sanity checks for ``_confidence_score``,
``aggregate_final_clearance``, and ``build_risk_report``. These assert
structural properties (boundedness, determinism, "any high-severity per-trial
blocks the final"), not specific numeric outputs.
"""

from __future__ import annotations

import math
from typing import Literal

from hypothesis import given, settings
from hypothesis import strategies as st

from gauntlet import (
    Clearance,
    Finding,
    IterationRecord,
    IterationSpec,
    Plan,
    RiskReport,
    TrialReport,
    aggregate_final_clearance,
    build_risk_report,
)
from gauntlet.loop import _confidence_score

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


def _plan_strategy() -> st.SearchStrategy[Plan]:
    return st.builds(
        Plan,
        name=st.text(min_size=1, max_size=10),
        category=st.sampled_from(["authz", "input", "state", "race", "idempotency"]),
        goal=st.text(min_size=1, max_size=20),
        steps=st.just([]),
        assertions=st.just([]),
        trial_id=st.none(),
    )


def _finding_strategy() -> st.SearchStrategy[Finding]:
    return st.builds(
        Finding,
        issue=st.text(min_size=1, max_size=20),
        severity=st.sampled_from(["low", "medium", "high"]),
        confidence=st.floats(min_value=0.0, max_value=1.0),
        rationale=st.text(min_size=1, max_size=20),
        next_targets=st.lists(st.text(min_size=1, max_size=10), max_size=3),
    )


def _iteration_record_strategy() -> st.SearchStrategy[IterationRecord]:
    return st.builds(
        IterationRecord,
        spec=st.builds(
            IterationSpec,
            index=st.integers(min_value=0, max_value=10),
            name=st.text(min_size=1, max_size=10),
            goal=st.text(min_size=1, max_size=20),
        ),
        plans=st.lists(_plan_strategy(), max_size=4),
        execution_results=st.just([]),
        findings=st.lists(_finding_strategy(), max_size=4),
    )


def _risk_report_strategy() -> st.SearchStrategy[RiskReport]:
    return st.builds(
        RiskReport,
        confidence_score=st.floats(min_value=0.0, max_value=1.0),
        risk_level=st.sampled_from(["low", "medium", "high"]),
        summary=st.just(["x"]),
        confirmed_failures=st.lists(st.text(min_size=1, max_size=10), max_size=3),
        suspicious_patterns=st.just([]),
        unexplored_surfaces=st.just([]),
        anomalies=st.just([]),
        coverage=st.just([]),
        conclusion=st.text(min_size=1, max_size=20),
    )


def _clearance_strategy() -> st.SearchStrategy[Clearance | None]:
    score_strategy = st.floats(min_value=0.0, max_value=1.0)

    def _make(score: float) -> Clearance:
        rec: Literal["pass", "conditional", "block"] = "pass" if score >= 0.9 else "block"
        return Clearance(
            passed=score >= 0.9,
            holdout_satisfaction_score=score,
            threshold=0.9,
            recommendation=rec,
            rationale="x",
        )

    return st.one_of(st.none(), score_strategy.map(_make))


def _trial_report_strategy() -> st.SearchStrategy[TrialReport]:
    return st.builds(
        TrialReport,
        trial_id=st.text(min_size=1, max_size=10),
        risk_report=_risk_report_strategy(),
        clearance=_clearance_strategy(),
    )


# ---------------------------------------------------------------------------
# _confidence_score invariants
# ---------------------------------------------------------------------------


@given(records=st.lists(_iteration_record_strategy(), max_size=6), coverage=st.just([]))
@settings(max_examples=50)
def test_confidence_score_bounded(records: list[IterationRecord], coverage: list[str]) -> None:
    score = _confidence_score(records, coverage)
    assert 0.0 <= score <= 1.0


@given(records=st.lists(_iteration_record_strategy(), min_size=1, max_size=6))
@settings(max_examples=50)
def test_confidence_score_finite_on_nonempty_records(records: list[IterationRecord]) -> None:
    score = _confidence_score(records, [])
    assert math.isfinite(score)


@given(records=st.lists(_iteration_record_strategy(), max_size=6))
@settings(max_examples=50)
def test_confidence_score_deterministic(records: list[IterationRecord]) -> None:
    a = _confidence_score(records, [])
    b = _confidence_score(records, [])
    assert a == b


# ---------------------------------------------------------------------------
# aggregate_final_clearance invariants
# ---------------------------------------------------------------------------


@given(per_trial=st.lists(_trial_report_strategy(), max_size=5))
@settings(max_examples=50)
def test_aggregate_overall_confidence_bounded(per_trial: list[TrialReport]) -> None:
    final = aggregate_final_clearance(per_trial, clearance_threshold=0.9)
    assert 0.0 <= final.overall_confidence <= 1.0


@given(per_trial=st.lists(_trial_report_strategy(), min_size=1, max_size=5))
@settings(max_examples=50)
def test_any_high_blocks(per_trial: list[TrialReport]) -> None:
    has_high = any(wr.risk_report.risk_level == "high" for wr in per_trial)
    final = aggregate_final_clearance(per_trial, clearance_threshold=0.9)
    if has_high:
        assert final.final_recommendation == "block"


@given(per_trial=st.lists(_trial_report_strategy(), min_size=1, max_size=5))
@settings(max_examples=50)
def test_pass_requires_no_medium_or_high_and_threshold_met(
    per_trial: list[TrialReport],
) -> None:
    final = aggregate_final_clearance(per_trial, clearance_threshold=0.9)
    if final.final_recommendation == "pass":
        assert all(wr.risk_report.risk_level == "low" for wr in per_trial)
        assert final.overall_confidence >= 0.9


def test_empty_per_trial_blocks() -> None:
    final = aggregate_final_clearance([], clearance_threshold=0.9)
    assert final.final_recommendation == "block"
    assert final.per_trial_reports == []


# ---------------------------------------------------------------------------
# build_risk_report invariants
# ---------------------------------------------------------------------------


def test_build_risk_report_empty_inputs_deterministic() -> None:
    report, clearance = build_risk_report([], [], clearance_threshold=0.9)
    assert clearance is None
    assert report.risk_level == "low"
    # Deterministic: call twice, get same report.
    report2, _ = build_risk_report([], [], clearance_threshold=0.9)
    assert report.model_dump() == report2.model_dump()


@given(records=st.lists(_iteration_record_strategy(), max_size=5))
@settings(max_examples=50)
def test_build_risk_report_confirmed_failures_sorted_and_deduped(
    records: list[IterationRecord],
) -> None:
    report, _ = build_risk_report(records, [], clearance_threshold=0.9)
    assert report.confirmed_failures == sorted(set(report.confirmed_failures))


@given(records=st.lists(_iteration_record_strategy(), max_size=5))
@settings(max_examples=50)
def test_build_risk_report_coverage_sorted_and_deduped(
    records: list[IterationRecord],
) -> None:
    report, _ = build_risk_report(records, [], clearance_threshold=0.9)
    assert report.coverage == sorted(set(report.coverage))
