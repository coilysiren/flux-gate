"""Drift-prevention tests for committed JSON Schema files.

If any of these fail, regenerate with::

    uv run python scripts/export_schemas.py
"""

from __future__ import annotations

import json

import pytest

from gauntlet.schemas import SCHEMA_MODELS, generate_schemas, load_schema, schema_path


@pytest.mark.parametrize("name", sorted(SCHEMA_MODELS))
def test_committed_schema_matches_model(name: str) -> None:
    expected = generate_schemas()[name]
    actual = load_schema(name)
    assert actual == expected, (
        f"{name}.schema.json is out of sync with its Pydantic model. "
        f"Regenerate with: uv run python scripts/export_schemas.py"
    )


@pytest.mark.parametrize("name", sorted(SCHEMA_MODELS))
def test_committed_schema_is_valid_json(name: str) -> None:
    with schema_path(name).open() as f:
        json.load(f)


@pytest.mark.parametrize("name", sorted(SCHEMA_MODELS))
def test_schema_declares_draft_2020_12(name: str) -> None:
    schema = load_schema(name)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith(f"/{name}.schema.json")
