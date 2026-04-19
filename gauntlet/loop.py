from __future__ import annotations

from typing import Literal

from .models import (
    Clearance,
    ExecutionResult,
    Finding,
    IterationRecord,
    IterationSpec,
    RiskReport,
)


def build_default_iteration_specs() -> list[IterationSpec]:
    """Return the default 4-stage escalation ladder.

    baseline → boundary → adversarial_misuse → targeted_escalation. Exposed
    as a reference for hosts driving the adversarial loop; they may follow
    it verbatim or author their own spec list.
    """
    return [
        IterationSpec(
            index=1,
            name="baseline",
            goal="baseline",
            tier=0,
            attacker_prompt="Generate diverse CRUD and lifecycle plans.",
            inspector_prompt="Identify anomalies and weak coverage.",
        ),
        IterationSpec(
            index=2,
            name="boundary",
            goal="boundary",
            tier=1,
            attacker_prompt="Target edge cases, missing fields, and schema drift.",
            inspector_prompt="Escalate guard violations.",
        ),
        IterationSpec(
            index=3,
            name="adversarial_misuse",
            goal="adversarial_misuse",
            tier=2,
            attacker_prompt="Simulate auth violations and invalid transitions.",
            inspector_prompt="Identify security and logic failures.",
        ),
        IterationSpec(
            index=4,
            name="targeted_escalation",
            goal="targeted_escalation",
            tier=3,
            attacker_prompt="Focus only on suspicious areas.",
            inspector_prompt="Finalize the failure model.",
        ),
    ]


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
    - Surface exploration depth: unique endpoints hit vs endpoints targeted.
    - Exploration completeness: next_targets flagged by findings but not yet covered.
    """
    if not records:
        return 0.0

    all_plans = [plan for record in records for plan in record.plans]
    distinct_categories = len({plan.category for plan in all_plans}) if all_plans else 0
    plan_diversity = min(1.0, distinct_categories / len(records))

    targeted = [
        endpoint
        for record in records
        if record.spec.target
        for endpoint in record.spec.target.endpoints
    ]
    if targeted:
        surface_depth = min(1.0, len(coverage) / len(targeted))
    else:
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


def _conclusion(risk_level: str, confirmed_failures: list[str]) -> str:
    if confirmed_failures:
        return (
            "System fails under adversarial pressure and should not be promoted "
            "without remediation."
        )
    return f"System survived the current adversarial loop with {risk_level} risk."
