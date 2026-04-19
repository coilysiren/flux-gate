"""Cross-run findings store + recurring_failures MCP tool."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from gauntlet import (
    Finding,
    HoldoutResult,
    HttpRequest,
    IterationRecord,
    IterationSpec,
    Plan,
    PlanStep,
)
from gauntlet._findings_store import FindingsStore
from gauntlet.models import ReplayBundle, ReplayStep
from gauntlet.server import (
    assemble_run_report,
    record_holdout_result,
    record_iteration,
    recurring_failures,
    start_run,
)

from ._factories import make_execution_result


def _spec() -> IterationSpec:
    return IterationSpec(index=1, name="baseline", goal="baseline")


def _finding(issue: str) -> Finding:
    return Finding(
        issue=issue,
        severity="high",
        confidence=0.9,
        rationale="test",
        replay_bundle=ReplayBundle(
            steps=[
                ReplayStep(
                    user="userA",
                    request=HttpRequest(method="POST", path="/tasks", body={"title": "t"}),
                )
            ]
        ),
    )


# ---------------------------------------------------------------------------
# FindingsStore unit behavior
# ---------------------------------------------------------------------------


def test_record_then_recurring_surfaces_repeated_issues(tmp_path: Path) -> None:
    store = FindingsStore(tmp_path)
    for run_id in ("run_1", "run_2", "run_3"):
        store.record("weapon_a", run_id, _finding("cross_user_patch_allowed"))
        store.record("weapon_a", run_id, _finding(f"unique_to_{run_id}"))

    recurring = store.recurring("weapon_a", lookback=5)
    assert len(recurring) == 1
    [entry] = recurring
    assert entry["issue"] == "cross_user_patch_allowed"
    assert entry["occurrences"] == 3
    assert entry["run_ids"] == ["run_1", "run_2", "run_3"]


def test_recurring_window_respects_lookback(tmp_path: Path) -> None:
    store = FindingsStore(tmp_path)
    for run_id in ("r1", "r2", "r3", "r4"):
        store.record("weapon_a", run_id, _finding("flaky_issue"))
    # With lookback=2, only the two most recent runs are counted.
    recurring = store.recurring("weapon_a", lookback=2)
    assert len(recurring) == 1
    assert recurring[0]["occurrences"] == 2
    assert recurring[0]["run_ids"] == ["r3", "r4"]


def test_recurring_returns_empty_when_no_issue_repeats(tmp_path: Path) -> None:
    store = FindingsStore(tmp_path)
    store.record("weapon_a", "run_1", _finding("one_off_issue"))
    store.record("weapon_a", "run_2", _finding("different_issue"))
    assert store.recurring("weapon_a", lookback=5) == []


def test_recurring_missing_file_returns_empty(tmp_path: Path) -> None:
    store = FindingsStore(tmp_path)
    assert store.recurring("weapon_never_seen", lookback=5) == []


def test_recurring_issue_per_run_is_deduped(tmp_path: Path) -> None:
    """Same issue twice within one run is one occurrence, not two."""
    store = FindingsStore(tmp_path)
    store.record("weapon_a", "run_1", _finding("issue_a"))
    store.record("weapon_a", "run_1", _finding("issue_a"))
    store.record("weapon_a", "run_2", _finding("issue_a"))
    recurring = store.recurring("weapon_a", lookback=5)
    assert recurring[0]["occurrences"] == 2
    assert recurring[0]["run_ids"] == ["run_1", "run_2"]


def test_clear_removes_weapon_file(tmp_path: Path) -> None:
    store = FindingsStore(tmp_path)
    store.record("weapon_a", "run_1", _finding("issue"))
    assert (tmp_path / "weapon_a.jsonl").exists()
    store.clear("weapon_a")
    assert not (tmp_path / "weapon_a.jsonl").exists()
    # Safe to call on a missing file.
    store.clear("weapon_a")


def test_record_skips_corrupt_lines_on_read(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = FindingsStore(tmp_path)
    store.record("weapon_a", "run_1", _finding("issue"))
    # Append a malformed JSON line and a valid one after.
    path = tmp_path / "weapon_a.jsonl"
    with path.open("a") as fh:
        fh.write("{not json\n")
        fh.write("null\n")  # valid JSON but not an object
    store.record("weapon_a", "run_2", _finding("issue"))

    with caplog.at_level(logging.WARNING, logger="gauntlet._findings_store"):
        recurring = store.recurring("weapon_a", lookback=5)

    # The corrupt + non-object lines were skipped; the two valid records show
    # up as a recurring issue across two distinct runs.
    assert recurring == [
        {"issue": "issue", "occurrences": 2, "run_ids": ["run_1", "run_2"]},
    ]
    corrupt_warnings = [r for r in caplog.records if "corrupt" in r.getMessage().lower()]
    assert corrupt_warnings


def test_record_rejects_invalid_weapon_ids(tmp_path: Path) -> None:
    store = FindingsStore(tmp_path)
    for bad in ("", "../escape", "a/b", ".", ".."):
        with pytest.raises(ValueError):
            store.record(bad, "run_1", _finding("x"))


def test_record_writes_schema_version_and_timestamp(tmp_path: Path) -> None:
    store = FindingsStore(tmp_path)
    store.record("weapon_a", "run_1", _finding("issue"))
    line = (tmp_path / "weapon_a.jsonl").read_text().strip()
    entry = json.loads(line)
    assert entry["schema_version"] == 1
    assert entry["run_id"] == "run_1"
    assert "timestamp" in entry and entry["timestamp"]
    assert entry["finding"]["issue"] == "issue"


def test_sorting_prefers_higher_occurrence_then_issue_name(tmp_path: Path) -> None:
    store = FindingsStore(tmp_path)
    # issue_b appears in 2 runs, issue_a appears in 3 runs.
    store.record("weapon_a", "run_1", _finding("issue_a"))
    store.record("weapon_a", "run_2", _finding("issue_a"))
    store.record("weapon_a", "run_3", _finding("issue_a"))
    store.record("weapon_a", "run_1", _finding("issue_b"))
    store.record("weapon_a", "run_2", _finding("issue_b"))
    recurring = store.recurring("weapon_a", lookback=5)
    assert [r["issue"] for r in recurring] == ["issue_a", "issue_b"]


# ---------------------------------------------------------------------------
# MCP integration via assemble_run_report + recurring_failures
# ---------------------------------------------------------------------------


_AUTHZ_PLAN = Plan(
    name="user_cannot_modify_other_users_task",
    category="authz",
    goal="cross-user modification should be rejected",
    steps=[
        PlanStep(
            user="userA",
            request=HttpRequest(method="POST", path="/tasks", body={"title": "t"}),
        )
    ],
    assertions=[],
)


def _record_one_run_with_finding(weapon_id: str, issue: str) -> None:
    out = start_run(weapon_ids=[weapon_id])
    run_id = out["run_id"]
    execution = make_execution_result(plan_name=_AUTHZ_PLAN.name)
    record_iteration(
        run_id=run_id,
        weapon_id=weapon_id,
        iteration_record=IterationRecord(
            spec=_spec(),
            plans=[_AUTHZ_PLAN],
            execution_results=[execution],
            findings=[_finding(issue)],
        ),
    )
    # Holdout has satisfaction_score=0.0 so clearance blocks; irrelevant here,
    # but we need at least one holdout so the report reads cleanly.
    record_holdout_result(
        run_id=run_id,
        weapon_id=weapon_id,
        holdout_result=HoldoutResult(weapon_id=weapon_id, execution_result=execution),
    )
    assemble_run_report(run_id=run_id, weapon_id=weapon_id)


def test_assemble_run_report_persists_findings_for_recurring_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _record_one_run_with_finding("weapon_a", "cross_user_patch_allowed")
    _record_one_run_with_finding("weapon_a", "cross_user_patch_allowed")

    recurring = recurring_failures(weapon_id="weapon_a", lookback=5)
    assert len(recurring) == 1
    entry = recurring[0]
    assert entry["issue"] == "cross_user_patch_allowed"
    assert entry["occurrences"] == 2


def test_assemble_run_report_does_not_persist_when_no_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    out = start_run(weapon_ids=["weapon_a"])
    run_id = out["run_id"]
    execution = make_execution_result(plan_name=_AUTHZ_PLAN.name)
    record_iteration(
        run_id=run_id,
        weapon_id="weapon_a",
        iteration_record=IterationRecord(
            spec=_spec(),
            plans=[_AUTHZ_PLAN],
            execution_results=[execution],
            findings=[],
        ),
    )
    record_holdout_result(
        run_id=run_id,
        weapon_id="weapon_a",
        holdout_result=HoldoutResult(weapon_id="weapon_a", execution_result=execution),
    )
    assemble_run_report(run_id=run_id, weapon_id="weapon_a")

    assert recurring_failures(weapon_id="weapon_a", lookback=5) == []
    # And no per-weapon findings file should have been created.
    assert not (tmp_path / ".gauntlet" / "findings" / "weapon_a.jsonl").exists()


def test_assemble_run_report_does_not_persist_anomalies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    out = start_run(weapon_ids=["weapon_a"])
    run_id = out["run_id"]
    execution = make_execution_result(plan_name=_AUTHZ_PLAN.name)
    anomaly = _finding("unexplained_500").model_copy(update={"is_anomaly": True})
    record_iteration(
        run_id=run_id,
        weapon_id="weapon_a",
        iteration_record=IterationRecord(
            spec=_spec(),
            plans=[_AUTHZ_PLAN],
            execution_results=[execution],
            findings=[anomaly],
        ),
    )
    record_holdout_result(
        run_id=run_id,
        weapon_id="weapon_a",
        holdout_result=HoldoutResult(weapon_id="weapon_a", execution_result=execution),
    )
    assemble_run_report(run_id=run_id, weapon_id="weapon_a")

    # Anomalies are tracked separately and should not flow into the cross-run store.
    assert recurring_failures(weapon_id="weapon_a", lookback=5) == []


def test_recurring_failures_honors_custom_findings_path(tmp_path: Path) -> None:
    custom = tmp_path / "custom_findings"
    FindingsStore(custom).record("weapon_a", "run_1", _finding("shared_issue"))
    FindingsStore(custom).record("weapon_a", "run_2", _finding("shared_issue"))

    out = recurring_failures(
        weapon_id="weapon_a",
        lookback=5,
        findings_path=str(custom),
    )
    assert len(out) == 1
    assert out[0]["issue"] == "shared_issue"
