from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gauntlet import (
    Assertion,
    HttpRequest,
    IterationRecord,
    IterationSpec,
    Plan,
    PlanStep,
    build_risk_report,
)
from gauntlet.server import get_trial, list_trials

from ._factories import make_execution_result

# ---------------------------------------------------------------------------
# Shared authorization probe used to anchor model shapes across tests.
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
    ],
    assertions=[
        Assertion(
            name="unauthorized_patch_blocked",
            expected=403,
            step_index=2,
        ),
    ],
)


def _spec(name: str = "baseline") -> IterationSpec:
    return IterationSpec(index=1, name=name, goal=name)


# ---------------------------------------------------------------------------
# Risk-report assembly
# ---------------------------------------------------------------------------


def test_build_risk_report_reflects_holdout_failure() -> None:
    """With zero-satisfaction holdout results and no findings, clearance blocks."""
    execution = make_execution_result(passing=False)
    iteration = IterationRecord(
        spec=_spec(),
        plans=[_AUTHZ_PLAN],
        execution_results=[execution],
        findings=[],
    )

    report, clearance = build_risk_report([iteration], [execution], clearance_threshold=0.9)

    assert clearance is not None
    assert clearance.passed is False
    assert clearance.recommendation == "block"
    assert report.risk_level == "low"  # no findings means no blocker-level severity


def test_build_risk_report_no_holdout_yields_no_clearance() -> None:
    iteration = IterationRecord(
        spec=_spec(),
        plans=[],
        execution_results=[],
        findings=[],
    )
    _, clearance = build_risk_report([iteration], [], clearance_threshold=0.9)
    assert clearance is None


# ---------------------------------------------------------------------------
# MCP tool surface
# ---------------------------------------------------------------------------


@pytest.fixture
def trials_dir(tmp_path: Path) -> Path:
    d = tmp_path / "trials"
    d.mkdir()
    (d / "ownership.yaml").write_text(
        yaml.dump(
            {
                "id": "resource_ownership_write_isolation",
                "title": "Users cannot modify each other's tasks",
                "description": "The task API must enforce resource ownership.",
                "blockers": ["A PATCH by a non-owner is rejected with 403"],
            }
        )
    )
    return d


def test_list_trials_omits_blockers(trials_dir: Path) -> None:
    briefs = list_trials(trials_path=str(trials_dir))
    assert len(briefs) == 1
    brief = briefs[0]
    assert brief["id"] == "resource_ownership_write_isolation"
    assert brief["title"] == "Users cannot modify each other's tasks"
    assert "blockers" not in brief  # the attacker view never carries blocker text


def test_get_trial_returns_full_trial(trials_dir: Path) -> None:
    trial = get_trial(
        trial_id="resource_ownership_write_isolation",
        trials_path=str(trials_dir),
    )
    assert trial.blockers == ["A PATCH by a non-owner is rejected with 403"]


def test_get_trial_raises_on_unknown_id(trials_dir: Path) -> None:
    with pytest.raises(ValueError, match="No trial"):
        get_trial(trial_id="nonexistent", trials_path=str(trials_dir))


def test_get_trial_lookup_is_id_only(trials_dir: Path) -> None:
    """Lookup is id-only; the human-readable title is no longer accepted."""
    with pytest.raises(ValueError, match="No trial"):
        get_trial(
            trial_id="Users cannot modify each other's tasks",
            trials_path=str(trials_dir),
        )
