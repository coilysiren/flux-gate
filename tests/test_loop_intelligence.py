"""Tests for the deterministic risk-report analyzers in ``gauntlet.loop``."""

from __future__ import annotations

from typing import Any

from gauntlet import (
    ExecutionResult,
    ExecutionStepResult,
    Finding,
    HttpRequest,
    HttpResponse,
    IterationRecord,
    IterationSpec,
    build_risk_report,
)

from ._factories import make_execution_result


def _spec(index: int = 1, name: str = "baseline") -> IterationSpec:
    return IterationSpec(index=index, name=name, goal=name)


def _step(
    *,
    method: str = "POST",
    path: str = "/tasks",
    status: int = 200,
    body: dict[str, Any] | None = None,
    step_index: int = 1,
    user: str = "userA",
) -> ExecutionStepResult:
    return ExecutionStepResult(
        step_index=step_index,
        user=user,
        request=HttpRequest(method=method, path=path),  # type: ignore[arg-type]
        response=HttpResponse(status_code=status, body=body or {}),
    )


def _finding(
    *,
    issue: str,
    severity: str = "medium",
    confidence: float = 0.5,
    traces: list[ExecutionStepResult] | None = None,
) -> Finding:
    return Finding(
        issue=issue,
        severity=severity,  # type: ignore[arg-type]
        confidence=confidence,
        rationale="synthetic",
        traces=traces or [],
    )


def _record(
    *,
    findings: list[Finding] | None = None,
    execution_results: list[ExecutionResult] | None = None,
    index: int = 1,
) -> IterationRecord:
    return IterationRecord(
        spec=_spec(index=index),
        plans=[],
        execution_results=execution_results or [],
        findings=findings or [],
    )


# ---------------------------------------------------------------------------
# Failure clustering
# ---------------------------------------------------------------------------


def test_failure_clustering_groups_by_endpoint_method_severity() -> None:
    trace = _step(method="PATCH", path="/tasks/1")
    findings = [
        _finding(issue="Cross-user PATCH accepted A", confidence=0.7, traces=[trace]),
        _finding(issue="Cross-user PATCH accepted B", confidence=0.9, traces=[trace]),
        _finding(issue="Cross-user PATCH accepted C", confidence=0.4, traces=[trace]),
    ]
    report, _ = build_risk_report([_record(findings=findings)], [], clearance_threshold=0.9)

    assert len(report.failure_clusters) == 1
    cluster = report.failure_clusters[0]
    assert cluster.endpoint == "/tasks/1"
    assert cluster.method == "PATCH"
    assert cluster.severity == "medium"
    assert cluster.size == 3
    assert cluster.representative_issue == "Cross-user PATCH accepted B"


def test_failure_clustering_unknown_when_no_traces() -> None:
    findings = [_finding(issue="no-trace", confidence=0.1)]
    report, _ = build_risk_report([_record(findings=findings)], [], clearance_threshold=0.9)
    assert report.failure_clusters[0].endpoint == "unknown"
    assert report.failure_clusters[0].method == "unknown"


def test_failure_clustering_orders_high_severity_first() -> None:
    low_trace = _step(method="GET", path="/a")
    high_trace = _step(method="POST", path="/b")
    findings = [
        _finding(issue="low finding", severity="low", traces=[low_trace]),
        _finding(issue="high finding", severity="high", traces=[high_trace]),
    ]
    report, _ = build_risk_report([_record(findings=findings)], [], clearance_threshold=0.9)
    assert report.failure_clusters[0].severity == "high"


def test_failure_clustering_skips_anomalies() -> None:
    trace = _step()
    findings = [
        Finding(
            issue="just an anomaly",
            severity="low",
            confidence=0.5,
            rationale="x",
            traces=[trace],
            is_anomaly=True,
        )
    ]
    report, _ = build_risk_report([_record(findings=findings)], [], clearance_threshold=0.9)
    assert report.failure_clusters == []


# ---------------------------------------------------------------------------
# Coverage gap analysis
# ---------------------------------------------------------------------------


def test_coverage_gaps_only_2xx_reports_missing_345xx() -> None:
    exec_result = ExecutionResult(
        plan_name="p",
        category="c",
        goal="g",
        steps=[_step(status=200), _step(status=201, step_index=2)],
        assertions=[],
    )
    report, _ = build_risk_report(
        [_record(execution_results=[exec_result])], [], clearance_threshold=0.9
    )
    # Order is severity-first: 5xx > 4xx > 3xx
    assert report.coverage_gaps == ["missing 5xx", "missing 4xx", "missing 3xx"]


def test_coverage_gaps_full_coverage_is_empty() -> None:
    exec_result = ExecutionResult(
        plan_name="p",
        category="c",
        goal="g",
        steps=[
            _step(status=200, step_index=1),
            _step(status=301, step_index=2),
            _step(status=403, step_index=3),
            _step(status=500, step_index=4),
        ],
        assertions=[],
    )
    report, _ = build_risk_report(
        [_record(execution_results=[exec_result])], [], clearance_threshold=0.9
    )
    assert report.coverage_gaps == []


def test_coverage_gaps_empty_when_no_steps() -> None:
    report, _ = build_risk_report([_record()], [], clearance_threshold=0.9)
    assert report.coverage_gaps == []


def test_coverage_gaps_reports_only_missing_buckets() -> None:
    exec_result = ExecutionResult(
        plan_name="p",
        category="c",
        goal="g",
        steps=[_step(status=200, step_index=1), _step(status=403, step_index=2)],
        assertions=[],
    )
    report, _ = build_risk_report(
        [_record(execution_results=[exec_result])], [], clearance_threshold=0.9
    )
    assert report.coverage_gaps == ["missing 5xx", "missing 3xx"]


# ---------------------------------------------------------------------------
# Response-fingerprint collisions
# ---------------------------------------------------------------------------


def test_response_collisions_detected_across_distinct_plans() -> None:
    body = {"error": "x", "message": "y"}
    steps_a = [_step(status=403, body=body, step_index=i + 1) for i in range(2)]
    steps_b = [_step(status=403, body=body)]
    result_a = ExecutionResult(
        plan_name="plan_a", category="c", goal="g", steps=steps_a, assertions=[]
    )
    result_b = ExecutionResult(
        plan_name="plan_b", category="c", goal="g", steps=steps_b, assertions=[]
    )
    report, _ = build_risk_report(
        [_record(execution_results=[result_a, result_b])], [], clearance_threshold=0.9
    )
    assert len(report.response_collisions) == 1
    collision = report.response_collisions[0]
    assert collision.occurrences == 3
    assert collision.distinct_plans == 2
    assert "status=403" in collision.fingerprint
    assert "keys=error,message" in collision.fingerprint


def test_response_collisions_requires_distinct_plans() -> None:
    """Three occurrences from a single plan is not a collision."""
    body = {"x": 1}
    steps = [_step(status=200, body=body, step_index=i + 1) for i in range(3)]
    result = ExecutionResult(plan_name="lonely", category="c", goal="g", steps=steps, assertions=[])
    report, _ = build_risk_report(
        [_record(execution_results=[result])], [], clearance_threshold=0.9
    )
    assert report.response_collisions == []


def test_response_collisions_requires_three_occurrences() -> None:
    body = {"x": 1}
    result_a = ExecutionResult(
        plan_name="a",
        category="c",
        goal="g",
        steps=[_step(status=200, body=body)],
        assertions=[],
    )
    result_b = ExecutionResult(
        plan_name="b",
        category="c",
        goal="g",
        steps=[_step(status=200, body=body)],
        assertions=[],
    )
    report, _ = build_risk_report(
        [_record(execution_results=[result_a, result_b])], [], clearance_threshold=0.9
    )
    assert report.response_collisions == []


# ---------------------------------------------------------------------------
# Timing anomalies
# ---------------------------------------------------------------------------


def test_timing_anomalies_empty_when_duration_missing() -> None:
    # ExecutionStepResult in the current schema does not carry duration_ms.
    exec_result = make_execution_result()
    report, _ = build_risk_report(
        [_record(execution_results=[exec_result])], [], clearance_threshold=0.9
    )
    assert report.timing_anomalies == []


def test_timing_anomalies_flags_10x_median() -> None:
    """Simulate a richer ExecutionStepResult by attaching duration_ms via a
    subclass-free shim: monkeypatching the model is not worth it, so we
    build the records normally and then, after construction, attach the
    attribute on each step. ``getattr`` in the analyzer tolerates this."""
    normal_body: dict[str, Any] = {}
    steps: list[ExecutionStepResult] = []
    # three "normal" 10ms samples + one 200ms anomaly, all against the same endpoint
    for i, dur in enumerate([10.0, 11.0, 9.0, 200.0]):
        step = _step(
            method="GET",
            path="/slow",
            status=200,
            body=normal_body,
            step_index=i + 1,
        )
        object.__setattr__(step, "duration_ms", dur)
        steps.append(step)

    exec_result = ExecutionResult(plan_name="p", category="c", goal="g", steps=steps, assertions=[])
    report, _ = build_risk_report(
        [_record(execution_results=[exec_result])], [], clearance_threshold=0.9
    )
    assert len(report.timing_anomalies) == 1
    anomaly = report.timing_anomalies[0]
    assert anomaly.method == "GET"
    assert anomaly.path == "/slow"
    assert anomaly.duration_ms == 200.0
    # median of [9,10,11,200] is 10.5; 200 is >10x, so it's flagged.
    assert anomaly.endpoint_median_ms == 10.5


def test_timing_anomalies_requires_three_samples() -> None:
    steps: list[ExecutionStepResult] = []
    for i, dur in enumerate([10.0, 200.0]):
        step = _step(method="GET", path="/slow", status=200, step_index=i + 1)
        object.__setattr__(step, "duration_ms", dur)
        steps.append(step)
    exec_result = ExecutionResult(plan_name="p", category="c", goal="g", steps=steps, assertions=[])
    report, _ = build_risk_report(
        [_record(execution_results=[exec_result])], [], clearance_threshold=0.9
    )
    assert report.timing_anomalies == []


# ---------------------------------------------------------------------------
# Regression: existing fields stay populated
# ---------------------------------------------------------------------------


def test_existing_risk_report_fields_still_populated() -> None:
    """Additive change — the new lists default to [] and old fields survive."""
    exec_result = make_execution_result()
    iteration = IterationRecord(
        spec=_spec(),
        plans=[],
        execution_results=[exec_result],
        findings=[],
    )
    report, _ = build_risk_report([iteration], [], clearance_threshold=0.9)
    assert report.failure_clusters == []
    assert report.response_collisions == []
    assert report.timing_anomalies == []
    # coverage_gaps is populated because the single step is a 2xx
    assert report.coverage_gaps == ["missing 5xx", "missing 4xx", "missing 3xx"]
    # old fields still there
    assert report.risk_level == "low"
    assert report.coverage == ["POST /tasks"]
