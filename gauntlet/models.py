from __future__ import annotations

import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


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
    kind: Literal["status_code"] = "status_code"
    expected: Any | None = None
    step_index: int
    name: str


class PlanStep(GauntletModel):
    user: str
    request: HttpRequest
    extract: dict[str, str] = Field(default_factory=dict)
    """Map from template-variable name to JSONPath-ish response body key.

    Each entry captures a value from this step's response and writes it to the
    Drone's path-template context for subsequent steps. Values are simple
    dotted keys into ``response.body`` (e.g. ``id`` or ``data.id``); no
    jmespath, no wildcards. Missing paths are silently skipped.

    Example: ``extract={"order_id": "id"}`` on a ``POST /orders`` step makes
    ``{order_id}`` available to later steps' paths.
    """


class Plan(GauntletModel):
    name: str
    category: str
    goal: str
    steps: list[PlanStep]
    assertions: list[Assertion] = Field(default_factory=list)
    weapon_id: str | None = None


class ExecutionStepResult(GauntletModel):
    step_index: int
    user: str
    request: HttpRequest
    response: HttpResponse
    duration_ms: float = 0.0
    """Wall-clock time from request send to response received, in milliseconds.

    Populated by the Drone via ``time.perf_counter()``. Defaults to ``0.0`` so
    hand-built fixtures and earlier JSONL buffers don't need rewriting.
    """
    response_size_bytes: int = 0
    """Raw byte length of the response body as received over the wire."""
    response_headers: dict[str, str] = Field(default_factory=dict)
    """Filtered subset of response headers — see ``gauntlet.http`` for the allowlist.

    The filter keeps the surface to a stable, privacy-aware set the Inspector
    can reason about without drowning in noise.
    """
    outcome: Literal["ok", "timeout", "connection_reset", "dns_failure", "other_error"] = "ok"
    """Transport-level disposition.

    Distinguishes clean HTTP responses (``ok``) from network failures that
    would otherwise surface as exceptions. On non-``ok`` outcomes the Drone
    still records a synthetic ``HttpResponse`` (``status_code=0``, empty body)
    so downstream consumers see a uniform shape.
    """


class AssertionResult(GauntletModel):
    name: str
    kind: Literal["status_code"] = "status_code"
    passed: bool
    detail: str


class ExecutionResult(GauntletModel):
    # Override extra="forbid" so the computed ``satisfaction_score`` can survive
    # a JSON round-trip through the run buffer (model_dump emits it, validate
    # would otherwise reject it as an unknown field).
    model_config = ConfigDict(extra="ignore")

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


class EvidenceItem(GauntletModel):
    kind: Literal["request", "response", "assertion", "note"]
    content: str


class ReplayStep(GauntletModel):
    """A single action in a replay bundle: who performed it and what they sent."""

    user: str
    request: HttpRequest


class ReplayBundle(GauntletModel):
    """Minimal sequence of actions sufficient to reproduce a finding.

    Full determinism is not guaranteed — dynamic IDs and state-dependent paths
    may vary across runs — but the bundle captures enough context to attempt
    reproduction against a fresh instance of the system under test.
    """

    steps: list[ReplayStep]


class Finding(GauntletModel):
    issue: str
    severity: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    weapon_id: str | None = None
    next_targets: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    reproduction_steps: list[str] = Field(default_factory=list)
    traces: list[ExecutionStepResult] = Field(default_factory=list)
    violated_blocker: str | None = None
    replay_bundle: ReplayBundle | None = None
    is_anomaly: bool = False


class Weapon(GauntletModel):
    """Engineer-authored weapon that drives the adversarial loop.

    ``id`` is a stable snake_case identifier (e.g.
    ``resource_ownership_write_isolation``) used to accumulate failure
    knowledge across runs.  ``title`` is the human-readable alias.

    ``description`` is given to the Attacker to guide probe plan generation.
    ``blockers`` are the Weapon's Vitals — externally observable truths
    about expected system behavior — given only to the HoldoutEvaluator.
    The Attacker never receives them, preserving the train/test separation.

    Use ``Weapon.attacker_view()`` to produce the dict that ``list_weapons``
    returns to attacker contexts.
    """

    _SNAKE_CASE_RE: ClassVar[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")

    id: str | None = None
    title: str
    description: str
    blockers: list[str]
    inspired_by: str | None = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str | None) -> str | None:
        if value is not None and not cls._SNAKE_CASE_RE.match(value):
            raise ValueError(
                f"Weapon id must be non-empty snake_case "
                f"(e.g. 'resource_ownership_write_isolation'), got {value!r}"
            )
        return value

    def attacker_view(self) -> dict[str, str | None]:
        """Return the attacker-safe view of this weapon (no blockers)."""
        return {"id": self.id, "title": self.title, "description": self.description}


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


class IterationRecord(GauntletModel):
    spec: IterationSpec
    plans: list[Plan]
    execution_results: list[ExecutionResult]
    findings: list[Finding]


class HoldoutResult(GauntletModel):
    """Result of executing one acceptance plan derived from a Weapon's blockers.

    Captured by the HoldoutEvaluator after running a structured ``Plan`` that
    tests one of the weapon's blockers. The orchestrator reads these via
    ``read_holdout_results`` to assemble the final clearance gate. Inspector
    and Attacker contexts must never read holdout results — doing so leaks
    blocker semantics back across the train/test split.
    """

    weapon_id: str
    blocker_index: int | None = None
    blocker: str | None = None
    execution_result: ExecutionResult


class Clearance(GauntletModel):
    """CI gate decision derived from holdout satisfaction score."""

    passed: bool
    holdout_satisfaction_score: float
    threshold: float
    recommendation: Literal["pass", "conditional", "block"]
    rationale: str


class FailureCluster(GauntletModel):
    """A group of blocker findings sharing ``(endpoint, method, severity)``.

    Emitted by ``build_risk_report`` so the host can prioritize: 8 findings
    on the same endpoint are 1 issue, not 8. ``representative_issue`` is the
    ``issue`` text of the highest-confidence finding in the cluster.
    """

    endpoint: str
    method: str
    severity: Literal["low", "medium", "high"]
    size: int
    representative_issue: str


class ResponseCollision(GauntletModel):
    """Identical response fingerprint observed across multiple distinct plans.

    Two distinct attack patterns producing identical responses is signal —
    either both miss the same gap, or there is a common vulnerable code
    path. ``fingerprint`` is a stable string of ``(status, body shape, size
    bucket)``.
    """

    fingerprint: str
    occurrences: int
    distinct_plans: int


class TimingAnomaly(GauntletModel):
    """A single step whose duration is >= 10x the endpoint median.

    Populated only when ``ExecutionStepResult`` carries ``duration_ms`` and
    the endpoint has at least three timing samples.
    """

    method: str
    path: str
    duration_ms: float
    endpoint_median_ms: float


class RiskReport(GauntletModel):
    confidence_score: float = Field(ge=0.0, le=1.0)
    risk_level: Literal["low", "medium", "high"]
    summary: list[str]
    confirmed_failures: list[str]
    suspicious_patterns: list[str]
    unexplored_surfaces: list[str]
    anomalies: list[str] = Field(default_factory=list)
    coverage: list[str]
    conclusion: str
    failure_clusters: list[FailureCluster] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    response_collisions: list[ResponseCollision] = Field(default_factory=list)
    timing_anomalies: list[TimingAnomaly] = Field(default_factory=list)


class WeaponReport(GauntletModel):
    """One weapon's contribution to a multi-weapon `FinalClearance`.

    Carries the per-weapon `RiskReport` and `Clearance` plus the weapon id
    so the orchestrator can correlate results back to the run buffer.
    """

    weapon_id: str
    risk_report: RiskReport
    clearance: Clearance | None = None


class FinalClearance(GauntletModel):
    """Aggregated clearance across every weapon in a run.

    Produced by ``assemble_final_clearance``. The host treats
    ``final_recommendation`` as its overall pass/fail decision; the per-weapon
    reports are kept inline so a failed run can be unpacked without a second
    round-trip to the buffer.

    Aggregation rules (defaults — override in the docstring of the tool that
    constructs this if you change them):

    - ``overall_confidence`` — minimum of per-weapon ``risk_report.confidence_score``
      and per-weapon ``clearance.holdout_satisfaction_score`` (weakest link
      dominates). Weapons without a holdout still count their confidence score.
    - ``max_risk_level`` — highest severity across all per-weapon risk levels.
    - ``final_recommendation`` — ``pass`` if ``overall_confidence >=
      clearance_threshold`` AND no per-weapon ``high``-risk level; ``conditional``
      if threshold met but at least one per-weapon ``medium``-risk level exists;
      ``block`` otherwise.
    """

    overall_confidence: float = Field(ge=0.0, le=1.0)
    max_risk_level: Literal["low", "medium", "high"]
    all_confirmed_failures: list[str]
    final_recommendation: Literal["pass", "conditional", "block"]
    rationale: str
    clearance_threshold: float
    per_weapon_reports: list[WeaponReport]
