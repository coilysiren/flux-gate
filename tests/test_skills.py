"""Skills ship with the plugin and carry the right frontmatter.

Skill discovery is by-convention (Claude Code reads the YAML frontmatter at
``skills/<name>/SKILL.md``). Catch frontmatter regressions at CI time so a
broken trigger phrase or missing `name` does not silently disable the skill.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def _read_frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text()
    if not text.startswith("---\n"):
        raise AssertionError(f"{path} is missing YAML frontmatter")
    end = text.index("\n---\n", 4)
    block = text[4:end]
    data = yaml.safe_load(block)
    assert isinstance(data, dict)
    return data


@pytest.mark.parametrize(
    "skill_name, description_substr",
    [
        ("gauntlet", "adversarial"),
        ("gauntlet-author", "trial"),
    ],
)
def test_skill_frontmatter_present(skill_name: str, description_substr: str) -> None:
    path = SKILLS_DIR / skill_name / "SKILL.md"
    assert path.exists(), f"skill {skill_name} is missing at {path}"
    fm = _read_frontmatter(path)
    assert fm["name"] == skill_name
    assert isinstance(fm.get("description"), str)
    assert description_substr.lower() in str(fm["description"]).lower()


def test_gauntlet_author_skill_documents_train_test_split() -> None:
    """The author skill must explain that description and blockers play different roles.

    If this section disappears, the skill will start authoring trials whose
    description gives away the blockers — collapsing the train/test split
    before any run starts.
    """
    text = (SKILLS_DIR / "gauntlet-author" / "SKILL.md").read_text().lower()
    assert "train/test split" in text
    assert "blocker" in text
    assert "description" in text
