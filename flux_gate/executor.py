from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol

import requests as http

from .models import (
    Assertion,
    AssertionResult,
    ExecutionResult,
    ExecutionStepResult,
    HttpRequest,
    HttpResponse,
    Scenario,
)


class SystemUnderTest(Protocol):
    def send(self, actor: str, request: HttpRequest) -> HttpResponse: ...


class HttpExecutor:
    """Sends real HTTP requests to a locally-running API process.

    Each actor is identified by an ``X-Actor`` header by default. Pass
    ``actor_headers`` to override with bearer tokens or session cookies.

    Example::

        executor = HttpExecutor(
            "http://localhost:8000",
            actor_headers={
                "userA": {"Authorization": "Bearer token-a"},
                "userB": {"Authorization": "Bearer token-b"},
            },
        )
    """

    def __init__(
        self,
        base_url: str,
        actor_headers: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._actor_headers = actor_headers or {}

    def send(self, actor: str, request: HttpRequest) -> HttpResponse:
        headers = {"X-Actor": actor, **self._actor_headers.get(actor, {})}
        resp = http.request(
            request.method,
            f"{self._base_url}{request.path}",
            json=request.body if request.body else None,
            headers=headers,
            timeout=10,
        )
        body: dict[str, Any]
        try:
            body = resp.json()
        except ValueError:
            body = {"_raw": resp.text}
        return HttpResponse(status_code=resp.status_code, body=body)


class InMemoryTaskAPI:
    """Demo REST API with an intentional authorization flaw."""

    def __init__(self) -> None:
        self._tasks: dict[int, dict[str, object]] = {}
        self._next_id = 1

    def send(self, actor: str, request: HttpRequest) -> HttpResponse:
        if request.method == "POST" and request.path == "/tasks":
            return self._create_task(actor, request)
        if request.method == "GET" and request.path.startswith("/tasks/"):
            return self._get_task(actor, request)
        if request.method == "PATCH" and request.path.startswith("/tasks/"):
            return self._patch_task(actor, request)
        return HttpResponse(status_code=404, body={"error": "not_found"})

    def _create_task(self, actor: str, request: HttpRequest) -> HttpResponse:
        task_id = self._next_id
        self._next_id += 1
        task = {
            "id": task_id,
            "owner": actor,
            "title": request.body.get("title", ""),
            "completed": bool(request.body.get("completed", False)),
        }
        self._tasks[task_id] = task
        return HttpResponse(status_code=201, body=deepcopy(task))

    def _get_task(self, actor: str, request: HttpRequest) -> HttpResponse:
        task = self._tasks.get(_task_id_from_path(request.path))
        if task is None:
            return HttpResponse(status_code=404, body={"error": "not_found"})
        if task["owner"] != actor:
            return HttpResponse(status_code=403, body={"error": "forbidden"})
        return HttpResponse(status_code=200, body=deepcopy(task))

    def _patch_task(self, actor: str, request: HttpRequest) -> HttpResponse:
        task = self._tasks.get(_task_id_from_path(request.path))
        if task is None:
            return HttpResponse(status_code=404, body={"error": "not_found"})

        # This is the seeded flaw that the adversarial loop should surface.
        task["title"] = request.body.get("title", task["title"])
        task["completed"] = request.body.get("completed", task["completed"])
        task["last_modified_by"] = actor
        return HttpResponse(status_code=200, body=deepcopy(task))


def _task_id_from_path(path: str) -> int:
    return int(path.rsplit("/", maxsplit=1)[-1])


class DeterministicLocalExecutor:
    def __init__(self, sut: SystemUnderTest) -> None:
        self._sut = sut

    def run_scenario(self, scenario: Scenario) -> ExecutionResult:
        step_results: list[ExecutionStepResult] = []
        context: dict[str, object] = {}
        for index, step in enumerate(scenario.steps, start=1):
            request = step.request.model_copy(update={"path": step.request.path.format(**context)})
            response = self._sut.send(step.actor, request)
            step_results.append(
                ExecutionStepResult(
                    step_index=index,
                    actor=step.actor,
                    request=request,
                    response=response,
                )
            )
            if request.method == "POST" and request.path == "/tasks" and "id" in response.body:
                context["task_id"] = response.body["id"]

        assertion_results = [
            _evaluate_assertion(assertion, step_results) for assertion in scenario.assertions
        ]
        return ExecutionResult(
            scenario_name=scenario.name,
            category=scenario.category,
            goal=scenario.goal,
            steps=step_results,
            assertions=assertion_results,
        )


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
        detail=f"unknown guard rule: {assertion.rule}",
    )
