# Architecture

## Module map

```
gauntlet/
├── models.py    # all Pydantic data models — the shared vocabulary
│                #   includes Action/Observation (surface-agnostic wrappers
│                #   around HttpRequest/HttpResponse and future action types)
├── auth.py      # user authentication config (BearerAuth, ApiKeyAuth, UsersConfig)
├── openapi.py   # OpenAPI 3.x spec parser — turns a YAML/JSON spec into Target objects
├── roles.py     # Attacker, Inspector, HoldoutVitals, WeaponAssessor protocols + demo impls
├── adapters/    # Adapter protocol + concrete implementations
│   ├── __init__.py   # Adapter protocol (send + execute)
│   ├── http.py       # HttpApi (real HTTP) + InMemoryHttpApi (demo)
│   ├── cli.py        # CliAdapter (stub)
│   └── webdriver.py  # WebDriverAdapter (stub)
├── executor.py  # Drone — runs plans via Adapter.execute(Action) → Observation
├── llm.py       # LLMAttacker and LLMInspector backed by OpenAI or Anthropic
├── loop.py      # GauntletRunner orchestration + risk report assembly
├── store.py     # PlanStore and FindingsStore — disk-backed knowledge indexed by weapon ID
├── schemas/     # JSON Schemas generated from Pydantic models (Weapon, Target,
│                #   Arsenal, UsersConfig) — committed so external tooling can
│                #   validate user-authored YAML without importing the package.
│                #   Regenerate with `uv run python scripts/export_schemas.py`.
└── cli.py       # Click entry point — reads env vars, loads config, runs GauntletRunner
```

Nothing imports from `loop.py` or `cli.py` except `__init__.py`. Dependency order is:

```
models  ←  auth
models  ←  adapters (http, cli, webdriver, __init__)
models  ←  openapi
models  ←  roles
models  ←  store
models + auth  ←  schemas
models + adapters  ←  executor
models + roles + executor + store  ←  loop
models + auth + openapi + roles + executor + llm + loop  ←  cli
```

## Data flow

```
GauntletRunner.run()
│
├── [preflight] WeaponAssessor.assess(weapon) — if assessor present
│     └── returns WeaponAssessment; blocked → short-circuit
│
├── for each IterationSpec (4 total):
│   ├── Attacker.generate_plans(spec, previous records)
│   │     └── returns []Plan
│   │
│   ├── Drone.run_plan(plan) × N
│   │     ├── resolves path templates from prior step responses
│   │     ├── wraps HttpRequest in Action, calls Adapter.execute(user, action)
│   │     ├── unwraps Observation back to HttpResponse
│   │     └── evaluates assertions → []AssertionResult
│   │         returns ExecutionResult
│   │
│   ├── Inspector.analyze(spec, execution_results)
│   │     └── returns []Finding (includes anomalies: is_anomaly=True)
│   │
│   └── appends IterationRecord to records
│
├── [holdout] HoldoutVitals or NaturalLanguageHoldoutVitals
│     └── evaluates weapon acceptance plans (Attacker never sees these)
│
└── _build_risk_report(records)
      ├── aggregates findings across all iterations
      ├── separates blocker findings from anomalies (is_anomaly flag)
      ├── derives coverage from all executed steps
      ├── computes confidence_score (plan diversity + surface depth + exploration completeness)
      ├── derives risk_level from highest blocker-finding severity (anomalies excluded)
      ├── collects anomalies into RiskReport.anomalies for future weapon refinement
      ├── evaluates clearance against gate_threshold
      └── returns RiskReport
```

## Deterministic vs non-deterministic segments

The system is split into a **deterministic core** and **non-deterministic edges**.

**Deterministic (no LLM, no network):**

- `InMemoryHttpApi` — in-memory REST API with three deterministic seeded flaws: (1) PATCH without ownership check, (2) POST accepts invalid data types for title and missing required fields, (3) GET /tasks list endpoint leaks all tasks regardless of ownership. Pure dict operations, always same output for same input. Ships with the library as a working example SUT.
- `Drone` — resolves path templates, calls the SUT, evaluates assertions. Pure Python.
- Assertion evaluation and risk report assembly — branching logic, set unions, averages, threshold arithmetic. Fully reproducible.
- `Demo*` classes — hardcoded or regex-based implementations of each Protocol. Shipped with the library so users can run the full loop without API keys.

**Non-deterministic (LLM or network):**

- `LLMAttacker` / `LLMInspector` (`llm.py`) — call an LLM to generate plans and analyze findings. Output varies per call.
- `HttpExecutor` — sends real HTTP requests; outcome depends on network and the running server.

The `Demo*` classes are reference implementations of each Protocol in `roles.py`. They exist so that `GauntletRunner` can be exercised end-to-end in tests and examples without any external dependencies. The `LLM*` classes are the production counterparts.

## Design decisions

**Why Pydantic?** All interchange objects are `BaseModel` subclasses with
`extra="forbid"`. This catches schema drift early and makes JSON
serialization/deserialization free.

**Why 4 fixed iterations?** v0 trades flexibility for predictability. The four
goals (baseline → boundary → adversarial_misuse → targeted_escalation) form a natural escalation
ladder that works well for demo purposes. Future versions will make this
configurable.

**Why Protocols instead of ABCs?** Structural subtyping lets callers pass any
object that has the right methods without importing from `gauntlet`. This keeps
the integration surface small and avoids inheritance coupling.

**Why separate auth.py?** User credentials involve secret resolution from env
vars. Isolating this in `auth.py` keeps the rest of the codebase free of
secret-handling logic and makes the boundary clear.

**Why Action/Observation instead of passing HttpRequest/HttpResponse directly?**
The adversarial loop should not be coupled to a single execution surface.
Action wraps an HttpRequest today (and CLI commands or WebDriver interactions
tomorrow); Observation wraps the corresponding response.  The Drone converts
between the two layers so the rest of the system stays surface-agnostic.
Adapters implement both ``send`` (HTTP shorthand) and ``execute``
(Action/Observation) so existing callers keep working.

**Why LLM providers are configurable per-role?** The Attacker and Inspector
can use different providers (e.g., GPT-4 vs Claude) so users can mix strengths
or reduce cost. The same knob also supports the single-provider case: an
agentic-loop consumer running entirely inside one auth context (one
subscription, one MCP surface) sets both roles to the same provider. The
per-role configurability is the primitive; cross-provider and single-provider
are both first-class configurations of it.

**Why Arsenals?** Individual weapons test one property at a time, which is the
right granularity for authoring and debugging. But CI pipelines and agentic
loops need to select an entire class of attacks — authorization, input
validation, OWASP top-10 — as a single unit. An Arsenal is a named collection
of Weapons that can be versioned, shared, and loaded with one flag
(``--arsenal``). Without arsenals, users would need to list every weapon file
on the command line or maintain a wrapper script.  The CLI falls back to
``--weapon`` for individual weapon files when no arsenal is specified.

## Artifact directory layout

The CLI writes every machine-readable run artifact under a single root
directory, controlled by ``--artifact-dir`` (default ``.gauntlet/artifacts``).
An orchestrator invoking Gauntlet as a subprocess can point this flag at a
scratch directory and then read everything back from known locations without
parsing stdout.

```
{artifact_dir}/
├── plans/
│   └── {weapon_id}/
│       └── {plan_name}.yaml          # PlanStore — reused across runs
├── findings/
│   └── {weapon_id}/
│       └── {issue}.yaml              # FindingsStore — one file per issue
└── runs/
    ├── {weapon_slug}__{target_slug}.{yaml,json}   # one file per (weapon, target)
    └── latest.{yaml,json}            # always the most recent run
```

Rules:

- Paths are stable. A consumer can glob ``{artifact_dir}/runs/*.yaml`` (or
  ``*.json`` when ``--format json`` is set) to discover all runs from a single
  invocation.
- ``{weapon_slug}`` is the weapon's ``id`` (already snake_case) or
  ``no_weapon`` when no weapon was configured. ``{target_slug}`` is a lowercase
  underscore slug of ``target.title`` or ``no_target``.
- ``runs/latest.{ext}`` is overwritten on every run so a consumer that only
  cares about the last result has a single fixed path to read.
- The ``plans/`` and ``findings/`` trees accumulate across invocations and are
  indexed by weapon id; they are the persistence layer that ``PlanStore`` and
  ``FindingsStore`` read on startup.
- The extension of files in ``runs/`` matches ``--format`` (``yaml`` or
  ``json``). ``plans/`` and ``findings/`` are always YAML.
