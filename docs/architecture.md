# Architecture

## Module map

```
flux_gate/
├── models.py    # all Pydantic data models — the shared vocabulary
├── roles.py     # Operator and Adversary protocols + demo implementations
├── executor.py  # SystemUnderTest protocol + DeterministicLocalExecutor
└── loop.py      # FluxGateRunner orchestration + risk report assembly
```

Nothing imports from `loop.py` except `__init__.py`. Dependency order is:

```
models  ←  roles
models  ←  executor
models + roles + executor  ←  loop
```

## Key abstractions

### `SystemUnderTest` (executor.py)

```python
class SystemUnderTest(Protocol):
    def send(self, actor: str, request: HttpRequest) -> HttpResponse: ...
```

Anything that can receive an HTTP-shaped request and return a response. The only
implementation today is `InMemoryTaskAPI`, a fake REST API with an intentional
authorization flaw. A real integration would replace this with an HTTP client
pointed at a live service.

### `Operator` (roles.py)

```python
class Operator(Protocol):
    def generate_scenarios(
        self, spec: IterationSpec, previous_iterations: list[IterationRecord]
    ) -> list[Scenario]: ...
```

Generates test scenarios for one iteration. Receives the current iteration's goal
and everything found so far. The demo implementation always returns the same
cross-user modification scenario; a real implementation would call an LLM.

### `Adversary` (roles.py)

```python
class Adversary(Protocol):
    def analyze(
        self, spec: IterationSpec, execution_results: list[ExecutionResult]
    ) -> list[Finding]: ...
```

Analyzes execution results and returns findings. The demo always surfaces the
authorization flaw if any assertions failed. A real implementation would call
an LLM.

### `DeterministicLocalExecutor` (executor.py)

Runs scenarios against a `SystemUnderTest`. Handles `{task_id}`-style path
template substitution using values captured from prior steps in the same
scenario (currently only `id` from a `POST /tasks` response).

## Data flow

```
FluxGateRunner.run()
│
├── for each IterationSpec (4 total):
│   ├── Operator.generate_scenarios(spec, previous records)
│   │     └── returns []Scenario
│   │
│   ├── DeterministicLocalExecutor.run_scenario(scenario) × N
│   │     ├── resolves path templates from prior step responses
│   │     ├── calls SystemUnderTest.send(actor, request)
│   │     └── evaluates assertions → []AssertionResult
│   │         returns ExecutionResult
│   │
│   ├── Adversary.analyze(spec, execution_results)
│   │     └── returns []Finding
│   │
│   └── appends IterationRecord to records
│
└── _build_risk_report(records)
      ├── aggregates findings across all iterations
      ├── derives coverage from all executed steps
      ├── computes confidence_score (1 - avg finding confidence)
      ├── derives risk_level from highest finding severity
      └── returns RiskReport
```

## Adding a real Operator or Adversary

Implement the protocol — no base class needed:

```python
class MyOperator:
    def generate_scenarios(
        self, spec: IterationSpec, previous_iterations: list[IterationRecord]
    ) -> list[Scenario]:
        # call your LLM here, parse response into Scenario objects
        ...

runner = FluxGateRunner(
    executor=DeterministicLocalExecutor(MyRealAPI()),
    operator=MyOperator(),
    adversary=MyAdversary(),
)
```

## Adding a new assertion kind

1. Add the literal to `Assertion.kind` in `models.py`
2. Add an evaluation branch in `_evaluate_assertion()` in `executor.py`
3. Add a test case in `tests/test_flux_gate.py`

## Adding a new invariant rule

Add a branch keyed on `assertion.rule` inside the `"invariant"` block of
`_evaluate_assertion()` in `executor.py`. The rule string is set by the
`Operator` when it constructs the `Assertion` object.

## Risk report math

| Field | Formula |
|---|---|
| `confidence_score` | `1 - mean(finding.confidence)` across all findings; `0.9` if no findings |
| `risk_level` | highest severity across all findings; `"low"` if none |
| `coverage` | sorted set of `"METHOD /path"` strings from every executed step |
| `unexplored_surfaces` | union of `finding.next_targets` across all findings |

## Design decisions

**Why Pydantic?** All interchange objects are `BaseModel` subclasses with
`extra="forbid"`. This catches schema drift early and makes JSON
serialization/deserialization free.

**Why 4 fixed iterations?** v0 trades flexibility for predictability. The four
goals (baseline → boundary → adversarial → targeted) form a natural escalation
ladder that works well for demo purposes. Future versions will make this
configurable.

**Why Protocols instead of ABCs?** Structural subtyping lets callers pass any
object that has the right methods without importing from `flux_gate`. This keeps
the integration surface small and avoids inheritance coupling.
