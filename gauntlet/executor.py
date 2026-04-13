from __future__ import annotations

from .adapters import Adapter
from .models import (
    Action,
    Assertion,
    AssertionResult,
    ExecutionResult,
    ExecutionStepResult,
    Plan,
)


class Drone:
    def __init__(self, sut: Adapter) -> None:
        self._sut = sut

    def run_plan(self, plan: Plan) -> ExecutionResult:
        step_results: list[ExecutionStepResult] = []
        context: dict[str, object] = {}
        for index, step in enumerate(plan.steps, start=1):
            request = step.request.model_copy(update={"path": step.request.path.format(**context)})
            action = Action.from_http_request(request)
            observation = self._sut.execute(step.user, action)
            response = observation.to_http_response()
            step_results.append(
                ExecutionStepResult(
                    step_index=index,
                    user=step.user,
                    request=request,
                    response=response,
                )
            )
            if request.method == "POST" and request.path == "/tasks" and "id" in response.body:
                context["task_id"] = response.body["id"]

        assertion_results = [
            _evaluate_assertion(assertion, step_results) for assertion in plan.assertions
        ]
        return ExecutionResult(
            plan_name=plan.name,
            category=plan.category,
            goal=plan.goal,
            steps=step_results,
            assertions=assertion_results,
        )


# To add a new assertion kind: add the literal to Assertion.kind in models.py,
# then add a branch below keyed on assertion.kind.
# To add a new rule: add a branch inside the assertion.kind == "rule" block
# keyed on assertion.rule. The rule string is set by the Attacker when it builds
# the Assertion. Add a test case in tests/test_gauntlet.py for either.
def _evaluate_assertion(
    assertion: Assertion, step_results: list[ExecutionStepResult]
) -> AssertionResult:
    step_result = step_results[assertion.step_index - 1]
    if assertion.kind == "status_code":
        passed = step_result.response.status_code == assertion.expected
        return AssertionResult(
            name=assertion.name,
            kind=assertion.kind,
            passed=passed,
            detail=(
                f"expected status {assertion.expected}, got {step_result.response.status_code}"
            ),
        )

    if assertion.rule == "task_not_modified_by_other_user":
        body = step_result.response.body
        last_modified_by = body.get("last_modified_by")
        passed = last_modified_by in (None, body.get("owner"))
        return AssertionResult(
            name=assertion.name,
            kind=assertion.kind,
            passed=passed,
            detail=f"owner={body.get('owner')} last_modified_by={last_modified_by}",
        )

    return AssertionResult(
        name=assertion.name,
        kind=assertion.kind,
        passed=False,
        detail=f"unknown rule: {assertion.rule}",
    )
