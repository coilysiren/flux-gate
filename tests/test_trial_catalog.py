"""Validate every shipped trial under ``.gauntlet/trials/``.

The catalog has real operational value — a trial that silently fails to
load is one the orchestrator never runs, and the user never gets a signal
that anything is missing. These tests make that failure loud.

Each trial is exercised three ways:

1. The YAML parses and passes pydantic validation (shape + snake_case id).
2. The content is meaningful — non-empty description, non-empty blockers,
   no stubby placeholder strings that slipped through review.
3. The collection is internally consistent — ids are unique, filenames
   correspond to ids where a convention exists.

New trials added under ``.gauntlet/trials/`` automatically get the
treatment — pytest parametrizes over the directory contents at collection
time.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gauntlet.models import Trial

TRIALS_DIR = Path(__file__).resolve().parents[1] / ".gauntlet" / "trials"


def _all_trial_files() -> list[Path]:
    if not TRIALS_DIR.exists():
        return []
    return sorted(TRIALS_DIR.glob("*.yaml"))


_TRIAL_FILES = _all_trial_files()


@pytest.mark.parametrize("path", _TRIAL_FILES, ids=lambda p: p.stem)
def test_trial_yaml_loads(path: Path) -> None:
    """Every YAML in ``.gauntlet/trials/`` round-trips through ``Trial``."""
    data = yaml.safe_load(path.read_text())
    Trial(**data)


@pytest.mark.parametrize("path", _TRIAL_FILES, ids=lambda p: p.stem)
def test_trial_has_meaningful_content(path: Path) -> None:
    """No stub placeholders, no single-sentence descriptions, no empty blockers."""
    trial = Trial(**yaml.safe_load(path.read_text()))
    assert trial.id, f"{path.name}: missing id"
    assert trial.title.strip(), f"{path.name}: empty title"
    # A real description has at least ~100 chars and isn't a single fragment.
    assert len(trial.description.strip()) >= 100, (
        f"{path.name}: description looks stubby ({len(trial.description)} chars)"
    )
    assert trial.blockers, f"{path.name}: no blockers"
    assert len(trial.blockers) >= 2, (
        f"{path.name}: at least 2 blockers recommended, got {len(trial.blockers)}"
    )
    for blocker in trial.blockers:
        assert len(blocker.strip()) >= 20, f"{path.name}: blocker too short: {blocker!r}"
        assert not blocker.strip().lower().startswith("todo"), (
            f"{path.name}: stub blocker left in: {blocker!r}"
        )


def test_trial_ids_are_unique() -> None:
    """Two trials with the same id would silently shadow each other in the buffer."""
    ids: list[str] = []
    for path in _TRIAL_FILES:
        data = yaml.safe_load(path.read_text())
        if data.get("id"):
            ids.append(data["id"])
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"duplicate trial ids: {sorted(duplicates)}"


def test_catalog_has_trials() -> None:
    """Regression guard — if the shipping directory gets emptied by accident."""
    assert len(_TRIAL_FILES) >= 10, f"trial catalog collapsed to {len(_TRIAL_FILES)} entries"


def test_owasp_api_top10_coverage() -> None:
    """Keep the OWASP API Security Top 10 (2023) family covered end-to-end.

    If an OWASP trial gets removed, this test fails loudly — the OWASP set
    is a promise the README and trial filenames make. Less opinionated
    families (temporal, identity, state) aren't pinned here because they
    move more freely.
    """
    owasp_ids: set[str] = set()
    for path in _TRIAL_FILES:
        data = yaml.safe_load(path.read_text())
        wid = data.get("id", "")
        if wid.startswith("owasp_"):
            owasp_ids.add(wid)
    expected = {
        "owasp_bola",
        "owasp_broken_auth",
        "owasp_bopla",
        "owasp_bfla",
        "owasp_unrestricted_business_flows",
        "owasp_improper_inventory",
        "owasp_unsafe_api_consumption",
        "owasp_security_misconfiguration",
    }
    missing = expected - owasp_ids
    assert not missing, f"missing OWASP API trials: {sorted(missing)}"
