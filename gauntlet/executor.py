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


def _evaluate_assertion(
    assertion: Assertion, step_results: list[ExecutionStepResult]
) -> AssertionResult:
    step_result = step_results[assertion.step_index - 1]
    passed = step_result.response.status_code == assertion.expected
    return AssertionResult(
        name=assertion.name,
        passed=passed,
        detail=f"expected status {assertion.expected}, got {step_result.response.status_code}",
    )
