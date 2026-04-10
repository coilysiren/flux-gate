from flux_gate import (
    DemoAdversary,
    DemoHoldoutEvaluator,
    DemoNaturalLanguageEvaluator,
    DemoNaturalLanguageHoldoutEvaluator,
    DemoOperator,
    DemoSpecAssessor,
    DeterministicLocalExecutor,
    FeatureSpec,
    FluxGateRunner,
    InMemoryTaskAPI,
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
    """NaturalLanguageScenario path: criteria are parsed from the spec at runtime."""
    spec = FeatureSpec(
        title="Users cannot modify each other's tasks",
        description="The task API must enforce resource ownership.",
        acceptance_criteria=["A PATCH by a non-owner is rejected with 403"],
        target_endpoints=["PATCH /tasks/{id}"],
    )

    runner = FluxGateRunner(
        executor=DeterministicLocalExecutor(InMemoryTaskAPI()),
        operator=DemoOperator(),
        adversary=DemoAdversary(),
        nl_holdout_evaluator=DemoNaturalLanguageHoldoutEvaluator(),
        nl_evaluator=DemoNaturalLanguageEvaluator(),
        feature_spec=spec,
        gate_threshold=0.90,
    )

    run = runner.run()

    assert len(run.holdout_results) == 1
    assert run.holdout_results[0].assertions[0].kind == "verdict"
    assert run.holdout_results[0].satisfaction_score == 0.0
    assert run.risk_report.merge_gate is not None
    assert run.risk_report.merge_gate.recommendation == "block"


def test_holdout_gate_blocks_failing_api() -> None:
    spec = FeatureSpec(
        title="Users cannot modify each other's tasks",
        description="The task API must enforce resource ownership.",
        acceptance_criteria=["A PATCH by a non-owner is rejected with 403"],
        target_endpoints=["PATCH /tasks/{id}"],
    )

    runner = FluxGateRunner(
        executor=DeterministicLocalExecutor(InMemoryTaskAPI()),
        operator=DemoOperator(),
        adversary=DemoAdversary(),
        holdout_evaluator=DemoHoldoutEvaluator(),
        feature_spec=spec,
        gate_threshold=0.90,
    )

    run = runner.run()

    assert run.feature_spec == spec
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


def test_preflight_blocks_vague_spec() -> None:
    """DemoSpecAssessor rejects a spec whose criteria are too short."""
    vague_spec = FeatureSpec(
        title="Make it secure",
        description="It should be secure.",
        acceptance_criteria=["secure", "no bugs"],  # both under 20 chars
        target_endpoints=[],
    )

    runner = FluxGateRunner(
        executor=DeterministicLocalExecutor(InMemoryTaskAPI()),
        operator=DemoOperator(),
        adversary=DemoAdversary(),
        spec_assessor=DemoSpecAssessor(),
        feature_spec=vague_spec,
    )

    run = runner.run()

    assert run.iterations == []
    assert run.spec_assessment is not None
    assert run.spec_assessment.proceed is False
    assert run.spec_assessment.quality_score < 0.5
    assert run.risk_report.merge_gate is not None
    assert run.risk_report.merge_gate.recommendation == "block"


def test_preflight_passes_good_spec() -> None:
    """DemoSpecAssessor accepts a well-formed spec and allows the loop to run."""
    good_spec = FeatureSpec(
        title="Users cannot modify each other's tasks",
        description="The task API must enforce resource ownership.",
        acceptance_criteria=["A PATCH by a non-owner is rejected with 403"],
        target_endpoints=["PATCH /tasks/{id}"],
    )

    runner = FluxGateRunner(
        executor=DeterministicLocalExecutor(InMemoryTaskAPI()),
        operator=DemoOperator(),
        adversary=DemoAdversary(),
        spec_assessor=DemoSpecAssessor(),
        feature_spec=good_spec,
    )

    run = runner.run()

    assert run.spec_assessment is not None
    assert run.spec_assessment.proceed is True
    assert run.spec_assessment.quality_score >= 0.5
    assert len(run.iterations) == 4  # full loop ran
