from __future__ import annotations

import json
import threading
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
)
from gauntlet.runs import SCHEMA_VERSION
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


def _spec(name: str = "baseline") -> IterationSpec:
    return IterationSpec(index=1, name=name, goal=name)


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
    a = store.start_run(["trial_a"])
    b = store.start_run(["trial_a"])
    assert a != b
    assert (tmp_path / a / "manifest.json").exists()
    assert (tmp_path / a / "trial_a").is_dir()


def test_record_and_read_iteration_round_trip(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["trial_a"])
    record = _make_iteration(_spec())

    store.record_iteration(run_id, "trial_a", record)
    store.record_iteration(run_id, "trial_a", record)

    records = store.read_iteration_records(run_id, "trial_a")
    assert len(records) == 2
    assert records[0].plans[0].name == _AUTHZ_PLAN.name
    assert records[0].execution_results[0].plan_name == _AUTHZ_PLAN.name


def test_read_iteration_records_empty_when_no_writes(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["trial_a"])
    assert store.read_iteration_records(run_id, "trial_a") == []


def test_record_iteration_rejects_findings_with_violated_blocker(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["trial_a"])
    leaky_finding = Finding(
        issue="leak",
        severity="medium",
        confidence=0.8,
        rationale="leaked blocker text",
        violated_blocker="A PATCH by a non-owner is rejected with 403",
    )
    record = IterationRecord(
        spec=_spec(),
        plans=[],
        execution_results=[],
        findings=[leaky_finding],
    )
    with pytest.raises(ValueError, match="violated_blocker"):
        store.record_iteration(run_id, "trial_a", record)


def test_record_holdout_result_round_trip(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["trial_a"])
    execution = make_execution_result(plan_name=_AUTHZ_PLAN.name)
    holdout = HoldoutResult(
        trial_id="trial_a",
        blocker_index=0,
        blocker="A PATCH by a non-owner is rejected with 403",
        execution_result=execution,
    )
    store.record_holdout_result(run_id, "trial_a", holdout)
    results = store.read_holdout_results(run_id, "trial_a")
    assert len(results) == 1
    assert results[0].trial_id == "trial_a"
    assert results[0].execution_result.satisfaction_score == 0.0


def test_record_holdout_result_mismatched_trial_id_raises(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["trial_a"])
    execution = make_execution_result(plan_name=_AUTHZ_PLAN.name)
    holdout = HoldoutResult(trial_id="trial_b", execution_result=execution)
    with pytest.raises(ValueError, match="does not match"):
        store.record_holdout_result(run_id, "trial_a", holdout)


def test_runstore_rejects_invalid_trial_ids(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["trial_a"])
    record = _make_iteration(_spec())
    for bad in ["", "../escape", "a/b", ".", ".."]:
        with pytest.raises(ValueError):
            store.record_iteration(run_id, bad, record)


def test_runstore_rejects_invalid_run_id(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    record = _make_iteration(_spec())
    with pytest.raises(ValueError):
        store.record_iteration("../escape", "trial_a", record)


def test_list_trial_ids_returns_manifest_entries(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["trial_a", "trial_b"])
    assert store.list_trial_ids(run_id) == ["trial_a", "trial_b"]


def test_list_trial_ids_unknown_run_raises(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    with pytest.raises(ValueError, match="No run"):
        store.list_trial_ids("nonexistent")


# ---------------------------------------------------------------------------
# MCP tool surface — exercised against the default ``.gauntlet/runs`` path
# in a chdir'd tmp_path, the way the host invokes them.
# ---------------------------------------------------------------------------


def test_start_run_tool_returns_run_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    out = start_run(trial_ids=["trial_a"])
    assert "run_id" in out
    assert (tmp_path / ".gauntlet" / "runs" / out["run_id"] / "manifest.json").exists()


def test_iteration_buffer_tools_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    out = start_run(trial_ids=["trial_a"])
    run_id = out["run_id"]
    record = _make_iteration(_spec())

    record_iteration(run_id=run_id, trial_id="trial_a", iteration_record=record)
    records = read_iteration_records(run_id=run_id, trial_id="trial_a")
    assert len(records) == 1
    assert records[0].plans[0].name == _AUTHZ_PLAN.name


def test_holdout_buffer_tools_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    out = start_run(trial_ids=["trial_a"])
    run_id = out["run_id"]
    execution = make_execution_result(plan_name=_AUTHZ_PLAN.name)
    holdout = HoldoutResult(trial_id="trial_a", execution_result=execution)

    record_holdout_result(run_id=run_id, trial_id="trial_a", holdout_result=holdout)
    results = read_holdout_results(run_id=run_id, trial_id="trial_a")
    assert len(results) == 1
    assert results[0].execution_result.plan_name == _AUTHZ_PLAN.name


def test_assemble_run_report_buffer_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    out = start_run(trial_ids=["trial_a"])
    run_id = out["run_id"]
    record = _make_iteration(_spec())
    record_iteration(run_id=run_id, trial_id="trial_a", iteration_record=record)
    execution = make_execution_result(plan_name=_AUTHZ_PLAN.name)
    record_holdout_result(
        run_id=run_id,
        trial_id="trial_a",
        holdout_result=HoldoutResult(trial_id="trial_a", execution_result=execution),
    )

    out = assemble_run_report(
        run_id=run_id,
        trial_id="trial_a",
        clearance_threshold=0.9,
    )
    assert out["clearance"] is not None
    assert out["clearance"]["recommendation"] == "block"


# ---------------------------------------------------------------------------
# Buffer robustness: corrupt-line tolerance, schema_version, flock concurrency
# ---------------------------------------------------------------------------


def test_manifest_includes_schema_version(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["trial_a"])
    manifest = json.loads((tmp_path / run_id / "manifest.json").read_text())
    assert manifest["schema_version"] == SCHEMA_VERSION


def test_list_trial_ids_tolerates_missing_schema_version(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["trial_a"])
    # Simulate a buffer written before schema_version was introduced.
    manifest_path = tmp_path / run_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["schema_version"]
    manifest_path.write_text(json.dumps(manifest))
    assert store.list_trial_ids(run_id) == ["trial_a"]


def test_read_iteration_records_skips_corrupt_lines(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["trial_a"])
    good = _make_iteration(_spec())
    store.record_iteration(run_id, "trial_a", good)

    # Append a corrupt line directly to the JSONL file.
    iterations_path = tmp_path / run_id / "trial_a" / "iterations.jsonl"
    with iterations_path.open("a") as fh:
        fh.write("{this is not valid json\n")

    # And a structurally valid JSON line that fails schema validation.
    with iterations_path.open("a") as fh:
        fh.write(json.dumps({"unexpected": "shape"}) + "\n")

    records = store.read_iteration_records(run_id, "trial_a")
    assert len(records) == 1
    assert records[0].plans[0].name == _AUTHZ_PLAN.name

    counts = store.corrupt_record_counts()
    assert counts[(run_id, "trial_a")] == 2


def test_read_holdout_results_skips_corrupt_lines(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["trial_a"])
    execution = make_execution_result(plan_name=_AUTHZ_PLAN.name)
    store.record_holdout_result(
        run_id,
        "trial_a",
        HoldoutResult(trial_id="trial_a", execution_result=execution),
    )

    holdouts_path = tmp_path / run_id / "trial_a" / "holdouts.jsonl"
    with holdouts_path.open("a") as fh:
        fh.write("not json at all\n")

    results = store.read_holdout_results(run_id, "trial_a")
    assert len(results) == 1
    counts = store.corrupt_record_counts()
    assert counts[(run_id, "trial_a")] == 1


def test_corrupt_record_counts_is_readonly_snapshot(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["trial_a"])
    iterations_path = tmp_path / run_id / "trial_a" / "iterations.jsonl"
    iterations_path.parent.mkdir(parents=True, exist_ok=True)
    iterations_path.write_text("garbage\n")
    store.read_iteration_records(run_id, "trial_a")

    snapshot = store.corrupt_record_counts()
    snapshot[("foo", "bar")] = 99
    # Mutating the snapshot must not affect future reads.
    assert ("foo", "bar") not in store.corrupt_record_counts()


def test_concurrent_appends_preserve_line_integrity(tmp_path: Path) -> None:
    """With flock in place, concurrent writers must not interleave bytes.

    This is a best-effort check: run enough threads with enough payload that
    any bug would likely produce at least one malformed line.
    """
    store = RunStore(tmp_path)
    run_id = store.start_run(["trial_a"])
    record = _make_iteration(_spec())
    # Cached JSON — the exact serialized form every thread will append.
    payload = record.model_dump_json()
    writes_per_thread = 20
    num_threads = 8

    def worker() -> None:
        for _ in range(writes_per_thread):
            store.record_iteration(run_id, "trial_a", record)

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    iterations_path = tmp_path / run_id / "trial_a" / "iterations.jsonl"
    lines = iterations_path.read_text().splitlines()
    assert len(lines) == writes_per_thread * num_threads
    for line in lines:
        # Every line should be a complete serialized IterationRecord.
        assert line == payload
