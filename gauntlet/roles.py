from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol

from .models import (
    Assertion,
    AssertionResult,
    EvidenceItem,
    ExecutionResult,
    Finding,
    HttpRequest,
    IterationRecord,
    IterationSpec,
    NaturalLanguagePlan,
    Plan,
    PlanStep,
    ReplayBundle,
    ReplayStep,
    Target,
    Weapon,
    WeaponAssessment,
)

if TYPE_CHECKING:
    from .executor import Drone


class Attacker(Protocol):
    """Generates test plans for one iteration of the adversarial loop.

    Receives the current iteration's goal and all prior findings. The demo
    implementation always returns the same cross-user modification plan;
    a real implementation (see ``LLMAttacker``) calls an LLM.
    """

    def generate_plans(
        self, spec: IterationSpec, previous_iterations: list[IterationRecord]
    ) -> list[Plan]: ...


class Inspector(Protocol):
    """Analyzes execution results and returns security/logic findings.

    The demo implementation surfaces the authorization flaw whenever any
    assertion fails; a real implementation (see ``LLMInspector``) calls an LLM.
    """

    def analyze(
        self, spec: IterationSpec, execution_results: list[ExecutionResult]
    ) -> list[Finding]: ...


class HoldoutVitals(Protocol):
    """Converts a Weapon's Vitals (blockers) into executable acceptance plans.

    Vitals are externally observable truths about expected system behavior.
    The Attacker never receives these plans or their results, preserving the
    train/test separation.
    """

    def acceptance_plans(self, weapon: Weapon) -> list[Plan]: ...


class NaturalLanguageHoldoutVitals(Protocol):
    """Converts a Weapon's Vitals (blockers) into NaturalLanguagePlan objects.

    Each vital becomes a plan described in plain English. The Attacker never
    sees these vitals. A ``NaturalLanguageVitals`` interprets them at execution
    time without glue code.
    """

    def acceptance_plans(self, weapon: Weapon) -> list[NaturalLanguagePlan]: ...


class NaturalLanguageVitals(Protocol):
    """Interprets a NaturalLanguagePlan against a live system under test.

    A real implementation calls an LLM to plan requests from ``plan.description``
    and judge the outcome against ``plan.verdict``.  The demo implementation
    uses pattern-matching as a stand-in.
    """

    def evaluate(self, plan: NaturalLanguagePlan, executor: Drone) -> ExecutionResult: ...


class WeaponAssessor(Protocol):
    """Evaluates a Weapon for quality before the adversarial loop runs.

    Returns a ``WeaponAssessment`` with a quality score, issues, suggestions,
    and a ``proceed`` flag. When ``proceed`` is ``False``, the runner skips
    all iterations and returns a blocked clearance.
    """

    def assess(self, weapon: Weapon, target: Target | None) -> WeaponAssessment: ...


# Shared steps and assertions for the demo authorization plan.
_AUTHZ_STEPS = [
    PlanStep(
        user="userA",
        request=HttpRequest(method="POST", path="/tasks", body={"title": "private task"}),
    ),
    PlanStep(
        user="userB",
        request=HttpRequest(method="PATCH", path="/tasks/{task_id}", body={"completed": True}),
    ),
    PlanStep(
        user="userA",
        request=HttpRequest(method="GET", path="/tasks/{task_id}"),
    ),
]

_AUTHZ_ASSERTIONS = [
    Assertion(
        name="unauthorized_patch_blocked",
        kind="status_code",
        expected=403,
        step_index=2,
    ),
    Assertion(
        name="task_not_modified_by_other_user",
        kind="rule",
        rule="task_not_modified_by_other_user",
        step_index=3,
    ),
]


class DemoAttacker:
    def generate_plans(
        self, spec: IterationSpec, previous_iterations: list[IterationRecord]
    ) -> list[Plan]:
        plan = Plan(
            name="user_cannot_modify_other_users_task",
            category="authz",
            goal=spec.goal,
            steps=_AUTHZ_STEPS,
            assertions=_AUTHZ_ASSERTIONS,
        )

        if spec.index == 1:
            return [plan]

        return [plan.model_copy(update={"name": f"{plan.name}_{spec.index}"})]


class DemoInspector:
    def analyze(
        self, spec: IterationSpec, execution_results: list[ExecutionResult]
    ) -> list[Finding]:
        findings: list[Finding] = []
        for result in execution_results:
            failed_assertions = [a for a in result.assertions if not a.passed]
            if failed_assertions:
                evidence = [
                    EvidenceItem(kind="assertion", content=f"{a.name}: {a.detail}")
                    for a in failed_assertions
                ]
                reproduction_steps = [
                    f"Step {s.step_index} ({s.user}): {s.request.method} {s.request.path}"
                    + (f" body={s.request.body}" if s.request.body else "")
                    + f" → {s.response.status_code}"
                    for s in result.steps
                ]
                violated_blocker = failed_assertions[0].name if failed_assertions else None
                replay_bundle = ReplayBundle(
                    steps=[ReplayStep(user=s.user, request=s.request) for s in result.steps]
                )
                findings.append(
                    Finding(
                        issue="unauthorized_cross_user_modification",
                        severity="critical",
                        confidence=0.94,
                        rationale=(
                            "A non-owner mutated another user's task during deterministic local "
                            f"execution in iteration {spec.index}."
                        ),
                        next_targets=[
                            "ownership mutation",
                            "list endpoint visibility",
                            "partial update guards",
                        ],
                        evidence=evidence,
                        reproduction_steps=reproduction_steps,
                        traces=result.steps,
                        violated_blocker=violated_blocker,
                        replay_bundle=replay_bundle,
                    )
                )

            # Surface anomalies: unexpected behaviors that don't violate a blocker
            # but may signal issues worth investigating.
            anomalies = _detect_anomalies(result)
            for anomaly_evidence, anomaly_description in anomalies:
                replay_bundle = ReplayBundle(
                    steps=[ReplayStep(user=s.user, request=s.request) for s in result.steps]
                )
                findings.append(
                    Finding(
                        issue=anomaly_description,
                        severity="low",
                        confidence=0.5,
                        rationale=(
                            "Unexpected behavior observed during iteration "
                            f"{spec.index} that does not map to a known blocker."
                        ),
                        evidence=[anomaly_evidence],
                        reproduction_steps=[
                            f"Step {s.step_index} ({s.user}): "
                            f"{s.request.method} {s.request.path}"
                            + (f" body={s.request.body}" if s.request.body else "")
                            + f" → {s.response.status_code}"
                            for s in result.steps
                        ],
                        traces=result.steps,
                        replay_bundle=replay_bundle,
                        is_anomaly=True,
                    )
                )
        return findings


def _detect_anomalies(
    result: ExecutionResult,
) -> list[tuple[EvidenceItem, str]]:
    """Return (evidence, description) pairs for unexpected behaviors in a result.

    Heuristics checked:
    - A mutating request (POST/PATCH) that succeeds with 200 when the step
      involves a different user than the resource creator.
    - A response body containing fields not present in the request body
      (potential data leakage or over-exposure).
    """
    anomalies: list[tuple[EvidenceItem, str]] = []

    for step in result.steps:
        # Heuristic: mutating request returned 200 where 4xx might be expected
        if (
            step.request.method in ("PATCH", "POST")
            and step.response.status_code == 200
            and step.request.path != "/tasks"
        ):
            anomalies.append(
                (
                    EvidenceItem(
                        kind="note",
                        content=(
                            f"Step {step.step_index}: {step.request.method} "
                            f"{step.request.path} returned 200 — expected 4xx "
                            f"for a mutating request on an existing resource."
                        ),
                    ),
                    "unexpected_success_on_mutation",
                )
            )

        # Heuristic: response body has fields not in the request body
        if step.request.body and step.response.body:
            extra_fields = set(step.response.body.keys()) - set(step.request.body.keys())
            if extra_fields:
                anomalies.append(
                    (
                        EvidenceItem(
                            kind="note",
                            content=(
                                f"Step {step.step_index}: response contains fields "
                                f"not in request: {sorted(extra_fields)}"
                            ),
                        ),
                        "unexpected_response_fields",
                    )
                )

    return anomalies


class DemoHoldoutVitals:
    """Returns the cross-user authorization plan as a structured holdout check."""

    def acceptance_plans(self, weapon: Weapon) -> list[Plan]:
        return [
            Plan(
                name="holdout_user_cannot_modify_other_users_task",
                category="authz",
                goal="verify ownership enforcement per blockers",
                steps=_AUTHZ_STEPS,
                assertions=_AUTHZ_ASSERTIONS,
            )
        ]


class DemoNaturalLanguageHoldoutVitals:
    """Converts each blocker into a NaturalLanguagePlan.

    A real implementation would parse the property with an LLM.  The demo
    passes the property text verbatim as the ``verdict``.
    """

    def acceptance_plans(self, weapon: Weapon) -> list[NaturalLanguagePlan]:
        return [
            NaturalLanguagePlan(
                name=f"criterion_{i}",
                description=weapon.description,
                users=["userA", "userB"],
                verdict=criterion,
            )
            for i, criterion in enumerate(weapon.blockers)
        ]


class DemoNaturalLanguageVitals:
    """Interprets NaturalLanguagePlans via pattern-matching (no LLM required).

    For each plan, it executes the hardcoded authorization test steps and
    judges the outcome by searching for expected status codes in the verdict text.
    A real implementation would use an LLM to plan steps from ``plan.description``
    and reason about the response against ``plan.verdict``.
    """

    def evaluate(self, plan: NaturalLanguagePlan, executor: Drone) -> ExecutionResult:
        probe = Plan(
            name=f"nl_{plan.name}",
            category="nl_evaluation",
            goal=plan.verdict,
            steps=_AUTHZ_STEPS,
            assertions=[],
        )
        result = executor.run_plan(probe)

        # Verdict judgment: extract expected status code from verdict text.
        step2_status = result.steps[1].response.status_code
        if "403" in plan.verdict:
            passed = step2_status == 403
            detail = f"verdict requires 403; step 2 returned {step2_status}"
        else:
            passed = all(s.response.status_code < 500 for s in result.steps)
            detail = "no server errors — verdict satisfied by default"

        verdict_result = AssertionResult(
            name="nl_verdict",
            kind="verdict",
            passed=passed,
            detail=detail,
        )

        return ExecutionResult(
            plan_name=plan.name,
            category="nl_evaluation",
            goal=plan.verdict,
            steps=result.steps,
            assertions=[verdict_result],
        )


class DemoWeaponAssessor:
    """Heuristic weapon assessor for use without an LLM.

    Scores a Weapon based on:
    - blocker length  (short blockers score low)
    - presence of specific HTTP status codes in blockers  (score high)
    - presence of target_endpoints  (score high)

    A ``quality_score`` below 0.5 sets ``proceed=False``, blocking the run.
    """

    _MIN_CRITERION_LEN = 20
    _STATUS_CODE_RE = re.compile(r"\b[1-5]\d{2}\b")

    def assess(self, weapon: Weapon, target: Target | None) -> WeaponAssessment:
        issues: list[str] = []
        suggestions: list[str] = []
        score = 1.0

        for criterion in weapon.blockers:
            if len(criterion.strip()) < self._MIN_CRITERION_LEN:
                issues.append(
                    f"Blocker too vague (< {self._MIN_CRITERION_LEN} chars): {criterion!r}"
                )
                suggestions.append(
                    "Specify expected status codes, fields, or observable behaviour."
                )
                score -= 0.3

        if target is None or not target.endpoints:
            issues.append("No target endpoints specified.")
            suggestions.append("List the endpoints the weapon covers (e.g. 'PATCH /tasks/{id}').")
            score -= 0.2

        has_status_code = any(self._STATUS_CODE_RE.search(c) for c in weapon.blockers)
        if not has_status_code:
            suggestions.append("Consider adding expected HTTP status codes to blockers.")
            score -= 0.1

        quality_score = round(max(0.0, score), 4)
        return WeaponAssessment(
            quality_score=quality_score,
            issues=issues,
            suggestions=suggestions,
            proceed=quality_score >= 0.5,
        )
