"""Subagent definitions ship with the plugin and carry the right allowlists.

The tool allowlist on each subagent is what physically enforces Gauntlet's
train/test split. If a subagent's allowlist silently grows to include a
forbidden tool, the structural enforcement collapses back to prompt
discipline. These tests catch that.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"

ATTACKER_ALLOW = {
    "mcp__gauntlet__list_weapons",
    "mcp__gauntlet__execute_plan",
    "mcp__gauntlet__read_iteration_records",
    "mcp__gauntlet__record_iteration",
    "mcp__gauntlet__mutate_plans",
}
ATTACKER_FORBID = {
    "mcp__gauntlet__get_weapon",
    "mcp__gauntlet__read_holdout_results",
    "mcp__gauntlet__record_holdout_result",
    "mcp__gauntlet__assemble_final_clearance",
}

INSPECTOR_ALLOW = {
    "mcp__gauntlet__read_iteration_records",
    "mcp__gauntlet__record_iteration",
}
INSPECTOR_FORBID = {
    "mcp__gauntlet__get_weapon",
    "mcp__gauntlet__execute_plan",
    "mcp__gauntlet__read_holdout_results",
    "mcp__gauntlet__record_holdout_result",
    "mcp__gauntlet__assemble_final_clearance",
}

HOLDOUT_ALLOW = {
    "mcp__gauntlet__get_weapon",
    "mcp__gauntlet__execute_plan",
    "mcp__gauntlet__record_holdout_result",
    "mcp__gauntlet__assemble_final_clearance",
}
HOLDOUT_FORBID = {
    "mcp__gauntlet__read_iteration_records",
    "mcp__gauntlet__record_iteration",
    "mcp__gauntlet__list_weapons",
}


def _read_frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text()
    if not text.startswith("---\n"):
        raise AssertionError(f"{path} is missing YAML frontmatter")
    end = text.index("\n---\n", 4)
    block = text[4:end]
    data = yaml.safe_load(block)
    assert isinstance(data, dict)
    return data


def _allowlist(path: Path) -> set[str]:
    fm = _read_frontmatter(path)
    raw = fm.get("tools")
    assert raw, f"{path} has no 'tools' frontmatter"
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw]
    else:
        items = [s.strip() for s in str(raw).split(",")]
    return {item for item in items if item}


@pytest.mark.parametrize(
    "filename, name, description_substr",
    [
        ("gauntlet-attacker.md", "gauntlet-attacker", "Attacker"),
        ("gauntlet-inspector.md", "gauntlet-inspector", "Inspector"),
        ("gauntlet-holdout-evaluator.md", "gauntlet-holdout-evaluator", "Holdout"),
    ],
)
def test_subagent_frontmatter_present(filename: str, name: str, description_substr: str) -> None:
    path = AGENTS_DIR / filename
    fm = _read_frontmatter(path)
    assert fm["name"] == name
    assert isinstance(fm.get("description"), str)
    assert description_substr.lower() in str(fm["description"]).lower()
    assert "tools" in fm


def test_attacker_allowlist_matches_role() -> None:
    tools = _allowlist(AGENTS_DIR / "gauntlet-attacker.md")
    assert ATTACKER_ALLOW.issubset(tools)
    assert tools.isdisjoint(ATTACKER_FORBID), (
        f"Attacker allowlist must not include {tools & ATTACKER_FORBID}"
    )


def test_inspector_allowlist_matches_role() -> None:
    tools = _allowlist(AGENTS_DIR / "gauntlet-inspector.md")
    # Inspector also gets Read for orchestrator-supplied context paths.
    assert INSPECTOR_ALLOW.issubset(tools)
    assert tools.isdisjoint(INSPECTOR_FORBID), (
        f"Inspector allowlist must not include {tools & INSPECTOR_FORBID}"
    )


def test_holdout_evaluator_allowlist_matches_role() -> None:
    tools = _allowlist(AGENTS_DIR / "gauntlet-holdout-evaluator.md")
    assert HOLDOUT_ALLOW.issubset(tools)
    assert tools.isdisjoint(HOLDOUT_FORBID), (
        f"HoldoutEvaluator allowlist must not include {tools & HOLDOUT_FORBID}"
    )
