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


def test_runner_produces_four_iteration_report() -> None:
    runner = FluxGateRunner(
        executor=DeterministicLocalExecutor(InMemoryTaskAPI()),
        operator=DemoOperator(),
        adversary=DemoAdversary(),
    )

    run = runner.run()

    assert len(run.iterations) == 4
    assert run.risk_report.risk_level == "critical"
    assert "unauthorized_cross_user_modification" in run.risk_report.confirmed_failures
    assert "PATCH /tasks/1" in run.risk_report.coverage
    assert run.risk_report.merge_gate is None  # no holdout evaluator provided


def test_demo_scenario_surfaces_authz_failure() -> None:
    runner = FluxGateRunner(
        executor=DeterministicLocalExecutor(InMemoryTaskAPI()),
        operator=DemoOperator(),
        adversary=DemoAdversary(),
    )

    first_iteration = runner.run().iterations[0]
    result = first_iteration.execution_results[0]

    assert result.steps[1].response.status_code == 200
    assert result.assertions[0].passed is False
    assert result.assertions[1].passed is False
    assert result.satisfaction_score == 0.0  # 0/2 assertions passed


def test_nl_holdout_gate_blocks_failing_api() -> None:
    """NaturalLanguageScenario path: must_hold properties are parsed from the weapon."""
    inv = Weapon(
        title="Users cannot modify each other's tasks",
        description="The task API must enforce resource ownership.",
        blockers=["A PATCH by a non-owner is rejected with 403"],
    )

    runner = FluxGateRunner(
        executor=DeterministicLocalExecutor(InMemoryTaskAPI()),
        operator=DemoOperator(),
        adversary=DemoAdversary(),
        nl_holdout_vitals=DemoNaturalLanguageHoldoutVitals(),
        nl_vitals=DemoNaturalLanguageVitals(),
        weapon=inv,
        gate_threshold=0.90,
    )

    run = runner.run()

    assert len(run.holdout_results) == 1
    assert run.holdout_results[0].assertions[0].kind == "verdict"
    assert run.holdout_results[0].satisfaction_score == 0.0
    assert run.risk_report.merge_gate is not None
    assert run.risk_report.merge_gate.recommendation == "block"


def test_holdout_gate_blocks_failing_api() -> None:
    inv = Weapon(
        title="Users cannot modify each other's tasks",
        description="The task API must enforce resource ownership.",
        blockers=["A PATCH by a non-owner is rejected with 403"],
    )

    runner = FluxGateRunner(
        executor=DeterministicLocalExecutor(InMemoryTaskAPI()),
        operator=DemoOperator(),
        adversary=DemoAdversary(),
        holdout_vitals=DemoHoldoutVitals(),
        weapon=inv,
        gate_threshold=0.90,
    )

    run = runner.run()

    assert run.weapon == inv
    assert len(run.holdout_results) == 1
    assert run.holdout_results[0].satisfaction_score == 0.0  # 0/2 assertions passed
    assert run.risk_report.merge_gate is not None
    assert run.risk_report.merge_gate.passed is False
    assert run.risk_report.merge_gate.recommendation == "block"
    assert run.risk_report.merge_gate.holdout_satisfaction_score == 0.0


def test_fail_fast_tier_stops_early_on_critical_finding() -> None:
    """fail_fast_tier=0 stops after the first iteration when a critical finding appears."""
    runner = FluxGateRunner(
        executor=DeterministicLocalExecutor(InMemoryTaskAPI()),
        operator=DemoOperator(),
        adversary=DemoAdversary(),
        fail_fast_tier=0,
    )

    run = runner.run()

    # The demo adversary finds a critical issue in iteration 1 (tier 0),
    # so the loop should stop there rather than running all four iterations.
    assert len(run.iterations) == 1
    assert run.iterations[0].spec.tier == 0
    assert any(f.severity == "critical" for f in run.iterations[0].findings)


def test_preflight_blocks_vague_weapon() -> None:
    """DemoWeaponAssessor rejects a weapon whose must_hold properties are too short."""
    vague = Weapon(
        title="Make it secure",
        description="It should be secure.",
        blockers=["secure", "no bugs"],  # both under 20 chars
    )

    runner = FluxGateRunner(
        executor=DeterministicLocalExecutor(InMemoryTaskAPI()),
        operator=DemoOperator(),
        adversary=DemoAdversary(),
        assessor=DemoWeaponAssessor(),
        weapon=vague,
    )

    run = runner.run()

    assert run.iterations == []
    assert run.weapon_assessment is not None
    assert run.weapon_assessment.proceed is False
    assert run.weapon_assessment.quality_score < 0.5
    assert run.risk_report.merge_gate is not None
    assert run.risk_report.merge_gate.recommendation == "block"


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
    assert len(run.iterations) == 4  # full loop ran


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
