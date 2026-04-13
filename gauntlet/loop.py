from __future__ import annotations

from typing import Literal

from .executor import Drone
from .models import (
    Clearance,
    ExecutionResult,
    Finding,
    GauntletRun,
    IterationRecord,
    IterationSpec,
    RiskReport,
    Target,
    Weapon,
    WeaponAssessment,
)
from .roles import (
    Attacker,
    HoldoutVitals,
    Inspector,
    NaturalLanguageHoldoutVitals,
    NaturalLanguageVitals,
    WeaponAssessor,
)
from .store import PlanStore


def build_default_iteration_specs() -> list[IterationSpec]:
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


class GauntletRunner:
    def __init__(
        self,
        executor: Drone,
        attacker: Attacker,
        inspector: Inspector,
        holdout_vitals: HoldoutVitals | None = None,
        nl_holdout_vitals: NaturalLanguageHoldoutVitals | None = None,
        nl_vitals: NaturalLanguageVitals | None = None,
        assessor: WeaponAssessor | None = None,
        weapon: Weapon | None = None,
        target: Target | None = None,
        clearance_threshold: float = 0.90,
        fail_fast_tier: int | None = None,
        plan_store: PlanStore | None = None,
    ) -> None:
        self._drone = executor
        self._attacker = attacker
        self._inspector = inspector
        self._holdout_vitals = holdout_vitals
        self._nl_holdout_vitals = nl_holdout_vitals
        self._nl_vitals = nl_vitals
        self._assessor = assessor
        self._weapon = weapon
        self._target = target
        self._clearance_threshold = clearance_threshold
        self._fail_fast_tier = fail_fast_tier
        self._plan_store = plan_store

    def run(self, iterations: list[IterationSpec] | None = None) -> GauntletRun:
        specs = iterations or build_default_iteration_specs()

        # Preflight: assess weapon quality before running any iterations.
        weapon_assessment: WeaponAssessment | None = None
        if self._assessor is not None and self._weapon is not None:
            weapon_assessment = self._assessor.assess(self._weapon, self._target)
            if not weapon_assessment.proceed:
                return self._blocked_by_preflight(weapon_assessment)

        # Inject a WeaponBrief (no blockers) into each iteration spec so the
        # Attacker can read spec.weapon.description and spec.weapon.title.
        # The full Weapon (with blockers) is held back and only passed to the
        # holdout vitals below — this is the train/test boundary.
        if self._weapon:
            weapon_brief = self._weapon.brief()
            specs = [
                s.model_copy(update={"weapon": weapon_brief, "target": self._target}) for s in specs
            ]

        records: list[IterationRecord] = []
        for spec in specs:
            plans = self._attacker.generate_plans(spec, records)
            if self._plan_store is not None and self._weapon and self._weapon.id:
                plans = self._plan_store.deduplicate(plans, self._weapon.id)
            execution_results = [self._drone.run_plan(plan) for plan in plans]
            findings = self._inspector.analyze(spec, execution_results)
            records.append(
                IterationRecord(
                    spec=spec,
                    plans=plans,
                    execution_results=execution_results,
                    findings=findings,
                )
            )

            # Fail-fast: stop as soon as a critical finding appears in a tier
            # at or above the configured threshold tier.
            if self._fail_fast_tier is not None and spec.tier >= self._fail_fast_tier:
                if any(f.severity == "critical" for f in findings):
                    break

        # Holdout plans are executed after the probe loop and their results
        # are never fed back to the Attacker or Inspector.
        holdout_results: list[ExecutionResult] = []
        if self._weapon is not None:
            if self._holdout_vitals is not None:
                for plan in self._holdout_vitals.acceptance_plans(self._weapon):
                    holdout_results.append(self._drone.run_plan(plan))

            if self._nl_holdout_vitals is not None and self._nl_vitals is not None:
                nl_plans = self._nl_holdout_vitals.acceptance_plans(self._weapon)
                for nl_plan in nl_plans:
                    holdout_results.append(self._nl_vitals.evaluate(nl_plan, self._drone))

        risk_report, clearance = _build_risk_report(
            records, holdout_results, self._clearance_threshold
        )
        return GauntletRun(
            clearance=clearance,
            weapon=self._weapon,
            target=self._target,
            iterations=records,
            holdout_results=holdout_results,
            weapon_assessment=weapon_assessment,
            risk_report=risk_report,
        )

    def _blocked_by_preflight(self, assessment: WeaponAssessment) -> GauntletRun:
        rationale = (
            f"Weapon quality score {assessment.quality_score:.0%} is too low to proceed. "
            f"Issues: {'; '.join(assessment.issues) or 'none'}."
        )
        clearance = Clearance(
            passed=False,
            holdout_satisfaction_score=0.0,
            threshold=self._clearance_threshold,
            recommendation="block",
            rationale=rationale,
        )
        return GauntletRun(
            clearance=clearance,
            weapon=self._weapon,
            target=self._target,
            iterations=[],
            holdout_results=[],
            weapon_assessment=assessment,
            risk_report=RiskReport(
                confidence_score=0.0,
                risk_level="low",
                summary=["Run blocked by preflight weapon assessment."],
                confirmed_failures=[],
                suspicious_patterns=[],
                unexplored_surfaces=[],
                coverage=[],
                conclusion="Run blocked: weapon quality score below threshold.",
            ),
        )


def _build_risk_report(
    records: list[IterationRecord],
    holdout_results: list[ExecutionResult],
    clearance_threshold: float,
) -> tuple[RiskReport, Clearance | None]:
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
        {item.content for finding in all_findings for item in finding.evidence}
    )
    unexplored_surfaces = _derive_unexplored_surfaces(all_findings)
    confidence_score = _confidence_score(records, coverage)
    risk_level = _risk_level(all_findings)

    clearance = _build_clearance(holdout_results, clearance_threshold) if holdout_results else None

    report = RiskReport(
        confidence_score=confidence_score,
        risk_level=risk_level,
        summary=confirmed_failures or ["no confirmed failures detected"],
        confirmed_failures=confirmed_failures,
        suspicious_patterns=suspicious_patterns,
        unexplored_surfaces=unexplored_surfaces,
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

    # Plan diversity: distinct attack categories relative to iterations run
    all_plans = [plan for record in records for plan in record.plans]
    distinct_categories = len({plan.category for plan in all_plans}) if all_plans else 0
    plan_diversity = min(1.0, distinct_categories / len(records))

    # Surface exploration depth: endpoints hit vs endpoints targeted
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

    # Exploration completeness: next_targets identified by findings but not yet covered
    all_findings = [finding for record in records for finding in record.findings]
    next_targets = {surface for finding in all_findings for surface in finding.next_targets}
    if next_targets:
        uncovered = len(next_targets - set(coverage))
        exploration_completeness = 1.0 - (uncovered / len(next_targets))
    else:
        exploration_completeness = 1.0

    return round(plan_diversity * 0.35 + surface_depth * 0.35 + exploration_completeness * 0.30, 2)


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
