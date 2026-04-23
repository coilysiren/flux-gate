"""Tests for the HoldoutEvaluator plan plausibility heuristics."""

from __future__ import annotations

from pathlib import Path

import pytest

from gauntlet import (
    Assertion,
    AssertionResult,
    ExecutionResult,
    ExecutionStepResult,
    HoldoutResult,
    HttpRequest,
    HttpResponse,
    Plan,
    PlanStep,
)
from gauntlet._plausibility import check_holdout_plausibility
from gauntlet.server import record_holdout_result, start_run

# ---------------------------------------------------------------------------
# Plan builders shared across the heuristic tests.
# ---------------------------------------------------------------------------


def _single_user_plan() -> Plan:
    return Plan(
        name="p",
        category="authz",
        goal="g",
        steps=[
            PlanStep(
                user="userA",
                request=HttpRequest(method="GET", path="/tasks"),
            ),
        ],
        assertions=[
            Assertion(name="ok", kind="status_code", expected=200, step_index=1),
        ],
    )


def _two_user_plan() -> Plan:
    return Plan(
        name="p",
        category="authz",
        goal="g",
        steps=[
            PlanStep(user="userA", request=HttpRequest(method="POST", path="/tasks")),
            PlanStep(
                user="userB",
                request=HttpRequest(method="PATCH", path="/tasks/{task_id}"),
            ),
        ],
        assertions=[
            Assertion(name="blocked", kind="status_code", expected=403, step_index=2),
        ],
    )


# ---------------------------------------------------------------------------
# Cross-user heuristic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "A non-owner must be rejected with 403",
        "other user cannot modify",
        "different user is blocked",
        "cross-user writes fail",
        "another user cannot read",
    ],
)
def test_cross_user_blocker_flags_single_user_plan(phrase: str) -> None:
    warnings = check_holdout_plausibility(phrase, _single_user_plan())
    assert any("cross-user" in w.lower() or "one distinct user" in w for w in warnings)


def test_cross_user_blocker_satisfied_by_two_user_plan() -> None:
    warnings = check_holdout_plausibility(
        "A non-owner must be rejected with 403",
        _two_user_plan(),
    )
    # With two distinct users, the cross-user heuristic should not fire.
    assert not any("one distinct user" in w for w in warnings)


def test_non_cross_user_blocker_does_not_flag() -> None:
    plan = _single_user_plan()
    warnings = check_holdout_plausibility("GET /tasks returns 200", plan)
    # Neither the cross-user, status, nor method heuristics should fire.
    assert warnings == []


# ---------------------------------------------------------------------------
# Status-code heuristic
# ---------------------------------------------------------------------------


def test_status_code_blocker_flags_missing_assertion() -> None:
    plan = Plan(
        name="p",
        category="authz",
        goal="g",
        steps=[
            PlanStep(user="userA", request=HttpRequest(method="GET", path="/tasks/1")),
        ],
        assertions=[
            Assertion(name="ok", kind="status_code", expected=200, step_index=1),
        ],
    )
    warnings = check_holdout_plausibility("must return 404 when task missing", plan)
    assert any("404" in w and "no assertion" in w for w in warnings)


def test_status_code_blocker_matches_assertion() -> None:
    plan = Plan(
        name="p",
        category="authz",
        goal="g",
        steps=[
            PlanStep(user="userA", request=HttpRequest(method="GET", path="/tasks/1")),
        ],
        assertions=[
            Assertion(name="ok", kind="status_code", expected=404, step_index=1),
        ],
    )
    warnings = check_holdout_plausibility("must return 404 when task missing", plan)
    assert not any("404" in w and "no assertion" in w for w in warnings)


def test_status_code_blocker_with_multiple_codes_reports_only_missing() -> None:
    plan = Plan(
        name="p",
        category="authz",
        goal="g",
        steps=[
            PlanStep(user="userA", request=HttpRequest(method="GET", path="/tasks/1")),
        ],
        assertions=[
            Assertion(name="ok", kind="status_code", expected=403, step_index=1),
        ],
    )
    # 403 is present; 404 is missing.
    warnings = check_holdout_plausibility("403 for unauthorized, 404 for missing", plan)
    joined = " ".join(warnings)
    assert "404" in joined
    # Should only mention 404, not 403.
    assert not any("403" in w and "no assertion" in w for w in warnings)


# ---------------------------------------------------------------------------
# Method heuristic
# ---------------------------------------------------------------------------


def test_method_blocker_flags_missing_method() -> None:
    plan = Plan(
        name="p",
        category="authz",
        goal="g",
        steps=[
            PlanStep(user="userA", request=HttpRequest(method="GET", path="/tasks/1")),
        ],
        assertions=[],
    )
    warnings = check_holdout_plausibility(
        "DELETE on someone else's task must 403",
        plan,
    )
    assert any("DELETE" in w and "no step" in w for w in warnings)


def test_method_blocker_satisfied_by_matching_step() -> None:
    plan = Plan(
        name="p",
        category="authz",
        goal="g",
        steps=[
            PlanStep(user="userA", request=HttpRequest(method="PATCH", path="/tasks/1")),
        ],
        assertions=[],
    )
    warnings = check_holdout_plausibility("PATCH must return 403", plan)
    assert not any("PATCH" in w and "no step" in w for w in warnings)


def test_method_heuristic_is_case_insensitive_against_lowercase_blocker() -> None:
    plan = Plan(
        name="p",
        category="authz",
        goal="g",
        steps=[
            PlanStep(user="userA", request=HttpRequest(method="GET", path="/tasks/1")),
        ],
        assertions=[],
    )
    # Lowercase 'delete' in prose still triggers the method heuristic.
    warnings = check_holdout_plausibility("delete must be rejected", plan)
    assert any("DELETE" in w for w in warnings)


# ---------------------------------------------------------------------------
# Integration: record_holdout_result surfaces warnings
# ---------------------------------------------------------------------------


def _execution_result_single_user() -> ExecutionResult:
    request = HttpRequest(method="GET", path="/tasks")
    response = HttpResponse(status_code=200, body={})
    return ExecutionResult(
        plan_name="single_user_plan",
        category="authz",
        goal="g",
        steps=[
            ExecutionStepResult(step_index=1, user="userA", request=request, response=response),
        ],
        assertions=[
            AssertionResult(
                name="ok",
                kind="status_code",
                passed=True,
                detail="expected status 200, got 200",
            ),
        ],
    )


def test_record_holdout_result_returns_warnings_for_cross_user_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    out = start_run(trial_ids=["trial_a"])
    run_id = out["run_id"]

    holdout = HoldoutResult(
        trial_id="trial_a",
        blocker_index=0,
        blocker="A non-owner must be rejected with 403",
        execution_result=_execution_result_single_user(),
    )
    result = record_holdout_result(
        run_id=run_id,
        trial_id="trial_a",
        holdout_result=holdout,
    )
    assert result["status"] == "ok"
    warnings = result["warnings"]
    assert isinstance(warnings, list)
    assert warnings, "expected at least one plausibility warning"
    # Both the cross-user heuristic and the 403 heuristic should fire.
    joined = " ".join(warnings)
    assert "cross-user" in joined.lower() or "one distinct user" in joined
    assert "403" in joined


def test_record_holdout_result_returns_empty_warnings_when_plausible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    out = start_run(trial_ids=["trial_a"])
    run_id = out["run_id"]

    request_a = HttpRequest(method="POST", path="/tasks", body={"title": "x"})
    request_b = HttpRequest(method="PATCH", path="/tasks/1", body={})
    execution = ExecutionResult(
        plan_name="two_user_plan",
        category="authz",
        goal="g",
        steps=[
            ExecutionStepResult(
                step_index=1,
                user="userA",
                request=request_a,
                response=HttpResponse(status_code=201, body={"id": 1}),
            ),
            ExecutionStepResult(
                step_index=2,
                user="userB",
                request=request_b,
                response=HttpResponse(status_code=403, body={}),
            ),
        ],
        assertions=[
            AssertionResult(
                name="unauthorized_patch",
                kind="status_code",
                passed=True,
                detail="expected status 403, got 403",
            ),
        ],
    )
    holdout = HoldoutResult(
        trial_id="trial_a",
        blocker_index=0,
        blocker="A non-owner's PATCH must be rejected with 403",
        execution_result=execution,
    )
    result = record_holdout_result(
        run_id=run_id,
        trial_id="trial_a",
        holdout_result=holdout,
    )
    assert result["status"] == "ok"
    assert result["warnings"] == []


def test_record_holdout_result_without_blocker_has_no_warnings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    out = start_run(trial_ids=["trial_a"])
    run_id = out["run_id"]

    # HoldoutResult may carry no blocker text; plausibility has nothing to
    # check against, so warnings must be empty.
    holdout = HoldoutResult(
        trial_id="trial_a",
        execution_result=_execution_result_single_user(),
    )
    result = record_holdout_result(
        run_id=run_id,
        trial_id="trial_a",
        holdout_result=holdout,
    )
    assert result == {"status": "ok", "warnings": []}
