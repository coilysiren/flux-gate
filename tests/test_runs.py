from __future__ import annotations

from pathlib import Path

import pytest

from gauntlet import (
    Assertion,
    Finding,
    HoldoutResult,
    HttpRequest,
    IterationRecord,
    IterationSpec,
    Plan,
    PlanStep,
    RunStore,
    build_default_iteration_specs,
)
from gauntlet.server import (
    assemble_run_report,
    read_holdout_results,
    read_iteration_records,
    record_holdout_result,
    record_iteration,
    start_run,
)

from ._factories import make_execution_result

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


_AUTHZ_PLAN = Plan(
    name="user_cannot_modify_other_users_task",
    category="authz",
    goal="cross-user modification should be rejected",
    steps=[
        PlanStep(
            user="userA",
            request=HttpRequest(method="POST", path="/tasks", body={"title": "private task"}),
        ),
        PlanStep(
            user="userB",
            request=HttpRequest(method="PATCH", path="/tasks/{task_id}", body={"completed": True}),
        ),
        PlanStep(user="userA", request=HttpRequest(method="GET", path="/tasks/{task_id}")),
    ],
    assertions=[
        Assertion(
            name="unauthorized_patch_blocked",
            kind="status_code",
            expected=403,
            step_index=2,
        ),
    ],
)


def _make_iteration(spec: IterationSpec) -> IterationRecord:
    execution = make_execution_result(plan_name=_AUTHZ_PLAN.name)
    return IterationRecord(
        spec=spec,
        plans=[_AUTHZ_PLAN],
        execution_results=[execution],
        findings=[],
    )


# ---------------------------------------------------------------------------
# RunStore behaviour
# ---------------------------------------------------------------------------


def test_start_run_returns_unique_ids(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    a = store.start_run(["weapon_a"])
    b = store.start_run(["weapon_a"])
    assert a != b
    assert (tmp_path / a / "manifest.json").exists()
    assert (tmp_path / a / "weapon_a").is_dir()


def test_record_and_read_iteration_round_trip(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["weapon_a"])
    record = _make_iteration(build_default_iteration_specs()[0])

    store.record_iteration(run_id, "weapon_a", record)
    store.record_iteration(run_id, "weapon_a", record)

    records = store.read_iteration_records(run_id, "weapon_a")
    assert len(records) == 2
    assert records[0].plans[0].name == _AUTHZ_PLAN.name
    assert records[0].execution_results[0].plan_name == _AUTHZ_PLAN.name


def test_read_iteration_records_empty_when_no_writes(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["weapon_a"])
    assert store.read_iteration_records(run_id, "weapon_a") == []


def test_record_iteration_rejects_findings_with_violated_blocker(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["weapon_a"])
    leaky_finding = Finding(
        issue="leak",
        severity="medium",
        confidence=0.8,
        rationale="leaked blocker text",
        violated_blocker="A PATCH by a non-owner is rejected with 403",
    )
    record = IterationRecord(
        spec=build_default_iteration_specs()[0],
        plans=[],
        execution_results=[],
        findings=[leaky_finding],
    )
    with pytest.raises(ValueError, match="violated_blocker"):
        store.record_iteration(run_id, "weapon_a", record)


def test_record_holdout_result_round_trip(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["weapon_a"])
    execution = make_execution_result(plan_name=_AUTHZ_PLAN.name)
    holdout = HoldoutResult(
        weapon_id="weapon_a",
        blocker_index=0,
        blocker="A PATCH by a non-owner is rejected with 403",
        execution_result=execution,
    )
    store.record_holdout_result(run_id, "weapon_a", holdout)
    results = store.read_holdout_results(run_id, "weapon_a")
    assert len(results) == 1
    assert results[0].weapon_id == "weapon_a"
    assert results[0].execution_result.satisfaction_score == 0.0


def test_record_holdout_result_mismatched_weapon_id_raises(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["weapon_a"])
    execution = make_execution_result(plan_name=_AUTHZ_PLAN.name)
    holdout = HoldoutResult(weapon_id="weapon_b", execution_result=execution)
    with pytest.raises(ValueError, match="does not match"):
        store.record_holdout_result(run_id, "weapon_a", holdout)


def test_runstore_rejects_invalid_weapon_ids(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["weapon_a"])
    record = _make_iteration(build_default_iteration_specs()[0])
    for bad in ["", "../escape", "a/b", ".", ".."]:
        with pytest.raises(ValueError):
            store.record_iteration(run_id, bad, record)


def test_runstore_rejects_invalid_run_id(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    record = _make_iteration(build_default_iteration_specs()[0])
    with pytest.raises(ValueError):
        store.record_iteration("../escape", "weapon_a", record)


def test_list_weapon_ids_returns_manifest_entries(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["weapon_a", "weapon_b"])
    assert store.list_weapon_ids(run_id) == ["weapon_a", "weapon_b"]


def test_list_weapon_ids_unknown_run_raises(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    with pytest.raises(ValueError, match="No run"):
        store.list_weapon_ids("nonexistent")


# ---------------------------------------------------------------------------
# MCP tool surface
# ---------------------------------------------------------------------------


def test_start_run_tool_returns_run_id(tmp_path: Path) -> None:
    out = start_run(weapon_ids=["weapon_a"], runs_path=str(tmp_path))
    assert "run_id" in out
    assert (tmp_path / out["run_id"] / "manifest.json").exists()


def test_iteration_buffer_tools_round_trip(tmp_path: Path) -> None:
    out = start_run(weapon_ids=["weapon_a"], runs_path=str(tmp_path))
    run_id = out["run_id"]
    record = _make_iteration(build_default_iteration_specs()[0])

    record_iteration(
        run_id=run_id,
        weapon_id="weapon_a",
        iteration_record=record,
        runs_path=str(tmp_path),
    )
    records = read_iteration_records(run_id=run_id, weapon_id="weapon_a", runs_path=str(tmp_path))
    assert len(records) == 1
    assert records[0].plans[0].name == _AUTHZ_PLAN.name


def test_holdout_buffer_tools_round_trip(tmp_path: Path) -> None:
    out = start_run(weapon_ids=["weapon_a"], runs_path=str(tmp_path))
    run_id = out["run_id"]
    execution = make_execution_result(plan_name=_AUTHZ_PLAN.name)
    holdout = HoldoutResult(weapon_id="weapon_a", execution_result=execution)

    record_holdout_result(
        run_id=run_id,
        weapon_id="weapon_a",
        holdout_result=holdout,
        runs_path=str(tmp_path),
    )
    results = read_holdout_results(run_id=run_id, weapon_id="weapon_a", runs_path=str(tmp_path))
    assert len(results) == 1
    assert results[0].execution_result.plan_name == _AUTHZ_PLAN.name


def test_assemble_run_report_buffer_mode(tmp_path: Path) -> None:
    out = start_run(weapon_ids=["weapon_a"], runs_path=str(tmp_path))
    run_id = out["run_id"]
    record = _make_iteration(build_default_iteration_specs()[0])
    record_iteration(
        run_id=run_id,
        weapon_id="weapon_a",
        iteration_record=record,
        runs_path=str(tmp_path),
    )
    execution = make_execution_result(plan_name=_AUTHZ_PLAN.name)
    record_holdout_result(
        run_id=run_id,
        weapon_id="weapon_a",
        holdout_result=HoldoutResult(weapon_id="weapon_a", execution_result=execution),
        runs_path=str(tmp_path),
    )

    out = assemble_run_report(
        run_id=run_id,
        weapon_id="weapon_a",
        runs_path=str(tmp_path),
        clearance_threshold=0.9,
    )
    assert out["clearance"] is not None
    assert out["clearance"]["recommendation"] == "block"


def test_assemble_run_report_requires_arguments() -> None:
    with pytest.raises(ValueError, match="requires either"):
        assemble_run_report()
