from __future__ import annotations

from typing import Literal

from .executor import DeterministicLocalExecutor
from .models import (
    ExecutionResult,
    Finding,
    FluxGateRun,
    Guard,
    GuardAssessment,
    IterationRecord,
    IterationSpec,
    MergeGate,
    RiskReport,
)
from .roles import (
    Adversary,
    GuardAssessor,
    HoldoutEvaluator,
    NaturalLanguageEvaluator,
    NaturalLanguageHoldoutEvaluator,
    Operator,
)


def build_default_iteration_specs() -> list[IterationSpec]:
    return [
        IterationSpec(
            index=1,
            name="broad_baseline",
            goal="broad_baseline",
            tier=0,
            operator_prompt="Generate diverse CRUD and lifecycle scenarios.",
            adversary_prompt="Identify anomalies and weak coverage.",
        ),
        IterationSpec(
            index=2,
            name="boundary_and_guards",
            goal="boundary_and_guards",
            tier=1,
            operator_prompt="Target edge cases, missing fields, and schema drift.",
            adversary_prompt="Escalate guard violations.",
        ),
        IterationSpec(
            index=3,
            name="adversarial_misuse",
            goal="adversarial_misuse",
            tier=2,
            operator_prompt="Simulate auth violations and invalid transitions.",
            adversary_prompt="Identify security and logic failures.",
        ),
        IterationSpec(
            index=4,
            name="targeted_followup",
            goal="targeted_followup",
            tier=3,
            operator_prompt="Focus only on suspicious areas.",
            adversary_prompt="Finalize the failure model.",
        ),
    ]


class FluxGateRunner:
    def __init__(
        self,
        executor: DeterministicLocalExecutor,
        operator: Operator,
        adversary: Adversary,
        holdout_evaluator: HoldoutEvaluator | None = None,
        nl_holdout_evaluator: NaturalLanguageHoldoutEvaluator | None = None,
        nl_evaluator: NaturalLanguageEvaluator | None = None,
        assessor: GuardAssessor | None = None,
        guard: Guard | None = None,
        gate_threshold: float = 0.90,
        fail_fast_tier: int | None = None,
    ) -> None:
        self._executor = executor
        self._operator = operator
        self._adversary = adversary
        self._holdout_evaluator = holdout_evaluator
        self._nl_holdout_evaluator = nl_holdout_evaluator
        self._nl_evaluator = nl_evaluator
        self._assessor = assessor
        self._guard = guard
        self._gate_threshold = gate_threshold
        self._fail_fast_tier = fail_fast_tier

    def run(self, iterations: list[IterationSpec] | None = None) -> FluxGateRun:
        specs = iterations or build_default_iteration_specs()

        # Preflight: assess guard quality before running any iterations.
        guard_assessment: GuardAssessment | None = None
        if self._assessor is not None and self._guard is not None:
            guard_assessment = self._assessor.assess(self._guard)
            if not guard_assessment.proceed:
                return self._blocked_by_preflight(guard_assessment)

        # Inject guard into each iteration so the Operator can read
        # spec.guard.description — but never must_hold, which
        # is only passed to the holdout evaluators below.
        if self._guard:
            specs = [s.model_copy(update={"guard": self._guard}) for s in specs]

        records: list[IterationRecord] = []
        for spec in specs:
            scenarios = self._operator.generate_scenarios(spec, records)
            execution_results = [self._executor.run_scenario(scenario) for scenario in scenarios]
            findings = self._adversary.analyze(spec, execution_results)
            records.append(
                IterationRecord(
                    spec=spec,
                    scenarios=scenarios,
                    execution_results=execution_results,
                    findings=findings,
                )
            )

            # Fail-fast: stop as soon as a critical finding appears in a tier
            # at or above the configured threshold tier.
            if self._fail_fast_tier is not None and spec.tier >= self._fail_fast_tier:
                if any(f.severity == "critical" for f in findings):
                    break

        # Holdout scenarios are executed after the probe loop and their results
        # are never fed back to the Operator or Adversary.
        holdout_results: list[ExecutionResult] = []
        if self._guard is not None:
            if self._holdout_evaluator is not None:
                for scenario in self._holdout_evaluator.acceptance_scenarios(self._guard):
                    holdout_results.append(self._executor.run_scenario(scenario))

            if self._nl_holdout_evaluator is not None and self._nl_evaluator is not None:
                nl_scenarios = self._nl_holdout_evaluator.acceptance_scenarios(self._guard)
                for nl_scenario in nl_scenarios:
                    holdout_results.append(self._nl_evaluator.evaluate(nl_scenario, self._executor))

        return FluxGateRun(
            guard=self._guard,
            iterations=records,
            holdout_results=holdout_results,
            guard_assessment=guard_assessment,
            risk_report=_build_risk_report(records, holdout_results, self._gate_threshold),
        )

    def _blocked_by_preflight(self, assessment: GuardAssessment) -> FluxGateRun:
        rationale = (
            f"Guard quality score {assessment.quality_score:.0%} is too low to proceed. "
            f"Issues: {'; '.join(assessment.issues) or 'none'}."
        )
        return FluxGateRun(
            guard=self._guard,
            iterations=[],
            holdout_results=[],
            guard_assessment=assessment,
            risk_report=RiskReport(
                confidence_score=0.0,
                risk_level="low",
                summary=["Run blocked by preflight guard assessment."],
                confirmed_failures=[],
                suspicious_patterns=[],
                unexplored_surfaces=[],
                coverage=[],
                conclusion="Run blocked: guard quality score below threshold.",
                merge_gate=MergeGate(
                    passed=False,
                    holdout_satisfaction_score=0.0,
                    threshold=self._gate_threshold,
                    recommendation="block",
                    rationale=rationale,
                ),
            ),
        )


def _build_risk_report(
    records: list[IterationRecord],
    holdout_results: list[ExecutionResult],
    gate_threshold: float,
) -> RiskReport:
    all_findings = [finding for record in records for finding in record.findings]
    coverage = sorted(
        {
            f"{step.request.method} {step.request.path}"
            for record in records
            for result in record.execution_results
            for step in result.steps
        }
    )
    confirmed_failures = sorted({finding.issue for finding in all_findings})
    suspicious_patterns = sorted(
        {evidence for finding in all_findings for evidence in finding.evidence}
    )
    unexplored_surfaces = _derive_unexplored_surfaces(all_findings)
    confidence_score = _confidence_score(all_findings)
    risk_level = _risk_level(all_findings)

    merge_gate = _build_merge_gate(holdout_results, gate_threshold) if holdout_results else None

    return RiskReport(
        confidence_score=confidence_score,
        risk_level=risk_level,
        summary=confirmed_failures or ["no confirmed failures detected"],
        confirmed_failures=confirmed_failures,
        suspicious_patterns=suspicious_patterns,
        unexplored_surfaces=unexplored_surfaces,
        coverage=coverage,
        conclusion=_conclusion(risk_level, confirmed_failures),
        merge_gate=merge_gate,
    )


def _build_merge_gate(holdout_results: list[ExecutionResult], threshold: float) -> MergeGate:
    satisfaction_score = sum(r.satisfaction_score for r in holdout_results) / len(holdout_results)
    passed = satisfaction_score >= threshold

    if satisfaction_score >= threshold:
        recommendation: Literal["merge", "block", "review"] = "merge"
        rationale = (
            f"Holdout satisfaction score {satisfaction_score:.0%} meets threshold {threshold:.0%}."
        )
    elif satisfaction_score >= threshold * 0.8:
        recommendation = "review"
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

    return MergeGate(
        passed=passed,
        holdout_satisfaction_score=satisfaction_score,
        threshold=threshold,
        recommendation=recommendation,
        rationale=rationale,
    )


def _derive_unexplored_surfaces(findings: list[Finding]) -> list[str]:
    if not findings:
        return ["No high-risk unexplored surfaces identified."]
    return sorted({target for finding in findings for target in finding.next_targets})


def _confidence_score(findings: list[Finding]) -> float:
    if not findings:
        return 0.9
    average_finding_confidence = sum(finding.confidence for finding in findings) / len(findings)
    return round(max(0.0, 1.0 - average_finding_confidence), 2)


def _risk_level(findings: list[Finding]) -> Literal["low", "medium", "high", "critical"]:
    if any(finding.severity == "critical" for finding in findings):
        return "critical"
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
