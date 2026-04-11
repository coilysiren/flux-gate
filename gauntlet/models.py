from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field


class GauntletModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HttpRequest(GauntletModel):
    method: Literal["GET", "POST", "PATCH"]
    path: str
    body: dict[str, Any] = Field(default_factory=dict)


class HttpResponse(GauntletModel):
    status_code: int
    body: dict[str, Any] = Field(default_factory=dict)


class Assertion(GauntletModel):
    kind: Literal["status_code", "rule"]
    expected: Any | None = None
    rule: str | None = None
    step_index: int
    name: str


class PlanStep(GauntletModel):
    user: str
    request: HttpRequest


class Plan(GauntletModel):
    name: str
    category: str
    goal: str
    steps: list[PlanStep]
    assertions: list[Assertion] = Field(default_factory=list)


class NaturalLanguagePlan(GauntletModel):
    """A plan described in plain English; interpreted at runtime by a
    ``NaturalLanguageEvaluator`` rather than pre-defined as structured steps.

    No glue code, no schema maintenance — the evaluator plans its own request
    sequence from ``description`` and judges the outcome against ``verdict``.
    """

    name: str
    description: str
    users: list[str]
    verdict: str


class ExecutionStepResult(GauntletModel):
    step_index: int
    user: str
    request: HttpRequest
    response: HttpResponse


class AssertionResult(GauntletModel):
    name: str
    kind: Literal["status_code", "rule", "verdict"]
    passed: bool
    detail: str


class ExecutionResult(GauntletModel):
    plan_name: str
    category: str
    goal: str
    steps: list[ExecutionStepResult]
    assertions: list[AssertionResult]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def satisfaction_score(self) -> float:
        """Fraction of assertions that passed, in [0.0, 1.0].

        An empty assertion list (e.g. NL probe plans) returns 1.0.
        Computed automatically from ``assertions``; never set manually.
        """
        if not self.assertions:
            return 1.0
        return round(sum(1 for a in self.assertions if a.passed) / len(self.assertions), 4)


class Finding(GauntletModel):
    issue: str
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    next_targets: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class WeaponBrief(GauntletModel):
    """Attacker-visible slice of a Weapon.

    Contains only what the Attacker is allowed to see: the weapon id, title,
    and a plain-English description of the attack surface.  The acceptance
    criteria (``blockers``) are intentionally absent — they are withheld to
    preserve the train/test separation and prevent reward-hacking.
    """

    id: str | None = None
    title: str
    description: str


class Weapon(GauntletModel):
    """Engineer-authored weapon that drives the adversarial loop.

    ``id`` is a stable snake_case identifier (e.g.
    ``resource_ownership_write_isolation``) used to accumulate failure
    knowledge across runs.  ``title`` is the human-readable alias (e.g.
    "Users cannot modify each other's tasks").  Together they let the system
    correlate findings over time without schema churn.

    ``description`` (exposed via ``WeaponBrief``) is given to the Attacker to
    guide probe plan generation.  ``blockers`` are the Weapon's Vitals —
    externally observable truths about expected system behavior — given only
    to the HoldoutVitals.  The Attacker never receives them, preserving the
    train/test separation.

    Use ``Weapon.brief()`` to produce the attacker-safe view.
    """

    id: str | None = None
    title: str
    description: str
    blockers: list[str]

    def brief(self) -> WeaponBrief:
        """Return the attacker-safe view of this weapon (no blockers)."""
        return WeaponBrief(id=self.id, title=self.title, description=self.description)


class WeaponAssessment(GauntletModel):
    """Result of a preflight quality check on a Weapon.

    When ``proceed`` is ``False``, the runner returns early without executing
    any iterations. The ``issues`` list explains why the weapon was rejected;
    ``suggestions`` offers actionable fixes.
    """

    quality_score: float = Field(ge=0.0, le=1.0)
    issues: list[str]
    suggestions: list[str]
    proceed: bool


class Target(GauntletModel):
    """Engineer-specified API surface to test a Weapon against.

    ``endpoints`` lists the HTTP method+path pairs the weapon's plans
    should exercise (e.g. ``"PATCH /tasks/{id}"``). Additional configuration
    fields will be added here as the model grows.
    """

    title: str
    endpoints: list[str]


class IterationSpec(GauntletModel):
    index: int
    name: str
    goal: str
    attacker_prompt: str
    inspector_prompt: str
    tier: int = 0
    weapon: WeaponBrief | None = None
    target: Target | None = None


class IterationRecord(GauntletModel):
    spec: IterationSpec
    plans: list[Plan]
    execution_results: list[ExecutionResult]
    findings: list[Finding]


class Clearance(GauntletModel):
    """CI gate decision derived from holdout satisfaction score."""

    passed: bool
    holdout_satisfaction_score: float
    threshold: float
    recommendation: Literal["pass", "conditional", "block"]
    rationale: str


class RiskReport(GauntletModel):
    confidence_score: float = Field(ge=0.0, le=1.0)
    risk_level: Literal["low", "medium", "high", "critical"]
    summary: list[str]
    confirmed_failures: list[str]
    suspicious_patterns: list[str]
    unexplored_surfaces: list[str]
    coverage: list[str]
    conclusion: str


class GauntletRun(GauntletModel):
    clearance: Clearance | None = None
    weapon: Weapon | None = None
    target: Target | None = None
    iterations: list[IterationRecord]
    holdout_results: list[ExecutionResult] = Field(default_factory=list)
    weapon_assessment: WeaponAssessment | None = None
    risk_report: RiskReport
