"""Replay tooling: finding → ReplayBundle → deterministic re-execution.

Covers the ``replay_finding`` MCP tool end-to-end and the warning log emitted
when a finding is recorded without a populated ``replay_bundle``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from gauntlet import (
    Finding,
    HttpRequest,
    HttpResponse,
    IterationRecord,
    IterationSpec,
    RunStore,
)
from gauntlet.http import SendResult
from gauntlet.models import ReplayBundle, ReplayStep
from gauntlet.server import (
    record_iteration,
    replay_finding,
    start_run,
)


def _spec() -> IterationSpec:
    return IterationSpec(index=1, name="baseline", goal="baseline")


def _finding_with_replay_bundle() -> Finding:
    return Finding(
        issue="cross_user_patch_allowed",
        severity="high",
        confidence=0.9,
        rationale="PATCH by non-owner returned 200 instead of 403",
        replay_bundle=ReplayBundle(
            steps=[
                ReplayStep(
                    user="userA",
                    request=HttpRequest(method="POST", path="/tasks", body={"title": "t"}),
                ),
                ReplayStep(
                    user="userB",
                    request=HttpRequest(method="PATCH", path="/tasks/1", body={"completed": True}),
                ),
            ]
        ),
    )


def _record_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finding: Finding
) -> tuple[str, str]:
    """Start a run, record one iteration carrying ``finding``, return (run_id, weapon_id)."""
    monkeypatch.chdir(tmp_path)
    out = start_run(weapon_ids=["weapon_a"])
    run_id = out["run_id"]
    record_iteration(
        run_id=run_id,
        weapon_id="weapon_a",
        iteration_record=IterationRecord(
            spec=_spec(),
            plans=[],
            execution_results=[],
            findings=[finding],
        ),
    )
    return run_id, "weapon_a"


def test_replay_finding_round_trips_through_fake_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, weapon_id = _record_finding(tmp_path, monkeypatch, _finding_with_replay_bundle())

    sent: list[tuple[str, str, str]] = []  # (user, method, path)

    def fake_send(self: object, user: str, request: HttpRequest) -> SendResult:  # noqa: ARG001
        sent.append((user, request.method, request.path))
        return SendResult(response=HttpResponse(status_code=200, body={"id": 1}))

    monkeypatch.setattr("gauntlet.http.HttpApi.send", fake_send)

    result = replay_finding(
        run_id=run_id,
        weapon_id=weapon_id,
        finding_index=0,
        url="http://localhost:0",
    )

    assert result.plan_name == "replay:cross_user_patch_allowed"
    assert result.category == "replay"
    assert [s.user for s in result.steps] == ["userA", "userB"]
    assert sent == [
        ("userA", "POST", "/tasks"),
        ("userB", "PATCH", "/tasks/1"),
    ]
    # No assertions in a replay plan; satisfaction_score is trivially 1.0.
    assert result.satisfaction_score == 1.0


def test_replay_finding_without_bundle_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare_finding = Finding(
        issue="no_bundle_finding",
        severity="low",
        confidence=0.5,
        rationale="a finding that forgot its bundle",
    )
    run_id, weapon_id = _record_finding(tmp_path, monkeypatch, bare_finding)

    with pytest.raises(ValueError, match="no replay_bundle"):
        replay_finding(
            run_id=run_id,
            weapon_id=weapon_id,
            finding_index=0,
            url="http://localhost:0",
        )


def test_replay_finding_index_out_of_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id, weapon_id = _record_finding(tmp_path, monkeypatch, _finding_with_replay_bundle())
    with pytest.raises(ValueError, match="out of range"):
        replay_finding(
            run_id=run_id,
            weapon_id=weapon_id,
            finding_index=5,
            url="http://localhost:0",
        )


def test_record_iteration_warns_when_replay_bundle_missing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["weapon_a"])
    bare_finding = Finding(
        issue="no_bundle_finding",
        severity="low",
        confidence=0.4,
        rationale="skipped the bundle",
    )
    record = IterationRecord(
        spec=_spec(),
        plans=[],
        execution_results=[],
        findings=[bare_finding],
    )

    with caplog.at_level(logging.WARNING, logger="gauntlet.runs"):
        store.record_iteration(run_id, "weapon_a", record)

    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("replay_bundle" in m for m in messages)
    assert any(run_id in m for m in messages)
    assert any("weapon_a" in m for m in messages)
    assert any("no_bundle_finding" in m for m in messages)


def test_record_iteration_does_not_warn_when_replay_bundle_present(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = RunStore(tmp_path)
    run_id = store.start_run(["weapon_a"])
    record = IterationRecord(
        spec=_spec(),
        plans=[],
        execution_results=[],
        findings=[_finding_with_replay_bundle()],
    )

    with caplog.at_level(logging.WARNING, logger="gauntlet.runs"):
        store.record_iteration(run_id, "weapon_a", record)

    replay_warnings = [r for r in caplog.records if "replay_bundle" in r.getMessage()]
    assert replay_warnings == []
