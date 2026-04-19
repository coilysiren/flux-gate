from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gauntlet import (
    Assertion,
    DemoWeaponAssessor,
    HttpRequest,
    IterationRecord,
    IterationSpec,
    Plan,
    PlanStep,
    Target,
    Weapon,
    build_default_iteration_specs,
    build_risk_report,
)
from gauntlet.server import (
    assemble_run_report,
    assess_weapon,
    default_iteration_specs,
    get_weapon,
    list_targets,
    list_weapons,
)

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


# ---------------------------------------------------------------------------
# Risk-report assembly
# ---------------------------------------------------------------------------


def test_build_risk_report_reflects_holdout_failure() -> None:
    """With zero-satisfaction holdout results and no findings, clearance blocks."""
    execution = make_execution_result(passing=False)
    iteration = IterationRecord(
        spec=build_default_iteration_specs()[0],
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
        spec=build_default_iteration_specs()[0],
        plans=[],
        execution_results=[],
        findings=[],
    )
    _, clearance = build_risk_report([iteration], [], clearance_threshold=0.9)
    assert clearance is None


# ---------------------------------------------------------------------------
# Weapon quality assessment
# ---------------------------------------------------------------------------


def test_weapon_assessor_rejects_vague_weapon() -> None:
    vague = Weapon(
        title="Make it secure",
        description="It should be secure.",
        blockers=["secure", "no bugs"],
    )
    assessment = DemoWeaponAssessor().assess(vague, None)
    assert assessment.proceed is False
    assert assessment.quality_score < 0.5


def test_weapon_assessor_accepts_good_weapon() -> None:
    good = Weapon(
        title="Users cannot modify each other's tasks",
        description="The task API must enforce resource ownership.",
        blockers=["A PATCH by a non-owner is rejected with 403"],
    )
    target = Target(title="Task endpoints", endpoints=["PATCH /tasks/{id}"])
    assessment = DemoWeaponAssessor().assess(good, target)
    assert assessment.proceed is True
    assert assessment.quality_score >= 0.5


# ---------------------------------------------------------------------------
# MCP tool surface
# ---------------------------------------------------------------------------


@pytest.fixture
def weapons_dir(tmp_path: Path) -> Path:
    d = tmp_path / "weapons"
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


def test_list_weapons_omits_blockers(weapons_dir: Path) -> None:
    briefs = list_weapons(weapons_path=str(weapons_dir))
    assert len(briefs) == 1
    brief = briefs[0]
    assert brief.id == "resource_ownership_write_isolation"
    assert brief.title == "Users cannot modify each other's tasks"
    # WeaponBrief has no blockers field — Pydantic enforces this, but belt-and-braces:
    assert not hasattr(brief, "blockers")


def test_get_weapon_returns_full_weapon(weapons_dir: Path) -> None:
    weapon = get_weapon(
        weapon_id="resource_ownership_write_isolation",
        weapons_path=str(weapons_dir),
    )
    assert weapon.blockers == ["A PATCH by a non-owner is rejected with 403"]


def test_get_weapon_raises_on_unknown_id(weapons_dir: Path) -> None:
    with pytest.raises(ValueError, match="No weapon"):
        get_weapon(weapon_id="nonexistent", weapons_path=str(weapons_dir))


def test_list_targets_reads_yaml_dir(tmp_path: Path) -> None:
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    (targets_dir / "task_endpoints.yaml").write_text(
        yaml.dump(
            {
                "title": "Task endpoints",
                "endpoints": ["POST /tasks", "PATCH /tasks/{id}"],
            }
        )
    )
    targets = list_targets(targets_path=str(targets_dir))
    assert len(targets) == 1
    assert targets[0].endpoints == ["POST /tasks", "PATCH /tasks/{id}"]


def test_list_targets_returns_empty_when_missing(tmp_path: Path) -> None:
    assert list_targets(targets_path=str(tmp_path / "does-not-exist")) == []


def test_assess_weapon_via_mcp_surface(weapons_dir: Path) -> None:
    assessment = assess_weapon(
        weapon_id="resource_ownership_write_isolation",
        weapons_path=str(weapons_dir),
        target=Target(title="Task endpoints", endpoints=["PATCH /tasks/{id}"]),
    )
    assert assessment.proceed is True


def test_default_iteration_specs_returns_four_stages() -> None:
    specs = default_iteration_specs()
    assert [s.name for s in specs] == [
        "baseline",
        "boundary",
        "adversarial_misuse",
        "targeted_escalation",
    ]


def test_assemble_run_report_shapes_output() -> None:
    execution = make_execution_result(passing=False)
    iteration = IterationRecord(
        spec=IterationSpec(
            index=1,
            name="baseline",
            goal="baseline",
            attacker_prompt="",
            inspector_prompt="",
        ),
        plans=[_AUTHZ_PLAN],
        execution_results=[execution],
        findings=[],
    )

    out = assemble_run_report(
        iterations=[iteration],
        holdout_results=[execution],
        clearance_threshold=0.9,
    )

    assert "risk_report" in out
    assert "clearance" in out
    assert out["clearance"] is not None
    assert out["clearance"]["recommendation"] == "block"
