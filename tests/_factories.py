"""Hand-built model factories used across tests.

Replaces the InMemoryHttpApi+Drone pipeline that earlier tests used to
produce ExecutionResults. With the in-memory adapter gone, tests that
need an ExecutionResult build one directly — they don't need a real
HTTP roundtrip to assert buffer behaviour, risk-report shape, or
clearance aggregation.
"""

from __future__ import annotations

from gauntlet import (
    AssertionResult,
    ExecutionResult,
    ExecutionStepResult,
    HttpRequest,
    HttpResponse,
)


def make_execution_result(
    *,
    plan_name: str = "synthetic_plan",
    category: str = "authz",
    goal: str = "synthetic test plan",
    passing: bool = False,
) -> ExecutionResult:
    """Return an ExecutionResult with one step and one assertion.

    ``passing=True`` produces satisfaction_score=1.0; ``passing=False``
    produces satisfaction_score=0.0. The exact requests are arbitrary —
    callers that care about specifics should build the model directly.
    """
    request = HttpRequest(method="POST", path="/tasks", body={"title": "x"})
    response = HttpResponse(status_code=201 if passing else 200, body={"id": 1})
    step = ExecutionStepResult(step_index=1, user="userA", request=request, response=response)
    assertion = AssertionResult(
        name="status_code_matches",
        passed=passing,
        detail=f"expected 201, got {response.status_code}",
    )
    return ExecutionResult(
        plan_name=plan_name,
        category=category,
        goal=goal,
        steps=[step],
        assertions=[assertion],
    )
