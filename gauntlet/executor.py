from __future__ import annotations

from typing import Any

from .http import HttpApi
from .models import (
    Assertion,
    AssertionResult,
    ExecutionResult,
    ExecutionStepResult,
    Plan,
    PlanStep,
)

_MISSING = object()


class Drone:
    def __init__(self, sut: HttpApi) -> None:
        self._sut = sut

    def run_plan(self, plan: Plan) -> ExecutionResult:
        step_results: list[ExecutionStepResult] = []
        context: dict[str, object] = {}
        for index, step in enumerate(plan.steps, start=1):
            request = step.request.model_copy(update={"path": step.request.path.format(**context)})
            send_result = self._sut.send(step.user, request)
            step_results.append(
                ExecutionStepResult(
                    step_index=index,
                    user=step.user,
                    request=request,
                    response=send_result.response,
                    duration_ms=send_result.duration_ms,
                    response_size_bytes=send_result.response_size_bytes,
                    response_headers=send_result.response_headers,
                    outcome=send_result.outcome,
                )
            )
            _apply_extractions(step, send_result.response.body, context)

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


def _apply_extractions(step: PlanStep, body: dict[str, Any], context: dict[str, object]) -> None:
    """Write template-variable captures from ``body`` into ``context``.

    Generic ``step.extract`` entries are applied first. The ``/tasks`` →
    ``task_id`` shortcut is a legacy-compat carve-out for plans written before
    ``extract`` existed; new plans should set ``extract={"task_id": "id"}``
    explicitly instead of relying on the hardcoded path match.
    """
    for var_name, body_path in step.extract.items():
        value = _lookup_dotted(body, body_path)
        if value is not _MISSING:
            context[var_name] = value

    # Legacy backward-compat: pre-``extract`` plans that POST to /tasks used to
    # auto-populate {task_id}. Only kick in when the caller didn't opt into
    # explicit extraction, so new plans retain full control.
    if (
        not step.extract
        and step.request.method == "POST"
        and step.request.path == "/tasks"
        and "id" in body
    ):
        context["task_id"] = body["id"]


def _lookup_dotted(body: dict[str, Any], path: str) -> Any:
    """Return the value at ``path`` inside ``body`` or ``_MISSING``.

    ``path`` is a dotted key like ``id`` or ``data.id``. Any missing segment
    or non-dict traversal short-circuits to ``_MISSING``.
    """
    current: Any = body
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


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
