"""Drone executor tests — path-template extraction and assertion evaluation."""

from __future__ import annotations

from gauntlet import (
    Drone,
    HttpRequest,
    HttpResponse,
    Plan,
    PlanStep,
)
from gauntlet.http import HttpApi, SendResult


class _StubApi:
    """Stand-in for ``HttpApi`` that records calls and returns queued responses.

    The real ``HttpApi`` opens sockets; unit tests just need to know what the
    Drone sent and what it should read back.
    """

    def __init__(self, responses: list[HttpResponse]) -> None:
        self._queue = [SendResult(response=r) for r in responses]
        self.calls: list[tuple[str, HttpRequest]] = []

    def send(self, user: str, request: HttpRequest) -> SendResult:
        self.calls.append((user, request))
        return self._queue.pop(0)


def _drone(responses: list[HttpResponse]) -> tuple[Drone, _StubApi]:
    stub = _StubApi(responses)
    # mypy: Drone accepts an HttpApi but duck-typed send() is enough for tests.
    return Drone(stub), stub  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Task 1: extraction
# ---------------------------------------------------------------------------


def test_extract_captures_flat_key_into_template() -> None:
    """An ``extract`` mapping writes the captured value into the template context."""
    drone, stub = _drone(
        [
            HttpResponse(status_code=201, body={"id": "order-42"}),
            HttpResponse(status_code=200, body={}),
        ]
    )
    plan = Plan(
        name="orders_flow",
        category="crud",
        goal="verify extract generalizes beyond /tasks",
        steps=[
            PlanStep(
                user="userA",
                request=HttpRequest(method="POST", path="/orders", body={"item": "x"}),
                extract={"order_id": "id"},
            ),
            PlanStep(
                user="userA",
                request=HttpRequest(method="GET", path="/orders/{order_id}"),
            ),
        ],
    )

    drone.run_plan(plan)

    # The second request's path must have {order_id} substituted from step 1.
    assert stub.calls[1][1].path == "/orders/order-42"


def test_extract_supports_dotted_paths() -> None:
    """Dotted paths like ``data.id`` descend into nested dicts."""
    drone, stub = _drone(
        [
            HttpResponse(status_code=201, body={"data": {"id": "abc"}}),
            HttpResponse(status_code=200, body={}),
        ]
    )
    plan = Plan(
        name="nested",
        category="crud",
        goal="dotted-path extraction",
        steps=[
            PlanStep(
                user="userA",
                request=HttpRequest(method="POST", path="/widgets"),
                extract={"widget_id": "data.id"},
            ),
            PlanStep(
                user="userA",
                request=HttpRequest(method="GET", path="/widgets/{widget_id}"),
            ),
        ],
    )

    drone.run_plan(plan)

    assert stub.calls[1][1].path == "/widgets/abc"


def test_extract_missing_key_silently_skipped() -> None:
    """A missing dotted segment leaves the context untouched rather than raising."""
    drone, _ = _drone(
        [
            HttpResponse(status_code=201, body={"id": "x"}),
        ]
    )
    plan = Plan(
        name="missing_key",
        category="crud",
        goal="missing extract paths are tolerated",
        steps=[
            PlanStep(
                user="userA",
                request=HttpRequest(method="POST", path="/orders"),
                extract={"order_id": "data.id"},  # path doesn't exist in body
            ),
        ],
    )

    # Should not raise despite the missing path.
    drone.run_plan(plan)


def test_legacy_tasks_shortcut_still_populates_task_id() -> None:
    """Existing plans that rely on the hardcoded /tasks → task_id path keep working."""
    drone, stub = _drone(
        [
            HttpResponse(status_code=201, body={"id": "t-99"}),
            HttpResponse(status_code=200, body={}),
        ]
    )
    plan = Plan(
        name="legacy_tasks",
        category="crud",
        goal="regression: POST /tasks still populates {task_id}",
        steps=[
            PlanStep(
                user="userA",
                request=HttpRequest(method="POST", path="/tasks", body={"title": "t"}),
            ),
            PlanStep(
                user="userA",
                request=HttpRequest(method="GET", path="/tasks/{task_id}"),
            ),
        ],
    )

    drone.run_plan(plan)

    assert stub.calls[1][1].path == "/tasks/t-99"


def test_explicit_extract_supersedes_legacy_shortcut() -> None:
    """When a step declares ``extract``, the legacy /tasks carve-out does not run."""
    drone, stub = _drone(
        [
            HttpResponse(status_code=201, body={"id": "t-1", "slug": "alpha"}),
            HttpResponse(status_code=200, body={}),
        ]
    )
    plan = Plan(
        name="explicit_wins",
        category="crud",
        goal="explicit extract replaces the legacy shortcut",
        steps=[
            PlanStep(
                user="userA",
                request=HttpRequest(method="POST", path="/tasks"),
                extract={"slug": "slug"},
            ),
            PlanStep(
                user="userA",
                request=HttpRequest(method="GET", path="/tasks/{slug}"),
            ),
        ],
    )

    drone.run_plan(plan)

    assert stub.calls[1][1].path == "/tasks/alpha"


# ---------------------------------------------------------------------------
# Task 2: metadata propagation from SendResult into ExecutionStepResult
# ---------------------------------------------------------------------------


def test_send_result_metadata_flows_into_step_result() -> None:
    """Drone copies duration, size, headers, and outcome onto each step."""
    stub = _StubApi([])
    stub._queue.append(
        SendResult(
            response=HttpResponse(status_code=200, body={"ok": True}),
            duration_ms=12.5,
            response_size_bytes=17,
            response_headers={"Server": "nginx", "X-Request-Id": "abc"},
            outcome="ok",
        )
    )
    drone = Drone(stub)  # type: ignore[arg-type]
    plan = Plan(
        name="metadata",
        category="crud",
        goal="verify metadata flow",
        steps=[PlanStep(user="userA", request=HttpRequest(method="GET", path="/ping"))],
    )

    result = drone.run_plan(plan)

    step = result.steps[0]
    assert step.duration_ms == 12.5
    assert step.response_size_bytes == 17
    assert step.response_headers == {"Server": "nginx", "X-Request-Id": "abc"}
    assert step.outcome == "ok"


def test_execution_step_result_defaults_preserve_existing_fixtures() -> None:
    """New fields default so hand-built ExecutionStepResults don't need rewriting."""
    from gauntlet import ExecutionStepResult

    step = ExecutionStepResult(
        step_index=1,
        user="userA",
        request=HttpRequest(method="GET", path="/x"),
        response=HttpResponse(status_code=200, body={}),
    )
    assert step.duration_ms == 0.0
    assert step.response_size_bytes == 0
    assert step.response_headers == {}
    assert step.outcome == "ok"


# ---------------------------------------------------------------------------
# Sanity: Drone's public wiring still accepts a real HttpApi type.
# ---------------------------------------------------------------------------


def test_drone_accepts_httpapi_instance() -> None:
    """Drone's constructor signature still takes an HttpApi (no behavior check)."""
    _ = Drone(HttpApi("http://unused"))
