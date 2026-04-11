from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field


class FluxGateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HttpRequest(FluxGateModel):
    method: Literal["GET", "POST", "PATCH"]
    path: str
    body: dict[str, Any] = Field(default_factory=dict)


class HttpResponse(FluxGateModel):
    status_code: int
    body: dict[str, Any] = Field(default_factory=dict)


class Assertion(FluxGateModel):
    kind: Literal["status_code", "guard"]
    expected: Any | None = None
    rule: str | None = None
    step_index: int
    name: str


class ScenarioStep(FluxGateModel):
    actor: str
    request: HttpRequest


class Scenario(FluxGateModel):
    name: str
    category: str
    goal: str
    steps: list[ScenarioStep]
    assertions: list[Assertion] = Field(default_factory=list)


class NaturalLanguageScenario(FluxGateModel):
    """A scenario described in plain English; interpreted at runtime by a
    ``NaturalLanguageEvaluator`` rather than pre-defined as structured steps.

    No glue code, no schema maintenance — the evaluator plans its own request
    sequence from ``description`` and judges the outcome against ``verdict``.
    """

    name: str
    description: str
    actors: list[str]
    verdict: str


class ExecutionStepResult(FluxGateModel):
    step_index: int
    actor: str
    request: HttpRequest
    response: HttpResponse


class AssertionResult(FluxGateModel):
    name: str
    kind: Literal["status_code", "guard", "verdict"]
    passed: bool
    detail: str


class ExecutionResult(FluxGateModel):
    scenario_name: str
    category: str
    goal: str
    steps: list[ExecutionStepResult]
    assertions: list[AssertionResult]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def satisfaction_score(self) -> float:
        """Fraction of assertions that passed, in [0.0, 1.0].

        An empty assertion list (e.g. NL probe scenarios) returns 1.0.
        Computed automatically from ``assertions``; never set manually.
        """
        if not self.assertions:
            return 1.0
        return round(sum(1 for a in self.assertions if a.passed) / len(self.assertions), 4)


class Finding(FluxGateModel):
    issue: str
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    next_targets: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class Guard(FluxGateModel):
    """Engineer-authored guard that drives the adversarial loop.

    ``description`` is given to the Operator to guide probe scenario generation.
    ``must_hold`` are given only to the HoldoutEvaluator — the Operator
    never receives them, preserving the train/test separation.
    """

    title: str
    description: str
    must_hold: list[str]
    target_endpoints: list[str] = Field(default_factory=list)


class GuardAssessment(FluxGateModel):
    """Result of a preflight quality check on an Guard.

    When ``proceed`` is ``False``, the runner returns early without executing
    any iterations. The ``issues`` list explains why the guard was rejected;
    ``suggestions`` offers actionable fixes.
    """

    quality_score: float = Field(ge=0.0, le=1.0)
    issues: list[str]
    suggestions: list[str]
    proceed: bool


class IterationSpec(FluxGateModel):
    index: int
    name: str
    goal: str
    operator_prompt: str
    adversary_prompt: str
    tier: int = 0
    guard: Guard | None = None


class IterationRecord(FluxGateModel):
    spec: IterationSpec
    scenarios: list[Scenario]
    execution_results: list[ExecutionResult]
    findings: list[Finding]


class MergeGate(FluxGateModel):
    """Merge decision derived from holdout satisfaction score."""

    passed: bool
    holdout_satisfaction_score: float
    threshold: float
    recommendation: Literal["merge", "block", "review"]
    rationale: str


class RiskReport(FluxGateModel):
    confidence_score: float = Field(ge=0.0, le=1.0)
    risk_level: Literal["low", "medium", "high", "critical"]
    summary: list[str]
    confirmed_failures: list[str]
    suspicious_patterns: list[str]
    unexplored_surfaces: list[str]
    coverage: list[str]
    conclusion: str
    merge_gate: MergeGate | None = None


class FluxGateRun(FluxGateModel):
    guard: Guard | None = None
    iterations: list[IterationRecord]
    holdout_results: list[ExecutionResult] = Field(default_factory=list)
    guard_assessment: GuardAssessment | None = None
    risk_report: RiskReport
