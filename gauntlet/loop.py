from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any, Literal

from .models import (
    Clearance,
    ExecutionResult,
    ExecutionStepResult,
    FailureCluster,
    FinalClearance,
    Finding,
    IterationRecord,
    ResponseCollision,
    RiskReport,
    TimingAnomaly,
    WeaponReport,
)


def build_risk_report(
    records: list[IterationRecord],
    holdout_results: list[ExecutionResult],
    clearance_threshold: float,
) -> tuple[RiskReport, Clearance | None]:
    """Assemble a ``RiskReport`` and optional ``Clearance`` from iteration records.

    ``records`` is the full per-iteration log the host has accumulated.
    ``holdout_results`` are the execution results of the weapon's acceptance
    plans (the withheld vitals). When ``holdout_results`` is empty, the
    returned clearance is ``None`` — there's no gate to evaluate.
    """
    all_findings = [finding for record in records for finding in record.findings]
    blocker_findings = [f for f in all_findings if not f.is_anomaly]
    anomaly_findings = [f for f in all_findings if f.is_anomaly]
    coverage = sorted(
        {
            f"{step.request.method} {step.request.path}"
            for record in records
            for result in record.execution_results
            for step in result.steps
        }
    )
    confirmed_failures = sorted({finding.issue for finding in blocker_findings})
    suspicious_patterns = sorted(
        {item.content for finding in blocker_findings for item in finding.evidence}
    )
    anomalies = sorted({finding.issue for finding in anomaly_findings})
    unexplored_surfaces = _derive_unexplored_surfaces(all_findings)
    confidence_score = _confidence_score(records, coverage)
    risk_level = _risk_level(blocker_findings)

    clearance = _build_clearance(holdout_results, clearance_threshold) if holdout_results else None

    failure_clusters = _cluster_failures(blocker_findings)
    coverage_gaps = _coverage_gaps(records)
    response_collisions = _response_collisions(records)
    timing_anomalies = _timing_anomalies(records)

    report = RiskReport(
        confidence_score=confidence_score,
        risk_level=risk_level,
        summary=confirmed_failures or ["no confirmed failures detected"],
        confirmed_failures=confirmed_failures,
        suspicious_patterns=suspicious_patterns,
        unexplored_surfaces=unexplored_surfaces,
        anomalies=anomalies,
        coverage=coverage,
        conclusion=_conclusion(risk_level, confirmed_failures),
        failure_clusters=failure_clusters,
        coverage_gaps=coverage_gaps,
        response_collisions=response_collisions,
        timing_anomalies=timing_anomalies,
    )
    return report, clearance


def _build_clearance(holdout_results: list[ExecutionResult], threshold: float) -> Clearance:
    satisfaction_score = sum(r.satisfaction_score for r in holdout_results) / len(holdout_results)
    passed = satisfaction_score >= threshold

    if satisfaction_score >= threshold:
        recommendation: Literal["pass", "conditional", "block"] = "pass"
        rationale = (
            f"Holdout satisfaction score {satisfaction_score:.0%} meets threshold {threshold:.0%}."
        )
    elif satisfaction_score >= threshold * 0.8:
        recommendation = "conditional"
        rationale = (
            f"Holdout satisfaction score {satisfaction_score:.0%} is below threshold "
            f"{threshold:.0%} but within 20% — human review recommended."
        )
    else:
        recommendation = "block"
        rationale = (
            f"Holdout satisfaction score {satisfaction_score:.0%} "
            f"is below threshold {threshold:.0%}."
        )

    return Clearance(
        passed=passed,
        holdout_satisfaction_score=satisfaction_score,
        threshold=threshold,
        recommendation=recommendation,
        rationale=rationale,
    )


def _derive_unexplored_surfaces(findings: list[Finding]) -> list[str]:
    if not findings:
        return ["No high-risk unexplored surfaces identified."]
    return sorted({surface for finding in findings for surface in finding.next_targets})


def _confidence_score(records: list[IterationRecord], coverage: list[str]) -> float:
    """Coverage confidence: how thoroughly the attack surface was explored.

    Composed of three signals:
    - Plan diversity: distinct attack categories relative to iterations run.
    - Surface exploration depth: unique endpoints hit per iteration.
    - Exploration completeness: next_targets flagged by findings but not yet covered.
    """
    if not records:
        return 0.0

    all_plans = [plan for record in records for plan in record.plans]
    distinct_categories = len({plan.category for plan in all_plans}) if all_plans else 0
    plan_diversity = min(1.0, distinct_categories / len(records))

    surface_depth = min(1.0, len(coverage) / max(1, len(records) * 2))

    all_findings = [finding for record in records for finding in record.findings]
    next_targets = {surface for finding in all_findings for surface in finding.next_targets}
    if next_targets:
        uncovered = len(next_targets - set(coverage))
        exploration_completeness = 1.0 - (uncovered / len(next_targets))
    else:
        exploration_completeness = 1.0

    return round(plan_diversity * 0.35 + surface_depth * 0.35 + exploration_completeness * 0.30, 2)


def _risk_level(findings: list[Finding]) -> Literal["low", "medium", "high"]:
    if any(finding.severity == "high" for finding in findings):
        return "high"
    if any(finding.severity == "medium" for finding in findings):
        return "medium"
    return "low"


_RISK_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


def aggregate_final_clearance(
    per_weapon: list[WeaponReport], clearance_threshold: float
) -> FinalClearance:
    """Aggregate per-weapon reports into one overall pass/fail decision.

    Used by the ``assemble_final_clearance`` MCP tool. See
    :class:`FinalClearance` for the aggregation rules.
    """
    if not per_weapon:
        return FinalClearance(
            overall_confidence=0.0,
            max_risk_level="low",
            all_confirmed_failures=[],
            final_recommendation="block",
            rationale="No weapons were run; nothing can be cleared.",
            clearance_threshold=clearance_threshold,
            per_weapon_reports=[],
        )

    confidence_signals: list[float] = []
    for wr in per_weapon:
        confidence_signals.append(wr.risk_report.confidence_score)
        if wr.clearance is not None:
            confidence_signals.append(wr.clearance.holdout_satisfaction_score)
    overall_confidence = round(min(confidence_signals), 4)

    max_risk_rank = max(_RISK_RANK[wr.risk_report.risk_level] for wr in per_weapon)
    max_risk_level: Literal["low", "medium", "high"]
    if max_risk_rank == _RISK_RANK["high"]:
        max_risk_level = "high"
    elif max_risk_rank == _RISK_RANK["medium"]:
        max_risk_level = "medium"
    else:
        max_risk_level = "low"

    all_confirmed_failures = sorted(
        {failure for wr in per_weapon for failure in wr.risk_report.confirmed_failures}
    )

    has_high = max_risk_rank == _RISK_RANK["high"]
    has_medium = any(wr.risk_report.risk_level == "medium" for wr in per_weapon)
    threshold_met = overall_confidence >= clearance_threshold

    final_recommendation: Literal["pass", "conditional", "block"]
    if threshold_met and not has_high and not has_medium:
        final_recommendation = "pass"
        rationale = (
            f"Overall confidence {overall_confidence:.0%} meets threshold "
            f"{clearance_threshold:.0%} and no medium- or high-risk findings."
        )
    elif threshold_met and not has_high:
        final_recommendation = "conditional"
        rationale = (
            f"Overall confidence {overall_confidence:.0%} meets threshold "
            f"{clearance_threshold:.0%} but at least one weapon surfaced "
            f"medium-severity findings — human review recommended."
        )
    else:
        final_recommendation = "block"
        if has_high:
            rationale = "At least one weapon surfaced high-severity findings; promotion is blocked."
        else:
            rationale = (
                f"Overall confidence {overall_confidence:.0%} is below threshold "
                f"{clearance_threshold:.0%}."
            )

    return FinalClearance(
        overall_confidence=overall_confidence,
        max_risk_level=max_risk_level,
        all_confirmed_failures=all_confirmed_failures,
        final_recommendation=final_recommendation,
        rationale=rationale,
        clearance_threshold=clearance_threshold,
        per_weapon_reports=per_weapon,
    )


def _conclusion(risk_level: str, confirmed_failures: list[str]) -> str:
    if confirmed_failures:
        return (
            "System fails under adversarial pressure and should not be promoted "
            "without remediation."
        )
    return f"System survived the current adversarial loop with {risk_level} risk."


# ---------------------------------------------------------------------------
# Risk-report deterministic intelligence
# ---------------------------------------------------------------------------


def _cluster_failures(blocker_findings: list[Finding]) -> list[FailureCluster]:
    """Group blocker findings by ``(endpoint, method, severity)``.

    The endpoint/method pair is derived from the finding's first ``trace``
    step. If the finding has no traces we fall back to ``"unknown"`` so the
    cluster key is still stable. The representative finding is the one with
    the highest ``confidence`` in the group; ties break on first-seen order.
    """
    clusters: dict[tuple[str, str, str], list[Finding]] = defaultdict(list)
    for finding in blocker_findings:
        endpoint: str
        method: str
        if finding.traces:
            first = finding.traces[0]
            endpoint = first.request.path
            method = first.request.method
        else:
            endpoint = "unknown"
            method = "unknown"
        clusters[(endpoint, method, finding.severity)].append(finding)

    out: list[FailureCluster] = []
    for (endpoint, method, severity), group in clusters.items():
        representative = max(group, key=lambda f: f.confidence)
        out.append(
            FailureCluster(
                endpoint=endpoint,
                method=method,
                severity=severity,  # type: ignore[arg-type]
                size=len(group),
                representative_issue=representative.issue,
            )
        )
    # Deterministic ordering: highest severity first, then largest cluster,
    # then endpoint alphabetically for stability.
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda c: (severity_rank[c.severity], -c.size, c.endpoint, c.method))
    return out


_GAP_SEVERITY_ORDER: dict[str, int] = {
    "missing 5xx": 0,
    "missing 4xx": 1,
    "missing 3xx": 2,
    "missing 2xx": 3,
}


def _coverage_gaps(records: list[IterationRecord]) -> list[str]:
    """Report status-code buckets that were never observed.

    Buckets are 2xx/3xx/4xx/5xx. If no steps ran, returns ``[]``. Order is
    severity-first: 5xx > 4xx > 3xx > 2xx, matching how a host would
    prioritize plugging gaps.
    """
    observed_buckets: set[str] = set()
    for record in records:
        for result in record.execution_results:
            for step in result.steps:
                bucket = _status_bucket(step.response.status_code)
                if bucket is not None:
                    observed_buckets.add(bucket)

    if not observed_buckets:
        return []

    all_buckets = {"2xx", "3xx", "4xx", "5xx"}
    missing = [f"missing {b}" for b in all_buckets - observed_buckets]
    missing.sort(key=lambda m: _GAP_SEVERITY_ORDER[m])
    return missing


def _status_bucket(status_code: int) -> str | None:
    if 200 <= status_code < 300:
        return "2xx"
    if 300 <= status_code < 400:
        return "3xx"
    if 400 <= status_code < 500:
        return "4xx"
    if 500 <= status_code < 600:
        return "5xx"
    return None


def _response_collisions(records: list[IterationRecord]) -> list[ResponseCollision]:
    """Find response fingerprints shared by >= 3 steps across >= 2 plans.

    Fingerprint: ``(status, body_shape, size_bucket)`` — see
    ``_body_schema_shape`` and ``_response_size_bucket`` for bucket shapes.
    """
    per_fingerprint_steps: dict[str, int] = defaultdict(int)
    per_fingerprint_plans: dict[str, set[str]] = defaultdict(set)

    for record in records:
        for result in record.execution_results:
            for step in result.steps:
                fingerprint = _response_fingerprint(step)
                per_fingerprint_steps[fingerprint] += 1
                per_fingerprint_plans[fingerprint].add(result.plan_name)

    out: list[ResponseCollision] = []
    for fingerprint, count in per_fingerprint_steps.items():
        distinct_plans = len(per_fingerprint_plans[fingerprint])
        if count >= 3 and distinct_plans >= 2:
            out.append(
                ResponseCollision(
                    fingerprint=fingerprint,
                    occurrences=count,
                    distinct_plans=distinct_plans,
                )
            )
    out.sort(key=lambda c: (-c.occurrences, -c.distinct_plans, c.fingerprint))
    return out


def _response_fingerprint(step: ExecutionStepResult) -> str:
    status = step.response.status_code
    shape = _body_schema_shape(step.response.body)
    size_bucket = _response_size_bucket(step.response.body)
    return f"status={status}|{shape}|size={size_bucket}"


def _body_schema_shape(body: Any) -> str:
    if not isinstance(body, dict):
        return "scalar"
    if not body:
        return "empty"
    keys = sorted(str(k) for k in body.keys())
    return "keys=" + ",".join(keys)


_SIZE_BUCKETS: list[tuple[int, str]] = [
    (0, "0"),
    (256, "1-256"),
    (1024, "257-1k"),
    (4096, "1k-4k"),
    (16384, "4k-16k"),
]


def _response_size_bucket(body: Any) -> str:
    # A stable, cheap estimate: len of the repr. We do not re-serialize to
    # JSON here because that would couple coarse bucketing to serializer
    # behavior, and the buckets are coarse enough that any consistent
    # measure is fine.
    try:
        size = len(repr(body)) if body else 0
    except Exception:
        size = 0
    for threshold, label in _SIZE_BUCKETS:
        if size <= threshold:
            return label
    return "16k+"


def _timing_anomalies(records: list[IterationRecord]) -> list[TimingAnomaly]:
    """Flag steps with duration >= 10x the median for their ``(method, path)``.

    Gated on the step carrying a ``duration_ms`` attribute; if the richer
    ``ExecutionStepResult`` hasn't landed yet, returns ``[]``. The median
    needs at least 3 samples before anomalies are reported.
    """
    by_endpoint: dict[tuple[str, str], list[float]] = defaultdict(list)
    all_steps: list[tuple[str, str, float]] = []

    for record in records:
        for result in record.execution_results:
            for step in result.steps:
                duration = getattr(step, "duration_ms", None)
                if duration is None:
                    continue
                try:
                    duration_f = float(duration)
                except (TypeError, ValueError):
                    continue
                method_str: str = step.request.method
                path_str: str = step.request.path
                key = (method_str, path_str)
                by_endpoint[key].append(duration_f)
                all_steps.append((method_str, path_str, duration_f))

    out: list[TimingAnomaly] = []
    medians: dict[tuple[str, str], float] = {}
    for key, samples in by_endpoint.items():
        if len(samples) >= 3:
            medians[key] = statistics.median(samples)

    for method, path, duration in all_steps:
        median = medians.get((method, path))
        if median is None or median <= 0:
            continue
        if duration >= 10 * median:
            out.append(
                TimingAnomaly(
                    method=method,
                    path=path,
                    duration_ms=duration,
                    endpoint_median_ms=median,
                )
            )
    out.sort(key=lambda a: (-a.duration_ms, a.method, a.path))
    return out
