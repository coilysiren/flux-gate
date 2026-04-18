#!/usr/bin/env -S uv run python
"""Run every weapon in the arsenal against the demo API.

No LLM keys required — uses the deterministic Demo* classes.
The demo API (InMemoryHttpApi) has three seeded flaws:
  1. PATCH without ownership check (any user can modify any task)
  2. POST accepts invalid/missing title fields without validation
  3. GET /tasks leaks all users' tasks regardless of requester

Usage:
    ./scripts/run_arsenal.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from gauntlet import (
    DemoAttacker,
    DemoHoldoutVitals,
    DemoInspector,
    DemoWeaponAssessor,
    Drone,
    GauntletRunner,
    InMemoryHttpApi,
    Target,
    Weapon,
)


def main() -> int:
    weapons_dir = Path(".gauntlet/weapons")
    targets_dir = Path(".gauntlet/targets")

    # Load all weapons
    weapons: list[Weapon] = []
    for path in sorted(weapons_dir.glob("*.yaml")):
        weapons.append(Weapon(**yaml.safe_load(path.read_text())))

    # Load all targets
    targets: list[Target] = []
    for path in sorted(targets_dir.glob("*.yaml")):
        targets.append(Target(**yaml.safe_load(path.read_text())))

    print(f"Arsenal: {len(weapons)} weapons, {len(targets)} targets")
    print(f"Weapons: {', '.join(w.id or w.title for w in weapons)}")
    print()

    blocked = False
    for weapon in weapons:
        for target in targets:
            label = weapon.id or weapon.title
            print(f"{'=' * 60}")
            print(f"Weapon: {label}")
            print(f"Target: {target.title}")
            print(f"{'=' * 60}")

            runner = GauntletRunner(
                executor=Drone(InMemoryHttpApi()),
                attacker=DemoAttacker(),
                inspector=DemoInspector(),
                holdout_vitals=DemoHoldoutVitals(),
                assessor=DemoWeaponAssessor(),
                weapon=weapon,
                target=target,
                clearance_threshold=0.90,
                fail_fast_tier=0,
            )

            run = runner.run()

            # Preflight result
            if run.weapon_assessment and not run.weapon_assessment.proceed:
                score = run.weapon_assessment.quality_score
                print(f"  SKIPPED — preflight rejected (score: {score:.0%})")
                for issue in run.weapon_assessment.issues:
                    print(f"    - {issue}")
                print()
                continue

            # Iteration summary
            total_findings = sum(len(r.findings) for r in run.iterations)
            total_plans = sum(len(r.plans) for r in run.iterations)
            n = len(run.iterations)
            print(f"  Iterations: {n} | Plans: {total_plans} | Findings: {total_findings}")

            # Findings
            for record in run.iterations:
                for finding in record.findings:
                    icon = "\u274c" if finding.severity in ("critical", "high") else "\u26a0\ufe0f"
                    print(f"  {icon} {finding.issue} [{finding.severity}]")

            # Holdout / clearance
            if run.clearance:
                rec = run.clearance.recommendation.upper()
                score = run.clearance.holdout_satisfaction_score
                print(f"  Clearance: {rec} (holdout score: {score:.0%})")
                if run.clearance.recommendation == "block":
                    blocked = True

            print()

    # Summary
    print("=" * 60)
    if blocked:
        print("RESULT: At least one weapon produced a BLOCK clearance.")
        print("The demo API has known flaws — this is expected.")
    else:
        print("RESULT: All weapons passed or were skipped.")
    print("=" * 60)

    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
