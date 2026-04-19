"""JSON Schemas for user-authored YAML artifacts (Weapon, Target, Arsenal, UsersConfig).

The schemas are generated from the Pydantic models via ``generate_schemas()`` and
committed as ``*.schema.json`` alongside this module so that external tooling
(``check-jsonschema``, ``ajv``, IDE YAML plugins) can validate Planner output
without importing the package.

To regenerate after editing models, run::

    uv run python scripts/export_schemas.py

A test in ``tests/test_schemas.py`` fails if the committed files drift from the
models — catching forgotten regenerations at CI time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..auth import UsersConfig
from ..models import Arsenal, Target, Weapon

SCHEMAS_DIR = Path(__file__).parent

_SCHEMA_ID_BASE = "https://github.com/coilysiren/gauntlet/schemas"

SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "weapon": Weapon,
    "target": Target,
    "arsenal": Arsenal,
    "users": UsersConfig,
}


def generate_schemas() -> dict[str, dict[str, Any]]:
    """Return ``name → JSON-Schema dict`` for every user-authored YAML artifact."""
    schemas: dict[str, dict[str, Any]] = {}
    for name, model in SCHEMA_MODELS.items():
        schema = model.model_json_schema()
        schema["$id"] = f"{_SCHEMA_ID_BASE}/{name}.schema.json"
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schemas[name] = schema
    return schemas


def schema_path(name: str) -> Path:
    """Absolute path to the committed schema file for ``name``."""
    return SCHEMAS_DIR / f"{name}.schema.json"


def load_schema(name: str) -> dict[str, Any]:
    """Load the committed schema file for ``name`` from disk."""
    with schema_path(name).open() as f:
        data: dict[str, Any] = json.load(f)
    return data
