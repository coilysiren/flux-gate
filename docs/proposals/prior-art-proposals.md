# Design Proposal: Prior Art Synthesis

Sources:
- [StrongDM Software Factory](https://factory.strongdm.ai/)
- [OctopusGarden](https://github.com/foundatron/octopusgarden)
- [Fabro](https://github.com/fabro-sh/fabro)

---

## 1. Probabilistic satisfaction scoring

**Source:** StrongDM + OctopusGarden

**The prior art:** Both projects replace boolean pass/fail with a probabilistic
satisfaction score — what fraction of observed trajectories likely satisfy a user?
OctopusGarden uses a 0–100 LLM judge score. StrongDM uses "satisfaction metrics"
across scenario trajectories. Neither treats a single failed assertion as a
binary blocker.

**The current problem:** Flux Gate's `MergeGate` is based on a binary holdout
pass rate (0.0 or 1.0 for a single scenario). A single assertion failure blocks
the merge regardless of how close the outcome was. There is no way to express
"this almost worked."

**Proposed change:** Add a `satisfaction_score: float` (0.0–1.0) to
`ExecutionResult` and `NaturalLanguageResult`. The score is computed per-scenario:
a structured scenario uses assertion pass ratio; an NL scenario gets an LLM
score. `MergeGate.holdout_pass_rate` becomes `holdout_satisfaction_score` —
the average across all holdout scenarios.

```python
class ExecutionResult(FluxGateModel):
    ...
    satisfaction_score: float = Field(ge=0.0, le=1.0, default=0.0)
```

This unlocks fine-grained gates: a scenario that fails one of five assertions
contributes `0.8` rather than `0.0`, letting nearly-correct implementations
reach "review" rather than "block."

---

## 2. Attractor loop (dynamic convergence)

**Source:** OctopusGarden

**The prior art:** OctopusGarden runs iterations until the satisfaction score
reaches a threshold (default 95%) or a stall is detected — not for a fixed
count. This lets simple systems converge in one or two iterations while complex
or broken systems get more scrutiny.

**The current problem:** Flux Gate always runs exactly four iterations regardless
of outcome. If iteration 1 finds a critical failure with 0.94 confidence, three
more iterations still run. If the system under test is clean, four iterations
still run even after establishing high confidence.

**Proposed change:** Add `convergence_threshold` and `max_iterations` to
`FluxGateRunner`:

```python
runner = FluxGateRunner(
    ...,
    max_iterations=8,
    convergence_threshold=0.95,  # stop early when confidence reaches this
)
```

Stopping conditions:
- `confidence_score >= convergence_threshold` after any iteration → converged clean
- All findings in the last two iterations are identical → stall detected
- `max_iterations` reached → hard stop

The current `build_default_iteration_specs()` behaviour (4 fixed iterations)
becomes the default when `convergence_threshold` is not set.

---

## 3. Stall recovery

**Source:** OctopusGarden ("wonder/reflect" pattern)

**The prior art:** OctopusGarden detects when improvement plateaus and triggers
a high-temperature "wonder" phase to diagnose root causes, followed by
low-temperature "reflect" generation to produce targeted fixes. The two-temperature
approach deliberately trades precision for exploration at the stall point.

**The current problem:** Flux Gate's Adversary produces the same findings when
the same failures appear across iterations. There is no mechanism to break out
when the loop stalls — it just keeps generating the same scenarios.

**Proposed change:** Track finding deltas across iterations. When the last
two `IterationRecord.findings` sets are identical, mark the run as stalled and
inject a `stall_recovery_spec` before continuing:

```python
class IterationSpec(FluxGateModel):
    ...
    mode: Literal["probe", "stall_recovery"] = "probe"
```

Stall recovery iterations receive an `adversary_prompt` that instructs the
Adversary to consider entirely different attack surfaces, forcing the Operator
away from the failing region. In LLM-backed implementations, the temperature
for stall recovery iterations should be higher.

---

## 4. Stratified scenario difficulty

**Source:** OctopusGarden

**The prior art:** OctopusGarden tests scenarios in ascending difficulty tiers.
Easy scenarios run first. If they fail, harder tiers are skipped — there is no
point running adversarial scenarios against a system that can't pass basic
CRUD. This makes the feedback loop faster and the report more readable.

**The current problem:** All four Flux Gate iterations run regardless of
severity. A critical failure in iteration 1 does not prevent iterations 2–4
from running. The operator and adversary continue probing even when the
system has already demonstrated it is fundamentally broken.

**Proposed change:** Add `tier: int` to `IterationSpec` (default 0). Add
`fail_fast_tier: int | None = None` to `FluxGateRunner`. If any iteration in
tier N finds a critical finding, skip all remaining iterations beyond tier N.

```python
runner = FluxGateRunner(
    ...,
    fail_fast_tier=1,  # stop after tier 1 if critical finding found
)
```

Default iteration specs would be tiered:
- Tier 0: `broad_baseline`
- Tier 1: `boundary_and_invariants`
- Tier 2: `adversarial_misuse`
- Tier 3: `targeted_followup`

---

## 5. Preflight spec assessment

**Source:** OctopusGarden

**The prior art:** OctopusGarden evaluates spec clarity and scenario quality
before execution. Low-quality specs produce low-quality scenarios. A preflight
assessment rejects underspecified inputs early rather than wasting a full run
on them.

**The current problem:** Flux Gate accepts any `FeatureSpec` without validation.
A spec with vague acceptance criteria ("make sure it's secure") or missing
`target_endpoints` will generate probe scenarios that explore nothing useful and
a holdout gate that means nothing.

**Proposed change:** Add a `SpecAssessor` protocol and `preflight` step to
`FluxGateRunner.run()`:

```python
class SpecAssessor(Protocol):
    def assess(self, spec: FeatureSpec) -> SpecAssessment: ...

class SpecAssessment(FluxGateModel):
    quality_score: float              # 0.0–1.0
    issues: list[str]                 # e.g. ["acceptance_criteria are too vague"]
    suggestions: list[str]            # e.g. ["specify expected status codes"]
    proceed: bool                     # False blocks the run
```

A `DemoSpecAssessor` uses heuristics: criteria shorter than 20 characters score
low, missing `target_endpoints` score low, criteria mentioning specific status
codes score high.

`FluxGateRun` gains a `spec_assessment: SpecAssessment | None` field. When
`proceed=False`, `run()` returns early with an empty iterations list and a
`block` merge gate.

---

## 6. Digital twin / service mock injection

**Source:** StrongDM (Digital Twin Universe)

**The prior art:** StrongDM's DTU creates behavioral clones of third-party
services (Okta, Stripe, Jira, Slack). These replicate APIs, edge cases, and
failure modes, enabling testing at volumes that exceed production limits and
simulating failure states that cannot be triggered safely in a live environment.

**The current problem:** Flux Gate's `HttpExecutor` sends real requests to a
real running process. If the system under test calls external services (payment
processors, auth providers, email), those calls either succeed silently (masking
failures) or fail unpredictably (producing noise).

**Proposed change:** Add a `ServiceMock` registry to `HttpExecutor`:

```python
class ServiceMock(Protocol):
    def matches(self, request: HttpRequest) -> bool: ...
    def respond(self, request: HttpRequest) -> HttpResponse: ...

class HttpExecutor:
    def __init__(
        self,
        base_url: str,
        actor_headers: dict[str, dict[str, str]] | None = None,
        service_mocks: list[ServiceMock] | None = None,
    ) -> None: ...
```

Before forwarding a request to `base_url`, `HttpExecutor` checks each
`ServiceMock`. The first matching mock intercepts the request and returns a
synthetic response. This keeps Flux Gate's execution layer independent of
external service availability, rate limits, and cost.

---

## Proposed sequencing

| Phase | Proposal | Effort | Unblocks |
|---|---|---|---|
| 1 | Preflight spec assessment (§5) | Low | Catches bad specs before full runs |
| 2 | Stratified difficulty + fail-fast (§4) | Low | Faster feedback on broken systems |
| 3 | Satisfaction scoring (§1) | Medium | Finer-grained gate decisions |
| 4 | Attractor loop (§2) | Medium | Dynamic convergence, removes 4-iteration ceiling |
| 5 | Stall recovery (§3) | Medium | Breaks adversarial deadlocks |
| 6 | Service mock injection (§6) | High | External dependency isolation |
