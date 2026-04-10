from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol

from .models import (
    Assertion,
    AssertionResult,
    ExecutionResult,
    FeatureSpec,
    Finding,
    HttpRequest,
    IterationRecord,
    IterationSpec,
    NaturalLanguageScenario,
    Scenario,
    ScenarioStep,
    SpecAssessment,
)

if TYPE_CHECKING:
    from .executor import DeterministicLocalExecutor


class Operator(Protocol):
    def generate_scenarios(
        self, spec: IterationSpec, previous_iterations: list[IterationRecord]
    ) -> list[Scenario]: ...


class Adversary(Protocol):
    def analyze(
        self, spec: IterationSpec, execution_results: list[ExecutionResult]
    ) -> list[Finding]: ...


class HoldoutEvaluator(Protocol):
    """Returns structured acceptance scenarios from a FeatureSpec.

    The Operator never receives these scenarios or their results — this preserves
    the train/test separation described in the dark factory pattern.
    """

    def acceptance_scenarios(self, spec: FeatureSpec) -> list[Scenario]: ...


class NaturalLanguageHoldoutEvaluator(Protocol):
    """Converts FeatureSpec acceptance criteria into NaturalLanguageScenario objects.

    Each criterion becomes a scenario described in plain English.  The Operator
    never sees these criteria.  A ``NaturalLanguageEvaluator`` interprets them
    at execution time without glue code.
    """

    def acceptance_scenarios(self, spec: FeatureSpec) -> list[NaturalLanguageScenario]: ...


class NaturalLanguageEvaluator(Protocol):
    """Interprets a NaturalLanguageScenario against a live system under test.

    A real implementation calls an LLM to plan requests from ``scenario.description``
    and judge the outcome against ``scenario.verdict``.  The demo implementation
    uses pattern-matching as a stand-in.
    """

    def evaluate(
        self, scenario: NaturalLanguageScenario, executor: DeterministicLocalExecutor
    ) -> ExecutionResult: ...


class SpecAssessor(Protocol):
    """Evaluates a FeatureSpec for quality before the adversarial loop runs.

    Returns a ``SpecAssessment`` with a quality score, issues, suggestions,
    and a ``proceed`` flag. When ``proceed`` is ``False``, the runner skips
    all iterations and returns a blocked merge gate.
    """

    def assess(self, spec: FeatureSpec) -> SpecAssessment: ...


# Shared steps and assertions for the demo authorization scenario.
_AUTHZ_STEPS = [
    ScenarioStep(
        actor="userA",
        request=HttpRequest(method="POST", path="/tasks", body={"title": "private task"}),
    ),
    ScenarioStep(
        actor="userB",
        request=HttpRequest(method="PATCH", path="/tasks/{task_id}", body={"completed": True}),
    ),
    ScenarioStep(
        actor="userA",
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
        kind="invariant",
        rule="task_not_modified_by_other_user",
        step_index=3,
    ),
]


class DemoOperator:
    def generate_scenarios(
        self, spec: IterationSpec, previous_iterations: list[IterationRecord]
    ) -> list[Scenario]:
        scenario = Scenario(
            name="user_cannot_modify_other_users_task",
            category="authz",
            goal=spec.goal,
            steps=_AUTHZ_STEPS,
            assertions=_AUTHZ_ASSERTIONS,
        )

        if spec.index == 1:
            return [scenario]

        return [scenario.model_copy(update={"name": f"{scenario.name}_{spec.index}"})]


class DemoAdversary:
    def analyze(
        self, spec: IterationSpec, execution_results: list[ExecutionResult]
    ) -> list[Finding]:
        findings: list[Finding] = []
        for result in execution_results:
            failed_assertions = [a for a in result.assertions if not a.passed]
            if not failed_assertions:
                continue

            evidence = [f"{a.name}: {a.detail}" for a in failed_assertions]
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
                        "partial update invariants",
                    ],
                    evidence=evidence,
                )
            )
        return findings


class DemoHoldoutEvaluator:
    """Returns the cross-user authorization scenario as a structured holdout check."""

    def acceptance_scenarios(self, spec: FeatureSpec) -> list[Scenario]:
        return [
            Scenario(
                name="holdout_user_cannot_modify_other_users_task",
                category="authz",
                goal="verify ownership enforcement per acceptance criteria",
                steps=_AUTHZ_STEPS,
                assertions=_AUTHZ_ASSERTIONS,
            )
        ]


class DemoNaturalLanguageHoldoutEvaluator:
    """Converts each acceptance criterion into a NaturalLanguageScenario.

    A real implementation would parse the criterion with an LLM.  The demo
    passes the criterion text verbatim as the ``verdict``.
    """

    def acceptance_scenarios(self, spec: FeatureSpec) -> list[NaturalLanguageScenario]:
        return [
            NaturalLanguageScenario(
                name=f"criterion_{i}",
                description=spec.description,
                actors=["userA", "userB"],
                verdict=criterion,
            )
            for i, criterion in enumerate(spec.acceptance_criteria)
        ]


class DemoNaturalLanguageEvaluator:
    """Interprets NaturalLanguageScenarios via pattern-matching (no LLM required).

    For each scenario, it executes the hardcoded authorization test steps and
    judges the outcome by searching for expected status codes in the verdict text.
    A real implementation would use an LLM to plan steps from ``scenario.description``
    and reason about the response against ``scenario.verdict``.
    """

    def evaluate(
        self, scenario: NaturalLanguageScenario, executor: DeterministicLocalExecutor
    ) -> ExecutionResult:
        probe = Scenario(
            name=f"nl_{scenario.name}",
            category="nl_evaluation",
            goal=scenario.verdict,
            steps=_AUTHZ_STEPS,
            assertions=[],
        )
        result = executor.run_scenario(probe)

        # Verdict judgment: extract expected status code from verdict text.
        step2_status = result.steps[1].response.status_code
        if "403" in scenario.verdict:
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
            scenario_name=scenario.name,
            category="nl_evaluation",
            goal=scenario.verdict,
            steps=result.steps,
            assertions=[verdict_result],
        )


class DemoSpecAssessor:
    """Heuristic spec assessor for use without an LLM.

    Scores a FeatureSpec based on:
    - acceptance criteria length  (short criteria score low)
    - presence of specific HTTP status codes in criteria  (score high)
    - presence of target_endpoints  (score high)

    A ``quality_score`` below 0.5 sets ``proceed=False``, blocking the run.
    """

    _MIN_CRITERION_LEN = 20
    _STATUS_CODE_RE = re.compile(r"\b[1-5]\d{2}\b")

    def assess(self, spec: FeatureSpec) -> SpecAssessment:
        issues: list[str] = []
        suggestions: list[str] = []
        score = 1.0

        for criterion in spec.acceptance_criteria:
            if len(criterion.strip()) < self._MIN_CRITERION_LEN:
                issues.append(
                    f"Criterion too vague (< {self._MIN_CRITERION_LEN} chars): {criterion!r}"
                )
                suggestions.append(
                    "Specify expected status codes, fields, or observable behaviour."
                )
                score -= 0.3

        if not spec.target_endpoints:
            issues.append("No target_endpoints specified.")
            suggestions.append("List the endpoints the spec covers (e.g. 'PATCH /tasks/{id}').")
            score -= 0.2

        has_status_code = any(self._STATUS_CODE_RE.search(c) for c in spec.acceptance_criteria)
        if not has_status_code:
            suggestions.append("Consider adding expected HTTP status codes to acceptance criteria.")
            score -= 0.1

        quality_score = round(max(0.0, score), 4)
        return SpecAssessment(
            quality_score=quality_score,
            issues=issues,
            suggestions=suggestions,
            proceed=quality_score >= 0.5,
        )
