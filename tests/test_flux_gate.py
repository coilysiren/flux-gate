from flux_gate import (
    DemoAdversary,
    DemoHoldoutEvaluator,
    DemoNaturalLanguageEvaluator,
    DemoNaturalLanguageHoldoutEvaluator,
    DemoOperator,
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
    assert run.risk_report.merge_gate is not None
    assert run.risk_report.merge_gate.passed is False
    assert run.risk_report.merge_gate.recommendation == "block"
    assert run.risk_report.merge_gate.holdout_pass_rate == 0.0
