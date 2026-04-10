# Design Proposal: Dark Factory Alignment

Source: [The Dark Factory Pattern](https://hackernoon.com/the-dark-factory-pattern-moving-from-ai-assisted-to-fully-autonomous-coding)

Flux Gate is currently a standalone adversarial loop. The article describes the full
pipeline it should slot into. These proposals close the gap between the two.

---

## 1. Holdout separation (highest priority)

**The article's key insight:** The coding agent never sees the acceptance scenarios.
Ever. This is the ML train/test split applied to code validation.

**The current problem:** Flux Gate's `Operator` generates scenarios that include
their own assertions. The agent generating the probes and the agent evaluating
correctness are reading from the same pool — there is no holdout layer.

**Proposed change:** Split scenarios into two isolated sets:

```
Operator  →  probe scenarios     (adversarial exploration; sees the spec)
Evaluator →  holdout scenarios   (acceptance criteria; Operator never sees these)
```

`HoldoutEvaluator` is a new protocol that receives a spec and returns acceptance
scenarios independently of the Operator. The runner executes both sets but the
Operator's context is seeded only from probe results.

```python
class HoldoutEvaluator(Protocol):
    def acceptance_scenarios(self, spec: "FeatureSpec") -> list[Scenario]: ...
```

The `RiskReport` gains a `holdout_pass_rate: float` field. The gate (see §4)
is based on holdout pass rate — not probe findings — because probe results
are already in the Operator's context and therefore no longer independent.

---

## 2. Spec-driven input

**The article's model:** Engineers write a markdown file with YAML frontmatter.
Specs go in, tested code comes out merged.

**The current problem:** Flux Gate has no external input. `IterationSpec` is
internal scaffolding, not a user-facing format. There's no way to tell Flux Gate
*what* to test without writing Python.

**Proposed change:** Add a `FeatureSpec` model and accept spec files from the CLI:

```python
class FeatureSpec(FluxGateModel):
    title: str
    description: str                    # plain English; given to the Operator
    acceptance_criteria: list[str]      # plain English; given to HoldoutEvaluator only
    target_endpoints: list[str] = []    # hints: ["POST /tasks", "PATCH /tasks/{id}"]
```

Spec files are YAML on disk:

```yaml
# specs/authz.yaml
title: Users cannot modify each other's tasks
description: >
  The task API should enforce ownership. A user who did not create a task
  must not be able to modify or delete it.
acceptance_criteria:
  - A PATCH request from a non-owner returns 403
  - A DELETE request from a non-owner returns 403
  - The task body is unchanged after an unauthorized PATCH attempt
target_endpoints:
  - PATCH /tasks/{id}
  - DELETE /tasks/{id}
```

CLI becomes:

```bash
flux-gate http://localhost:8000 --spec specs/authz.yaml
```

Without `--spec`, the current behaviour (demo Operator, no holdout) remains for
quick smoke-testing.

---

## 3. Plain-language scenario evaluation

**The article's model:** The evaluator reads a scenario in plain English, plans
its own API calls, executes them, and judges the response. No glue code. No
schema maintenance.

**The current problem:** Flux Gate's `Scenario` objects are structured Python —
explicit steps, explicit assertions. An LLM Operator must produce schema-valid
objects rather than reasoning in natural language. This is both brittle and
inconsistent with how acceptance criteria are written.

**Proposed change:** Add a `NaturalLanguageScenario` that the LLM executor
interprets at runtime, alongside the existing structured `Scenario` for
deterministic cases:

```python
class NaturalLanguageScenario(FluxGateModel):
    name: str
    description: str    # "userB attempts to PATCH a task owned by userA"
    actors: list[str]   # ["userA", "userB"]
    verdict: str        # "request should be rejected with 403"
```

The `HoldoutEvaluator` can return `NaturalLanguageScenario` objects. An LLM-backed
executor interprets them: plans the request sequence, executes it, and returns a
pass/fail judgment with rationale — no `Assertion` objects required.

Structured `Scenario` objects remain for deterministic, pre-planned probe scenarios
where reproducibility matters.

---

## 4. Merge gate

**The article's model:** 90% holdout pass rate gates merges. Below threshold the
PR is blocked. The system earns auto-merge trust incrementally.

**The current problem:** Flux Gate produces a qualitative `RiskReport` with a
risk level and confidence score, but no binary decision. Nothing calls it
pass or fail. There is no threshold.

**Proposed change:** Add `MergeGate` to `RiskReport`:

```python
class MergeGate(FluxGateModel):
    passed: bool
    holdout_pass_rate: float
    threshold: float                                          # default 0.90
    recommendation: Literal["merge", "block", "review"]
    rationale: str
```

`recommendation` logic:
- `holdout_pass_rate >= threshold` → `"merge"`
- `holdout_pass_rate >= threshold * 0.8` → `"review"` (close but below threshold)
- below that → `"block"`

The CLI exits with code 0 on merge/review and code 1 on block, making it a
natural GitHub Actions gate.

---

## 5. Multi-run statistics

**The article's model:** Each scenario runs three times. Two of three must pass.
This accounts for LLM non-determinism and flaky ephemeral environments.

**The current problem:** Every scenario runs exactly once. A single HTTP hiccup
or LLM sampling variance fails the whole run.

**Proposed change:** Add `run_count` and `min_passes` to `FluxGateRunner`:

```python
runner = FluxGateRunner(
    ...,
    run_count=3,      # run each scenario this many times
    min_passes=2,     # require this many passes to count the scenario as passing
)
```

`ScenarioResult` (rename from `ExecutionResult`) gains:

```python
class ScenarioResult(FluxGateModel):
    scenario_name: str
    runs: list[ExecutionResult]   # one per attempt
    passed: bool                  # True if >= min_passes runs passed
    pass_count: int
```

---

## 6. GitHub Actions orchestrator

**The article's model:** The orchestrator is a GitHub Actions workflow that
triggers on PR, deploys an ephemeral environment, runs validation, and
auto-merges or blocks.

**The current problem:** Flux Gate runs manually. There is no PR integration,
no auto-merge, and no ephemeral environment lifecycle.

**Proposed change:** A new `validate.yml` workflow:

```yaml
# .github/workflows/validate.yml
on:
  pull_request:
    paths: ['specs/**']

jobs:
  validate:
    steps:
      - deploy ephemeral environment (docker compose up)
      - run: flux-gate $EPHEMERAL_URL --spec ${{ spec_file }} --gate
      - post gate result as PR comment
      - if gate passes and auto-merge is enabled: merge PR
      - teardown ephemeral environment
```

The `--gate` flag makes the CLI exit 1 on block, which GitHub Actions treats as
a required status check failure.

This is Phase 2→3 of the article's maturity model. Phase 3 (auto-merge) is
gated on earning trust: ≥20 PRs validated, <5% false positive rate, <10% human
override rate.

---

## Proposed sequencing

| Phase | Proposal | Unblocks |
|---|---|---|
| 1 | Spec-driven input (§2) | Everything else; gives the runner external context |
| 2 | Holdout separation (§1) | Independent validation signal |
| 3 | Merge gate (§4) | CI integration |
| 4 | Multi-run statistics (§5) | Production reliability |
| 5 | Plain-language scenarios (§3) | LLM evaluator; no glue code |
| 6 | GitHub Actions orchestrator (§6) | Full dark factory loop |

Spec input is proposed first because it forces a concrete interface between the
engineer writing acceptance criteria and the system that validates them — every
other proposal depends on that boundary existing.
