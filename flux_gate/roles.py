from __future__ import annotations

from typing import Protocol

from .models import (
    Assertion,
    ExecutionResult,
    FeatureSpec,
    Finding,
    HttpRequest,
    IterationRecord,
    IterationSpec,
    Scenario,
    ScenarioStep,
)


class Operator(Protocol):
    def generate_scenarios(
        self, spec: IterationSpec, previous_iterations: list[IterationRecord]
    ) -> list[Scenario]: ...


class Adversary(Protocol):
    def analyze(
        self, spec: IterationSpec, execution_results: list[ExecutionResult]
    ) -> list[Finding]: ...


class HoldoutEvaluator(Protocol):
    """Produces acceptance scenarios from a FeatureSpec.

    The Operator never receives these scenarios or their results — this preserves
    the train/test separation described in the dark factory pattern.  A real
    implementation calls an LLM with ``spec.acceptance_criteria``; the demo
    implementation returns a fixed scenario regardless of spec content.
    """

    def acceptance_scenarios(self, spec: FeatureSpec) -> list[Scenario]: ...


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
    """Returns the cross-user authorization scenario as a holdout acceptance check.

    The Operator generates the same scenario as a probe — in the demo this is
    intentional, to show that the holdout layer independently catches the seeded
    authorization flaw.  A real evaluator would derive scenarios from
    ``spec.acceptance_criteria`` via an LLM.
    """

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
