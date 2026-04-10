from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
    kind: Literal["status_code", "invariant"]
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


class ExecutionStepResult(FluxGateModel):
    step_index: int
    actor: str
    request: HttpRequest
    response: HttpResponse


class AssertionResult(FluxGateModel):
    name: str
    kind: Literal["status_code", "invariant"]
    passed: bool
    detail: str


class ExecutionResult(FluxGateModel):
    scenario_name: str
    category: str
    goal: str
    steps: list[ExecutionStepResult]
    assertions: list[AssertionResult]


class Finding(FluxGateModel):
    issue: str
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    next_targets: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class FeatureSpec(FluxGateModel):
    """Engineer-authored spec that drives the adversarial loop.

    ``description`` is given to the Operator to guide probe scenario generation.
    ``acceptance_criteria`` are given only to the HoldoutEvaluator — the Operator
    never receives them, preserving the train/test separation.
    """

    title: str
    description: str
    acceptance_criteria: list[str]
    target_endpoints: list[str] = Field(default_factory=list)


class IterationSpec(FluxGateModel):
    index: int
    name: str
    goal: str
    operator_prompt: str
    adversary_prompt: str
    feature_spec: FeatureSpec | None = None


class IterationRecord(FluxGateModel):
    spec: IterationSpec
    scenarios: list[Scenario]
    execution_results: list[ExecutionResult]
    findings: list[Finding]


class MergeGate(FluxGateModel):
    """Binary merge decision derived from holdout pass rate."""

    passed: bool
    holdout_pass_rate: float
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
    system_under_test: str
    environment: str
    feature_spec: FeatureSpec | None = None
    iterations: list[IterationRecord]
    holdout_results: list[ExecutionResult] = Field(default_factory=list)
    risk_report: RiskReport
