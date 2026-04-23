"""Across-iteration plan mutator: strategies, determinism, MCP integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from gauntlet import (
    Assertion,
    HttpRequest,
    IterationRecord,
    IterationSpec,
    Plan,
    PlanStep,
)
from gauntlet._mutator import mutate_plans
from gauntlet.server import (
    mutate_plans as mutate_plans_tool,
)
from gauntlet.server import (
    record_iteration,
    start_run,
)


def _spec() -> IterationSpec:
    return IterationSpec(index=1, name="baseline", goal="baseline")


def _rich_plan() -> Plan:
    return Plan(
        name="user_cannot_modify_other_users_task",
        category="authz",
        goal="cross-user modification should be rejected",
        steps=[
            PlanStep(
                user="userA",
                request=HttpRequest(
                    method="POST", path="/tasks", body={"title": "t", "completed": False}
                ),
            ),
            PlanStep(
                user="userB",
                request=HttpRequest(
                    method="PATCH", path="/tasks/{task_id}", body={"completed": True}
                ),
            ),
            PlanStep(
                user="userA",
                request=HttpRequest(method="GET", path="/tasks/{task_id}"),
            ),
        ],
        assertions=[
            Assertion(
                name="unauthorized_patch_blocked", kind="status_code", expected=403, step_index=2
            ),
        ],
    )


def _one_user_plan() -> Plan:
    """Plan with only one user (for testing swap_users no-op)."""
    return Plan(
        name="single_user_probe",
        category="boundary",
        goal="single-user probe",
        steps=[
            PlanStep(
                user="userA",
                request=HttpRequest(method="POST", path="/tasks", body={"title": "t"}),
            ),
        ],
        assertions=[],
    )


# ---------------------------------------------------------------------------
# Per-strategy shape
# ---------------------------------------------------------------------------


def _find(variants: list[Plan], suffix: str) -> Plan:
    for variant in variants:
        if variant.name.endswith(f":mut-{suffix}"):
            return variant
    raise AssertionError(f"no variant with suffix {suffix!r} in {[v.name for v in variants]}")


def test_drop_field_removes_one_body_key() -> None:
    seed = _rich_plan()
    variants = mutate_plans([seed], max_variants=4)
    mutant = _find(variants, "drop_field")
    # Count keys across all bodies; exactly one fewer than the seed.
    seed_keys = sum(len(step.request.body) for step in seed.steps)
    mutant_keys = sum(len(step.request.body) for step in mutant.steps)
    assert mutant_keys == seed_keys - 1


def test_swap_users_rotates_user_assignments() -> None:
    seed = _rich_plan()
    variants = mutate_plans([seed], max_variants=4)
    mutant = _find(variants, "swap_users")
    seed_users = [s.user for s in seed.steps]
    mutant_users = [s.user for s in mutant.steps]
    assert mutant_users != seed_users
    # Same multiset of users, just rearranged.
    assert sorted(mutant_users) == sorted(seed_users)


def test_toggle_expected_negates_matchers() -> None:
    seed = _rich_plan()
    variants = mutate_plans([seed], max_variants=4)
    mutant = _find(variants, "toggle_expected")
    assert mutant.assertions[0].expected == {"not": 403}


def test_reverse_order_reverses_steps() -> None:
    seed = _rich_plan()
    variants = mutate_plans([seed], max_variants=4)
    mutant = _find(variants, "reverse_order")
    assert [s.user for s in mutant.steps] == [s.user for s in reversed(seed.steps)]


def test_strategies_are_skipped_when_they_noop() -> None:
    seed = _one_user_plan()
    variants = mutate_plans([seed], max_variants=4)
    # swap_users: needs ≥ 2 distinct users — skip.
    # toggle_expected: no assertions — skip.
    # reverse_order: only one step — skip.
    # drop_field: body={"title": "t"} — one key, still mutable.
    suffixes = {v.name.rsplit(":mut-", 1)[-1] for v in variants}
    assert "swap_users" not in suffixes
    assert "toggle_expected" not in suffixes
    assert "reverse_order" not in suffixes
    assert "drop_field" in suffixes


def test_max_variants_bounds_output() -> None:
    seed = _rich_plan()
    assert len(mutate_plans([seed], max_variants=2)) == 2
    assert len(mutate_plans([seed], max_variants=4)) == 4
    assert len(mutate_plans([seed], max_variants=0)) == 0


def test_empty_input_returns_empty() -> None:
    assert mutate_plans([], max_variants=4) == []


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_mutator_is_deterministic() -> None:
    seed = _rich_plan()
    a = mutate_plans([seed], max_variants=4)
    b = mutate_plans([seed], max_variants=4)
    assert [v.model_dump() for v in a] == [v.model_dump() for v in b]


def test_mutator_determinism_across_seed_plan_ordering() -> None:
    rich = _rich_plan()
    single = _one_user_plan()
    # Different input orderings should not produce identical outputs (the
    # strategy-major iteration interleaves seeds), but each ordering should
    # be stable on its own.
    a1 = mutate_plans([rich, single], max_variants=4)
    a2 = mutate_plans([rich, single], max_variants=4)
    assert [v.model_dump() for v in a1] == [v.model_dump() for v in a2]


# ---------------------------------------------------------------------------
# MCP tool integration
# ---------------------------------------------------------------------------


def test_mutate_plans_mcp_tool_sees_recorded_plans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    out = start_run(trial_ids=["trial_a"])
    run_id = out["run_id"]

    record = IterationRecord(
        spec=_spec(),
        plans=[_rich_plan()],
        execution_results=[],
        findings=[],
    )
    record_iteration(run_id=run_id, trial_id="trial_a", iteration_record=record)

    variants = mutate_plans_tool(
        run_id=run_id,
        trial_id="trial_a",
        max_variants=4,
    )
    assert len(variants) == 4
    # All variants carry the mutator suffix convention.
    assert all(":mut-" in v.name for v in variants)
    # All variants are rooted in the seed plan name.
    assert all(v.name.startswith(_rich_plan().name + ":mut-") for v in variants)


def test_mutate_plans_mcp_tool_returns_empty_for_trial_with_no_plans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    out = start_run(trial_ids=["trial_a"])
    run_id = out["run_id"]
    variants = mutate_plans_tool(
        run_id=run_id,
        trial_id="trial_a",
        max_variants=4,
    )
    assert variants == []


def test_mutate_plans_mcp_tool_dedupes_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plans with the same name across iterations are only mutated once."""
    monkeypatch.chdir(tmp_path)
    out = start_run(trial_ids=["trial_a"])
    run_id = out["run_id"]
    # Record the same plan twice across two iterations.
    for _ in range(2):
        record_iteration(
            run_id=run_id,
            trial_id="trial_a",
            iteration_record=IterationRecord(
                spec=_spec(),
                plans=[_rich_plan()],
                execution_results=[],
                findings=[],
            ),
        )
    variants = mutate_plans_tool(
        run_id=run_id,
        trial_id="trial_a",
        max_variants=4,
    )
    # One seed plan → four strategies → at most 4 variants, not 8.
    assert len(variants) == 4
