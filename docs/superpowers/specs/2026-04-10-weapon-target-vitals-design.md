# Weapon / Target / Vitals Framework

**Date:** 2026-04-10

## Summary

Introduce `Target` as a first-class model, rename `must_hold` to `fatals` on `Weapon`, and wire both into the runner and CLI. A weapon is a reusable, system-agnostic attack strategy. A target parameterizes that weapon against a specific API surface. Together they drive the vitals evaluation.

## Data model changes

### New: `Target`

```python
class Target(FluxGateModel):
    title: str
    endpoints: list[str]
```

`Target` is intentionally minimal — it is expected to grow with additional API configuration fields over time. One target per YAML file.

### Modified: `Weapon`

- Remove `target_endpoints: list[str]` — moves to `Target.endpoints`
- Rename `must_hold: list[str]` → `fatals: list[str]`

`fatals` are the properties the system must never violate. They are passed only to the holdout vitals, never to the Operator, preserving the train/test split.

### Modified: `IterationSpec`

Add `target: Target | None = None` alongside the existing `weapon` field.

### Modified: `FluxGateRun`

Add `target: Target | None = None` to record which target was used in the run.

## Protocol changes

### `WeaponAssessor.assess(weapon, target)`

Signature changes from `assess(weapon)` to `assess(weapon, target)`. Both are required for a meaningful quality check — endpoint coverage now lives on the target.

### `HoldoutVitals` / `NaturalLanguageHoldoutVitals`

No change. These protocols use `weapon.fatals` and `weapon.description`, neither of which moves to `Target`.

### `DemoWeaponAssessor`

Updated to check `target.endpoints` (was `weapon.target_endpoints`) and `weapon.fatals` (was `weapon.must_hold`).

## Runner changes

`FluxGateRunner.__init__` gains `target: Target | None = None`.

- Injects target into each `IterationSpec` alongside weapon
- Passes both to `assessor.assess(weapon, target)`
- Records `target` in the returned `FluxGateRun`

## CLI changes

New `--target FILE_OR_DIR` option (default `.flux_gate/targets/`), parallel to `--weapon`.

The run loop becomes `weapon × target` — one `FluxGateRunner` per pair. If no targets are loaded, each weapon runs once with `target=None`, preserving existing behavior:

```python
for weapon in weapons:
    for target in targets or [None]:
        runner = FluxGateRunner(weapon=weapon, target=target, ...)
```

Project config layout:

```
.flux_gate/
├── weapons/
│   └── broken_auth.yaml        # reusable attack strategy
└── targets/
    └── login_endpoint.yaml     # concrete API surface
```

Example weapon YAML (after rename):

```yaml
# .flux_gate/weapons/broken_auth.yaml
title: Broken authentication
description: >
  The auth system must reject unauthenticated and cross-user requests.
fatals:
  - A POST to /auth/login with invalid credentials returns 401
  - A request with a token belonging to user A cannot access user B's resources
```

Example target YAML:

```yaml
# .flux_gate/targets/login_endpoint.yaml
title: Login endpoint
endpoints:
  - POST /auth/login
  - POST /auth/refresh
```

## Rename: `must_hold` → `fatals`

All references updated:

- `Weapon.fatals` (model)
- `DemoNaturalLanguageHoldoutVitals` — iterates `weapon.fatals`
- `DemoWeaponAssessor` — checks `weapon.fatals`
- Tests — `Weapon(fatals=[...], ...)`
- Docs — YAML examples in README and usage.md

## Files affected

| File | Change |
|---|---|
| `flux_gate/models.py` | Add `Target`; modify `Weapon`, `IterationSpec`, `FluxGateRun` |
| `flux_gate/roles.py` | Update `WeaponAssessor` protocol; update `Demo*` implementations |
| `flux_gate/loop.py` | Add `target` param; inject into specs; pass to assessor; record in run |
| `flux_gate/cli.py` | Add `--target` flag; `_load_targets()`; `weapon × target` loop |
| `flux_gate/__init__.py` | Export `Target` |
| `tests/test_flux_gate.py` | Update `Weapon` construction; add weapon×target test |
| `README.md` | Update YAML examples; add `--target` to CLI reference |
| `docs/usage.md` | Update weapon YAML examples; add target section |
| `docs/architecture.md` | Update module map and data flow |
