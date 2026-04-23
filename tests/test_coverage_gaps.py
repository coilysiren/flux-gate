"""Targeted coverage for edge-case branches that existing tests don't hit.

The other test files cover the happy paths and the representative failure
modes. This module sits underneath them: each test locks in one specific
branch — an exception classifier, a fallback bucket, an invalid matcher —
that otherwise rots silently as internals move.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
import yaml

from gauntlet import (
    Assertion,
    ExecutionResult,
    ExecutionStepResult,
    HttpRequest,
    HttpResponse,
    IterationRecord,
    IterationSpec,
    Plan,
    PlanStep,
    Trial,
    build_risk_report,
)
from gauntlet._log import _JsonFormatter, configure_logging, log_tool_call
from gauntlet.executor import Drone, _evaluate_assertion
from gauntlet.http import SendResult, _classify_connection_error
from gauntlet.loop import (
    _body_schema_shape,
    _response_size_bucket,
    _status_bucket,
    _timing_anomalies,
)
from gauntlet.models import AssertionResult
from gauntlet.server import _load_trials

# ---------------------------------------------------------------------------
# _log.py — non-JSON-serializable extras and exception payloads
# ---------------------------------------------------------------------------


class _Unserializable:
    def __repr__(self) -> str:
        return "<Unserializable sentinel>"


def test_json_formatter_reprs_unserializable_extras() -> None:
    formatter = _JsonFormatter()
    record = logging.LogRecord(
        name="gauntlet.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="x",
        args=None,
        exc_info=None,
    )
    record.weird = _Unserializable()
    payload = json.loads(formatter.format(record))
    assert payload["weird"] == "<Unserializable sentinel>"


def test_json_formatter_captures_exc_info() -> None:
    formatter = _JsonFormatter()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            name="gauntlet.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=0,
            msg="fail",
            args=None,
            exc_info=sys.exc_info(),
        )
    payload = json.loads(formatter.format(record))
    assert payload["exc_type"] == "RuntimeError"
    assert payload["exc_msg"] == "boom"


def test_log_tool_call_records_exception_and_reraises() -> None:
    """Verify that an exception inside the context manager is logged + re-raised."""
    configure_logging()

    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    # ``gauntlet`` has propagate=False so caplog's root handler misses records;
    # attach our own handler directly to the emitter.
    logger = logging.getLogger("gauntlet.tool")
    handler = _Capture(level=logging.ERROR)
    logger.addHandler(handler)
    try:
        with pytest.raises(ValueError):
            with log_tool_call("failing_tool", run_id="r1"):
                raise ValueError("nope")
    finally:
        logger.removeHandler(handler)

    assert any(
        getattr(record, "tool", None) == "failing_tool"
        and getattr(record, "status", None) == "error"
        and getattr(record, "exc_type", None) == "ValueError"
        for record in captured
    )


# ---------------------------------------------------------------------------
# loop.py — bucket fallbacks and timing-anomaly robustness
# ---------------------------------------------------------------------------


def test_status_bucket_returns_none_for_out_of_range() -> None:
    assert _status_bucket(199) is None
    assert _status_bucket(600) is None
    assert _status_bucket(0) is None


def test_response_size_bucket_handles_repr_failure() -> None:
    class _BadRepr:
        def __repr__(self) -> str:
            raise RuntimeError("repr fails")

    # Non-empty truthy object so the ``if body`` guard enters the repr path.
    assert _response_size_bucket(_BadRepr()) == "0"


def test_response_size_bucket_large_body_falls_through_to_16k() -> None:
    huge = "x" * 20_000
    assert _response_size_bucket(huge) == "16k+"


def test_body_schema_shape_variants() -> None:
    assert _body_schema_shape({}) == "empty"
    assert _body_schema_shape("hello") == "scalar"
    assert _body_schema_shape({"b": 1, "a": 2}) == "keys=a,b"


def _iteration_with_step(step: ExecutionStepResult) -> IterationRecord:
    result = ExecutionResult(
        plan_name="p",
        category="c",
        goal="g",
        steps=[step],
        assertions=[],
    )
    return IterationRecord(
        spec=IterationSpec(index=1, name="n", goal="g"),
        plans=[],
        execution_results=[result],
        findings=[],
    )


def test_timing_anomalies_skips_steps_missing_duration() -> None:
    step = ExecutionStepResult(
        step_index=1,
        user="u",
        request=HttpRequest(method="GET", path="/x"),
        response=HttpResponse(status_code=200),
        # duration_ms omitted — defaults to None on the enriched schema
    )
    assert _timing_anomalies([_iteration_with_step(step)]) == []


def test_timing_anomalies_skips_unconvertible_duration() -> None:
    step = ExecutionStepResult(
        step_index=1,
        user="u",
        request=HttpRequest(method="GET", path="/x"),
        response=HttpResponse(status_code=200),
    )
    # Bypass pydantic validation to inject a junk duration.
    object.__setattr__(step, "duration_ms", "not-a-float")
    assert _timing_anomalies([_iteration_with_step(step)]) == []


# ---------------------------------------------------------------------------
# loop._build_clearance — pass and conditional recommendations
# ---------------------------------------------------------------------------


def _spec() -> IterationSpec:
    return IterationSpec(index=1, name="n", goal="g")


def _result_with_score(score: float) -> ExecutionResult:
    passed = score >= 1.0
    partial = 0.0 < score < 1.0
    assertions: list[AssertionResult] = [
        AssertionResult(name="a1", passed=passed or partial, detail="d"),
        AssertionResult(name="a2", passed=passed, detail="d"),
    ]
    step = ExecutionStepResult(
        step_index=1,
        user="u",
        request=HttpRequest(method="GET", path="/x"),
        response=HttpResponse(status_code=200),
    )
    return ExecutionResult(
        plan_name="p", category="c", goal="g", steps=[step], assertions=assertions
    )


def test_build_clearance_pass_recommendation() -> None:
    iteration = IterationRecord(
        spec=_spec(), plans=[], execution_results=[_result_with_score(1.0)], findings=[]
    )
    _, clearance = build_risk_report(
        [iteration], [_result_with_score(1.0)], clearance_threshold=0.9
    )
    assert clearance is not None
    assert clearance.recommendation == "pass"


def test_build_clearance_conditional_recommendation() -> None:
    iteration = IterationRecord(
        spec=_spec(), plans=[], execution_results=[_result_with_score(0.5)], findings=[]
    )
    _, clearance = build_risk_report(
        [iteration], [_result_with_score(0.5)], clearance_threshold=0.6
    )
    assert clearance is not None
    assert clearance.recommendation == "conditional"


# ---------------------------------------------------------------------------
# server._load_trials — single-file path (not a directory)
# ---------------------------------------------------------------------------


def test_load_trials_from_single_file(tmp_path: Path) -> None:
    path = tmp_path / "one.yaml"
    path.write_text(
        yaml.dump(
            {
                "id": "single_trial",
                "title": "Single",
                "description": "d",
                "blockers": ["b"],
            }
        )
    )
    trials = _load_trials(str(path))
    assert len(trials) == 1
    assert trials[0].id == "single_trial"


def test_load_trials_missing_path_returns_empty(tmp_path: Path) -> None:
    assert _load_trials(str(tmp_path / "does-not-exist")) == []


# ---------------------------------------------------------------------------
# executor — invalid-matcher branches return failing assertion, not raise
# ---------------------------------------------------------------------------


def _evaluate(expected: Any, status: int) -> AssertionResult:
    step = ExecutionStepResult(
        step_index=1,
        user="u",
        request=HttpRequest(method="GET", path="/x"),
        response=HttpResponse(status_code=status),
    )
    return _evaluate_assertion(
        Assertion(name="t", expected=expected, step_index=1),
        [step],
    )


def test_assertion_matcher_rejects_non_int_min() -> None:
    result = _evaluate({"min": "oops"}, 200)
    assert result.passed is False
    assert "'min' must be int" in result.detail


def test_assertion_matcher_rejects_non_int_max() -> None:
    result = _evaluate({"max": "oops"}, 200)
    assert result.passed is False
    assert "'max' must be int" in result.detail


def test_assertion_matcher_rejects_non_list_in() -> None:
    result = _evaluate({"in": "403"}, 403)
    assert result.passed is False
    assert "'in' value must be a list" in result.detail


def test_assertion_matcher_rejects_unknown_dict_shape() -> None:
    result = _evaluate({"xyz": 200}, 200)
    assert result.passed is False
    assert "unsupported shape" in result.detail


# ---------------------------------------------------------------------------
# http._classify_connection_error — DNS / reset / other branches
# ---------------------------------------------------------------------------


def test_classify_dns_failure_via_name_or_service() -> None:
    exc = OSError("[Errno -2] Name or service not known")
    assert _classify_connection_error(exc) == "dns_failure"


def test_classify_dns_failure_via_getaddrinfo() -> None:
    exc = OSError("getaddrinfo failed")
    assert _classify_connection_error(exc) == "dns_failure"


def test_classify_connection_reset() -> None:
    exc = ConnectionResetError("Connection reset by peer")
    assert _classify_connection_error(exc) == "connection_reset"


def test_classify_other_error_fallback() -> None:
    exc = OSError("some unknown failure")
    assert _classify_connection_error(exc) == "other_error"


# ---------------------------------------------------------------------------
# models — validator failure paths
# ---------------------------------------------------------------------------


def test_trial_id_rejects_non_snake_case() -> None:
    with pytest.raises(ValueError, match="snake_case"):
        Trial(id="NotSnake", title="t", description="d", blockers=["b"])


def test_execution_result_empty_assertions_is_perfect_score() -> None:
    result = ExecutionResult(
        plan_name="p",
        category="c",
        goal="g",
        steps=[],
        assertions=[],
    )
    assert result.satisfaction_score == 1.0


# ---------------------------------------------------------------------------
# Drone extract — dotted-path lookup + missing key silently skipped
# ---------------------------------------------------------------------------


class _FakeApi:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def send(self, user: str, request: HttpRequest) -> SendResult:
        self.calls.append((request.method, request.path))
        return SendResult(response=self._responses.pop(0))


def test_drone_extract_dotted_path() -> None:
    api = _FakeApi(
        [
            HttpResponse(status_code=201, body={"data": {"id": "abc"}}),
            HttpResponse(status_code=200, body={}),
        ]
    )
    plan = Plan(
        name="dotted",
        category="c",
        goal="g",
        steps=[
            PlanStep(
                user="u",
                request=HttpRequest(method="POST", path="/widgets"),
                extract={"widget_id": "data.id"},
            ),
            PlanStep(
                user="u",
                request=HttpRequest(method="GET", path="/widgets/{widget_id}"),
            ),
        ],
    )
    Drone(api).run_plan(plan)  # type: ignore[arg-type]
    assert ("GET", "/widgets/abc") in api.calls


def test_drone_extract_missing_key_is_silently_skipped() -> None:
    api = _FakeApi(
        [
            HttpResponse(status_code=201, body={"unrelated": True}),
            HttpResponse(status_code=200, body={}),
        ]
    )
    plan = Plan(
        name="missing",
        category="c",
        goal="g",
        steps=[
            PlanStep(
                user="u",
                request=HttpRequest(method="POST", path="/widgets"),
                extract={"widget_id": "id"},
            ),
            PlanStep(
                user="u",
                request=HttpRequest(method="GET", path="/widgets"),
            ),
        ],
    )
    # Missing extraction key should not raise; the second step just doesn't
    # template anything in.
    Drone(api).run_plan(plan)  # type: ignore[arg-type]
    assert ("GET", "/widgets") in api.calls
