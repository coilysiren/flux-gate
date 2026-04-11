# Weapon / Target / Vitals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce `Target` as a first-class model, rename `must_hold` → `blockers` on `Weapon`, and wire both through the runner and CLI.

**Architecture:** `Weapon` becomes a system-agnostic attack strategy; `Target` provides the concrete API surface (endpoints) for a specific run. The runner takes both and passes them through to specs, the assessor, and the run record. The CLI gains a `--target` flag and runs a `weapon × target` nested loop.

**Tech Stack:** Python 3.13, Pydantic v2, Click, pytest

---

## File Map

| File | Change |
|---|---|
| `flux_gate/models.py` | Add `Target`; rename `must_hold` → `blockers` on `Weapon`; remove `target_endpoints` from `Weapon`; add `target` to `IterationSpec` and `FluxGateRun` |
| `flux_gate/roles.py` | Import `Target`; update `WeaponAssessor.assess` signature; update `DemoWeaponAssessor`; update `DemoNaturalLanguageHoldoutVitals` |
| `flux_gate/loop.py` | Import `Target`; add `target` param to `FluxGateRunner`; inject into specs; pass to assessor; record in run; rename comprehension variable |
| `flux_gate/cli.py` | Import `Target`; add `_load_targets()`; add `--target` flag; weapon × target loop |
| `flux_gate/__init__.py` | Export `Target` |
| `tests/test_flux_gate.py` | Update `Weapon` construction; add `Target`; add `test_run_records_target` |
| `README.md` | Rename `must_hold` → `blockers`; add `--target` to CLI reference; add Targets section |
| `docs/usage.md` | Rename `must_hold` → `blockers`; add Write targets section |
| `docs/architecture.md` | Update data flow and deterministic section |

---

### Task 1: Rename `must_hold` → `blockers`

**Files:**
- Modify: `flux_gate/models.py`
- Modify: `flux_gate/roles.py`
- Modify: `flux_gate/loop.py`
- Modify: `tests/test_flux_gate.py`

- [ ] **Step 1: Update tests to use `blockers=` (they will fail)**

In `tests/test_flux_gate.py`, find all four `Weapon(...)` constructions and change `must_hold=` to `blockers=`. Leave `target_endpoints=` unchanged for now.

The four occurrences are in:
- `test_nl_holdout_gate_blocks_failing_api`
- `test_holdout_gate_blocks_failing_api`
- `test_preflight_blocks_vague_weapon`
- `test_preflight_passes_good_weapon`

```python
# test_nl_holdout_gate_blocks_failing_api
inv = Weapon(
    title="Users cannot modify each other's tasks",
    description="The task API must enforce resource ownership.",
    blockers=["A PATCH by a non-owner is rejected with 403"],
    target_endpoints=["PATCH /tasks/{id}"],
)

# test_holdout_gate_blocks_failing_api
inv = Weapon(
    title="Users cannot modify each other's tasks",
    description="The task API must enforce resource ownership.",
    blockers=["A PATCH by a non-owner is rejected with 403"],
    target_endpoints=["PATCH /tasks/{id}"],
)

# test_preflight_blocks_vague_weapon
vague = Weapon(
    title="Make it secure",
    description="It should be secure.",
    blockers=["secure", "no bugs"],
    target_endpoints=[],
)

# test_preflight_passes_good_weapon
good = Weapon(
    title="Users cannot modify each other's tasks",
    description="The task API must enforce resource ownership.",
    blockers=["A PATCH by a non-owner is rejected with 403"],
    target_endpoints=["PATCH /tasks/{id}"],
)
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /Users/kai/projects/coilysiren/flux-gate && .venv/bin/pytest tests/ -v 2>&1 | head -40
```

Expected: `ValidationError` or similar — `Weapon` doesn't accept `blockers` yet.

- [ ] **Step 3: Rename `must_hold` → `blockers` in `Weapon` model**

In `flux_gate/models.py`, change:
```python
class Weapon(FluxGateModel):
    """Engineer-authored weapon that drives the adversarial loop.

    ``description`` is given to the Operator to guide probe scenario generation.
    ``blockers`` are given only to the HoldoutVitals — the Operator
    never receives them, preserving the train/test separation.
    """

    title: str
    description: str
    blockers: list[str]
    target_endpoints: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Update all `weapon.must_hold` references in `roles.py`**

In `flux_gate/roles.py`:

`DemoNaturalLanguageHoldoutVitals.acceptance_scenarios` — change `weapon.must_hold` → `weapon.blockers`:
```python
def acceptance_scenarios(self, weapon: Weapon) -> list[NaturalLanguageScenario]:
    return [
        NaturalLanguageScenario(
            name=f"criterion_{i}",
            description=weapon.description,
            actors=["userA", "userB"],
            verdict=criterion,
        )
        for i, criterion in enumerate(weapon.blockers)
    ]
```

`DemoWeaponAssessor.assess` — change `weapon.must_hold` → `weapon.blockers` in two places:
```python
def assess(self, weapon: Weapon) -> WeaponAssessment:
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

    if not weapon.target_endpoints:
        issues.append("No target_endpoints specified.")
        suggestions.append("List the endpoints the weapon covers (e.g. 'PATCH /tasks/{id}').")
        score -= 0.2

    has_status_code = any(self._STATUS_CODE_RE.search(c) for c in weapon.blockers)
    if not has_status_code:
        suggestions.append(
            "Consider adding expected HTTP status codes to blockers."
        )
        score -= 0.1

    quality_score = round(max(0.0, score), 4)
    return WeaponAssessment(
        quality_score=quality_score,
        issues=issues,
        suggestions=suggestions,
        proceed=quality_score >= 0.5,
    )
```

- [ ] **Step 5: Update `loop.py` comment**

In `flux_gate/loop.py`, change the inline comment from:
```python
        # Inject weapon into each iteration so the Operator can read
        # spec.weapon.description — but never must_hold, which
        # is only passed to the holdout vitals below.
```
to:
```python
        # Inject weapon into each iteration so the Operator can read
        # spec.weapon.description — but never blockers, which
        # are only passed to the holdout vitals below.
```

- [ ] **Step 6: Run tests — verify they pass**

```bash
cd /Users/kai/projects/coilysiren/flux-gate && .venv/bin/pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/kai/projects/coilysiren/flux-gate && git add flux_gate/models.py flux_gate/roles.py flux_gate/loop.py tests/test_flux_gate.py && git commit -m "rename: must_hold → blockers on Weapon"
```

---

### Task 2: Add `Target` model; update `Weapon`, `IterationSpec`, `FluxGateRun`

**Files:**
- Modify: `flux_gate/models.py`
- Modify: `tests/test_flux_gate.py`

- [ ] **Step 1: Write a failing test for `Target`**

Add this test to `tests/test_flux_gate.py` (add `Target` to the imports at the top):

```python
from flux_gate import (
    DemoAdversary,
    DemoHoldoutVitals,
    DemoNaturalLanguageHoldoutVitals,
    DemoNaturalLanguageVitals,
    DemoOperator,
    DemoWeaponAssessor,
    DeterministicLocalExecutor,
    FluxGateRunner,
    InMemoryTaskAPI,
    Target,
    Weapon,
)
```

Add this test:

```python
def test_run_records_target() -> None:
    """FluxGateRun records the target passed to the runner."""
    target = Target(title="Task endpoints", endpoints=["POST /tasks", "PATCH /tasks/{id}"])
    runner = FluxGateRunner(
        executor=DeterministicLocalExecutor(InMemoryTaskAPI()),
        operator=DemoOperator(),
        adversary=DemoAdversary(),
        target=target,
    )
    run = runner.run()
    assert run.target == target
```

- [ ] **Step 2: Run the new test — verify it fails**

```bash
cd /Users/kai/projects/coilysiren/flux-gate && .venv/bin/pytest tests/test_flux_gate.py::test_run_records_target -v
```

Expected: `ImportError` — `Target` not exported yet.

- [ ] **Step 3: Add `Target` to `models.py` and update `Weapon`, `IterationSpec`, `FluxGateRun`**

In `flux_gate/models.py`:

Add `Target` after `WeaponAssessment`:
```python
class Target(FluxGateModel):
    """Engineer-specified API surface to test a Weapon against.

    ``endpoints`` lists the HTTP method+path pairs the weapon's scenarios
    should exercise (e.g. ``"PATCH /tasks/{id}"``). Additional configuration
    fields will be added here as the model grows.
    """

    title: str
    endpoints: list[str]
```

Remove `target_endpoints` from `Weapon`:
```python
class Weapon(FluxGateModel):
    """Engineer-authored weapon that drives the adversarial loop.

    ``description`` is given to the Operator to guide probe scenario generation.
    ``blockers`` are given only to the HoldoutVitals — the Operator
    never receives them, preserving the train/test separation.
    """

    title: str
    description: str
    blockers: list[str]
```

Add `target` to `IterationSpec`:
```python
class IterationSpec(FluxGateModel):
    index: int
    name: str
    goal: str
    operator_prompt: str
    adversary_prompt: str
    tier: int = 0
    weapon: Weapon | None = None
    target: Target | None = None
```

Add `target` to `FluxGateRun`:
```python
class FluxGateRun(FluxGateModel):
    weapon: Weapon | None = None
    target: Target | None = None
    iterations: list[IterationRecord]
    holdout_results: list[ExecutionResult] = Field(default_factory=list)
    weapon_assessment: WeaponAssessment | None = None
    risk_report: RiskReport
```

- [ ] **Step 4: Update existing tests — remove `target_endpoints` from `Weapon` constructions**

In `tests/test_flux_gate.py`, remove `target_endpoints=` from all four `Weapon(...)` calls (the field no longer exists):

```python
# test_nl_holdout_gate_blocks_failing_api
inv = Weapon(
    title="Users cannot modify each other's tasks",
    description="The task API must enforce resource ownership.",
    blockers=["A PATCH by a non-owner is rejected with 403"],
)

# test_holdout_gate_blocks_failing_api
inv = Weapon(
    title="Users cannot modify each other's tasks",
    description="The task API must enforce resource ownership.",
    blockers=["A PATCH by a non-owner is rejected with 403"],
)

# test_preflight_blocks_vague_weapon
vague = Weapon(
    title="Make it secure",
    description="It should be secure.",
    blockers=["secure", "no bugs"],
)

# test_preflight_passes_good_weapon
good = Weapon(
    title="Users cannot modify each other's tasks",
    description="The task API must enforce resource ownership.",
    blockers=["A PATCH by a non-owner is rejected with 403"],
)
```

- [ ] **Step 5: Run tests — verify they pass**

```bash
cd /Users/kai/projects/coilysiren/flux-gate && .venv/bin/pytest tests/ -v
```

Expected: all existing tests pass; `test_run_records_target` still fails (`Target` not exported, `FluxGateRunner` doesn't accept `target` yet).

- [ ] **Step 6: Commit**

```bash
cd /Users/kai/projects/coilysiren/flux-gate && git add flux_gate/models.py tests/test_flux_gate.py && git commit -m "feat: add Target model; remove target_endpoints from Weapon"
```

---

### Task 3: Update `WeaponAssessor`, `DemoWeaponAssessor`, and `FluxGateRunner`

These three changes are atomic — updating the assessor protocol signature without updating the runner's call site (or vice versa) breaks the preflight tests. All three files are committed together.

**Files:**
- Modify: `flux_gate/roles.py`
- Modify: `flux_gate/loop.py`
- Modify: `tests/test_flux_gate.py`

- [ ] **Step 1: Update `test_preflight_passes_good_weapon` to pass a `Target` (will fail until Step 3)**

In `tests/test_flux_gate.py`, update `test_preflight_passes_good_weapon`:

```python
def test_preflight_passes_good_weapon() -> None:
    """DemoWeaponAssessor accepts a well-formed weapon+target and allows the loop to run."""
    good = Weapon(
        title="Users cannot modify each other's tasks",
        description="The task API must enforce resource ownership.",
        blockers=["A PATCH by a non-owner is rejected with 403"],
    )
    target = Target(title="Task endpoints", endpoints=["PATCH /tasks/{id}"])

    runner = FluxGateRunner(
        executor=DeterministicLocalExecutor(InMemoryTaskAPI()),
        operator=DemoOperator(),
        adversary=DemoAdversary(),
        assessor=DemoWeaponAssessor(),
        weapon=good,
        target=target,
    )

    run = runner.run()

    assert run.weapon_assessment is not None
    assert run.weapon_assessment.proceed is True
    assert run.weapon_assessment.quality_score >= 0.5
    assert len(run.iterations) == 4
```

Note: `test_preflight_blocks_vague_weapon` does NOT need a `target` — the vague blockers alone drive the score below 0.5 (`score -= 0.3` twice = 0.4). Omitting `target` keeps the test focused on blocker quality.

- [ ] **Step 2: Run the updated test — verify it fails**

```bash
cd /Users/kai/projects/coilysiren/flux-gate && .venv/bin/pytest tests/test_flux_gate.py::test_preflight_passes_good_weapon tests/test_flux_gate.py::test_run_records_target -v
```

Expected: both fail — `FluxGateRunner` doesn't accept `target` yet.

- [ ] **Step 3: Update `roles.py` — `WeaponAssessor` protocol and `DemoWeaponAssessor`**

Add `Target` to the imports at the top of `flux_gate/roles.py`:
```python
from .models import (
    Assertion,
    AssertionResult,
    ExecutionResult,
    Finding,
    HttpRequest,
    IterationRecord,
    IterationSpec,
    NaturalLanguageScenario,
    Scenario,
    ScenarioStep,
    Target,
    Weapon,
    WeaponAssessment,
)
```

Update `WeaponAssessor` protocol:
```python
class WeaponAssessor(Protocol):
    """Evaluates a Weapon for quality before the adversarial loop runs.

    Returns a ``WeaponAssessment`` with a quality score, issues, suggestions,
    and a ``proceed`` flag. When ``proceed`` is ``False``, the runner skips
    all iterations and returns a blocked merge gate.
    """

    def assess(self, weapon: Weapon, target: Target | None) -> WeaponAssessment: ...
```

Update `DemoWeaponAssessor.assess`:
```python
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
        suggestions.append(
            "Consider adding expected HTTP status codes to blockers."
        )
        score -= 0.1

    quality_score = round(max(0.0, score), 4)
    return WeaponAssessment(
        quality_score=quality_score,
        issues=issues,
        suggestions=suggestions,
        proceed=quality_score >= 0.5,
    )
```

- [ ] **Step 4: Update `loop.py` — add `target` to `FluxGateRunner` and update all call sites**

Add `Target` to the models import in `flux_gate/loop.py`:
```python
from .models import (
    ExecutionResult,
    Finding,
    FluxGateRun,
    IterationRecord,
    IterationSpec,
    MergeGate,
    RiskReport,
    Target,
    Weapon,
    WeaponAssessment,
)
```

Add `target` parameter to `FluxGateRunner.__init__`:
```python
class FluxGateRunner:
    def __init__(
        self,
        executor: DeterministicLocalExecutor,
        operator: Operator,
        adversary: Adversary,
        holdout_vitals: HoldoutVitals | None = None,
        nl_holdout_vitals: NaturalLanguageHoldoutVitals | None = None,
        nl_vitals: NaturalLanguageVitals | None = None,
        assessor: WeaponAssessor | None = None,
        weapon: Weapon | None = None,
        target: Target | None = None,
        gate_threshold: float = 0.90,
        fail_fast_tier: int | None = None,
    ) -> None:
        self._executor = executor
        self._operator = operator
        self._adversary = adversary
        self._holdout_vitals = holdout_vitals
        self._nl_holdout_vitals = nl_holdout_vitals
        self._nl_vitals = nl_vitals
        self._assessor = assessor
        self._weapon = weapon
        self._target = target
        self._gate_threshold = gate_threshold
        self._fail_fast_tier = fail_fast_tier
```

Update `run()` to pass `target` to the assessor, inject it into specs, and record it in the run:
```python
    def run(self, iterations: list[IterationSpec] | None = None) -> FluxGateRun:
        specs = iterations or build_default_iteration_specs()

        # Preflight: assess weapon quality before running any iterations.
        weapon_assessment: WeaponAssessment | None = None
        if self._assessor is not None and self._weapon is not None:
            weapon_assessment = self._assessor.assess(self._weapon, self._target)
            if not weapon_assessment.proceed:
                return self._blocked_by_preflight(weapon_assessment)

        # Inject weapon and target into each iteration so the Operator can read
        # spec.weapon.description — but never blockers, which
        # are only passed to the holdout vitals below.
        if self._weapon:
            specs = [
                s.model_copy(update={"weapon": self._weapon, "target": self._target})
                for s in specs
            ]

        records: list[IterationRecord] = []
        for spec in specs:
            scenarios = self._operator.generate_scenarios(spec, records)
            execution_results = [self._executor.run_scenario(scenario) for scenario in scenarios]
            findings = self._adversary.analyze(spec, execution_results)
            records.append(
                IterationRecord(
                    spec=spec,
                    scenarios=scenarios,
                    execution_results=execution_results,
                    findings=findings,
                )
            )

            if self._fail_fast_tier is not None and spec.tier >= self._fail_fast_tier:
                if any(f.severity == "critical" for f in findings):
                    break

        holdout_results: list[ExecutionResult] = []
        if self._weapon is not None:
            if self._holdout_vitals is not None:
                for scenario in self._holdout_vitals.acceptance_scenarios(self._weapon):
                    holdout_results.append(self._executor.run_scenario(scenario))

            if self._nl_holdout_vitals is not None and self._nl_vitals is not None:
                nl_scenarios = self._nl_holdout_vitals.acceptance_scenarios(self._weapon)
                for nl_scenario in nl_scenarios:
                    holdout_results.append(self._nl_vitals.evaluate(nl_scenario, self._executor))

        return FluxGateRun(
            weapon=self._weapon,
            target=self._target,
            iterations=records,
            holdout_results=holdout_results,
            weapon_assessment=weapon_assessment,
            risk_report=_build_risk_report(records, holdout_results, self._gate_threshold),
        )
```

Update `_blocked_by_preflight` to record `target`:
```python
    def _blocked_by_preflight(self, assessment: WeaponAssessment) -> FluxGateRun:
        rationale = (
            f"Weapon quality score {assessment.quality_score:.0%} is too low to proceed. "
            f"Issues: {'; '.join(assessment.issues) or 'none'}."
        )
        return FluxGateRun(
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
                merge_gate=MergeGate(
                    passed=False,
                    holdout_satisfaction_score=0.0,
                    threshold=self._gate_threshold,
                    recommendation="block",
                    rationale=rationale,
                ),
            ),
        )
```

Also rename the comprehension variable `target` in `_derive_unexplored_surfaces` to avoid shadowing the `Target` import:
```python
def _derive_unexplored_surfaces(findings: list[Finding]) -> list[str]:
    if not findings:
        return ["No high-risk unexplored surfaces identified."]
    return sorted({surface for finding in findings for surface in finding.next_targets})
```

- [ ] **Step 2: Run tests — verify they all pass**

```bash
cd /Users/kai/projects/coilysiren/flux-gate && .venv/bin/pytest tests/ -v
```

Expected: all tests pass, including `test_run_records_target` and `test_preflight_passes_good_weapon`.

- [ ] **Step 3: Commit**

```bash
cd /Users/kai/projects/coilysiren/flux-gate && git add flux_gate/loop.py && git commit -m "feat: wire target into FluxGateRunner"
```

---

### Task 4: Wire `target` into CLI and export from `__init__.py`

**Files:**
- Modify: `flux_gate/cli.py`
- Modify: `flux_gate/__init__.py`

- [ ] **Step 1: Update `cli.py`**

Add `Target` to the models import:
```python
from .models import Target, Weapon
```

Add `_load_targets()` directly after `_load_weapons()`:
```python
def _load_targets(spec: str) -> list[Target]:
    """Return targets from a single YAML file or all *.yaml files in a directory."""
    path = Path(spec)
    if not path.exists():
        return []
    if path.is_dir():
        return [Target(**yaml.safe_load(f.read_text())) for f in sorted(path.glob("*.yaml"))]
    return [Target(**yaml.safe_load(path.read_text()))]
```

Add `--target` option to the `@click.command` decorator block (after `--weapon`):
```python
@click.option(
    "--target",
    default=".flux_gate/targets",
    metavar="FILE_OR_DIR",
    show_default=True,
    help="Path to a Target YAML file, or a directory of YAML files (one target per file).",
)
```

Update `main` signature to include `target`:
```python
def main(url: str, weapon: str, target: str, actors: str, threshold: float, fail_fast: bool) -> None:
```

Replace the run loop with the weapon × target nested loop:
```python
    weapons = _load_weapons(weapon)
    targets = _load_targets(target)

    actor_headers: dict[str, dict[str, str]] = {}
    actors_path = Path(actors)
    if actors_path.exists():
        actor_headers = to_actor_headers(ActorsConfig(**yaml.safe_load(actors_path.read_text())))

    operator = create_operator(operator_type, operator_key)
    adversary = create_adversary(adversary_type, adversary_key)
    executor = DeterministicLocalExecutor(HttpExecutor(url, actor_headers=actor_headers))

    blocked = False
    for inv in weapons or [None]:  # type: ignore[list-item]
        for tgt in targets or [None]:  # type: ignore[list-item]
            runner = FluxGateRunner(
                executor=executor,
                operator=operator,
                adversary=adversary,
                assessor=DemoWeaponAssessor() if inv else None,
                weapon=inv,
                target=tgt,
                gate_threshold=threshold,
                fail_fast_tier=0 if fail_fast else None,
            )

            try:
                run = runner.run()
            except Exception as exc:  # noqa: BLE001
                click.echo(f"error: {exc}", err=True)
                sys.exit(1)

            click.echo(yaml.dump(run.model_dump(), sort_keys=False, allow_unicode=True))

            gate = run.risk_report.merge_gate
            if gate and gate.recommendation == "block":
                click.echo(f"gate: BLOCKED — {gate.rationale}", err=True)
                blocked = True

    if blocked:
        sys.exit(1)
```

- [ ] **Step 2: Export `Target` from `__init__.py`**

In `flux_gate/__init__.py`, add `Target` to the models import and `__all__`:

```python
from .models import (
    Assertion,
    AssertionResult,
    ExecutionResult,
    ExecutionStepResult,
    Finding,
    FluxGateRun,
    HttpRequest,
    HttpResponse,
    IterationRecord,
    IterationSpec,
    MergeGate,
    NaturalLanguageScenario,
    RiskReport,
    Scenario,
    ScenarioStep,
    Target,
    Weapon,
    WeaponAssessment,
)
```

Add `"Target"` to `__all__` (keep alphabetical order, between `"Scenario"` and `"ScenarioStep"` ... actually between `"ScenarioStep"` and `"Weapon"`):
```python
    "Target",
    "Weapon",
    "WeaponAssessment",
```

- [ ] **Step 3: Run tests — verify all pass**

```bash
cd /Users/kai/projects/coilysiren/flux-gate && .venv/bin/pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
cd /Users/kai/projects/coilysiren/flux-gate && git add flux_gate/cli.py flux_gate/__init__.py && git commit -m "feat: add --target flag to CLI; export Target"
```

---

### Task 5: Update docs

**Files:**
- Modify: `README.md`
- Modify: `docs/usage.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Update `README.md`**

1. Fix stale anchor in the CLI table: change `[Weapon YAML](#guards)` → `[Weapon YAML](#weapons)`.

2. Add `--target` row to the CLI options table (after `--weapon`):

```markdown
| `--target` | `.flux_gate/targets` | Path to a Target YAML file, or a directory of YAML files (one target per file) |
```

3. Add `--target` to the usage examples:

```bash
flux-gate http://localhost:8000 --weapon /path/to/weapons/ --target /path/to/targets/ --actors /path/to/actors.yaml
flux-gate http://localhost:8000 --weapon /path/to/single_weapon.yaml --target /path/to/single_target.yaml
```

4. In the project config directory section, add `targets/`:

```
your-project/
├── .flux_gate/
│   ├── weapons/            # one YAML file per Weapon — all loaded automatically
│   │   ├── task_ownership.yaml
│   │   └── task_read_isolation.yaml
│   ├── targets/            # one YAML file per Target — all loaded automatically
│   │   └── task_endpoints.yaml
│   └── actors.yaml         # Actor auth — loaded automatically if present
└── ...
```

5. In the Weapons section, rename `must_hold:` → `blockers:` in the YAML example:

```yaml
# .flux_gate/weapons/task_ownership.yaml
title: Users cannot modify each other's tasks
description: >
  The task API must enforce resource ownership. A user who did not create
  a task must not be able to modify or delete it.
blockers:
  - A PATCH request by a non-owner is rejected with 403
  - The task body is unchanged after an unauthorized PATCH attempt
  - A GET by the owner after an unauthorized PATCH returns the original data
```

6. Add a Targets section after Weapons:

```markdown
### Targets

A Target defines the API surface a Weapon is tested against. One target per YAML file.

```yaml
# .flux_gate/targets/task_endpoints.yaml
title: Task ownership endpoints
endpoints:
  - POST /tasks
  - PATCH /tasks/{id}
  - GET /tasks/{id}
```

Point multiple targets at the same weapon to test the same attack across different API surfaces.
```

- [ ] **Step 2: Update `docs/usage.md`**

1. In the "Write weapons" section, rename `must_hold:` → `blockers:` in the YAML example and the explanation paragraph:

```yaml
# .flux_gate/weapons/task_ownership.yaml
title: Users cannot modify each other's tasks
description: >
  The task API must enforce resource ownership. A user who did not create
  a task must not be able to modify or delete it.
blockers:
  - A PATCH request by a non-owner is rejected with 403
  - The task body is unchanged after an unauthorized PATCH attempt
  - A GET by the owner after an unauthorized PATCH returns the original data
```

Update the train/test split paragraph:
```
**The train/test split:** `blockers` are never shown to the Operator — only to the holdout evaluator. This means the agent that wrote the code cannot inadvertently write code that passes by knowing what the checks are. Keep `blockers` statements specific and falsifiable.
```

Update the tips:
```
- `blockers` statements should describe observable HTTP behavior, not implementation details
```

2. Add a "Write targets" section after "Write weapons":

```markdown
## Write targets

Targets define the API surface a weapon is tested against. Each target is a YAML file in `.flux_gate/targets/`.

```yaml
# .flux_gate/targets/task_endpoints.yaml
title: Task ownership endpoints
endpoints:
  - POST /tasks
  - PATCH /tasks/{id}
  - GET /tasks/{id}
```

One weapon can be paired with many targets — the runner executes one pass per weapon/target combination. If no targets are configured, each weapon runs without a specific target.
```

- [ ] **Step 3: Update `docs/architecture.md`**

In the data flow section, update the IterationSpec injection line to mention target:

```markdown
│   ├── DeterministicLocalExecutor.run_scenario(scenario) × N
│   │     ├── resolves path templates from prior step responses
│   │     ├── calls Api.send(actor, request)
│   │     └── evaluates assertions → []AssertionResult
│   │         returns ExecutionResult
```

In the "Deterministic vs non-deterministic" section, find any reference to `must_hold` and change it to `blockers`. Search the file for `must_hold` and replace any occurrence in prose with `blockers`.

- [ ] **Step 4: Run tests one final time**

```bash
cd /Users/kai/projects/coilysiren/flux-gate && .venv/bin/pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/kai/projects/coilysiren/flux-gate && git add README.md docs/usage.md docs/architecture.md && git commit -m "docs: update weapon/target/blockers references"
```
