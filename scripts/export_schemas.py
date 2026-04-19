#!/usr/bin/env python
"""Regenerate ``gauntlet/schemas/*.schema.json`` from the Pydantic models.

Run after editing ``gauntlet/models.py`` or ``gauntlet/auth.py``::

    uv run python scripts/export_schemas.py

The drift test in ``tests/test_schemas.py`` fails if this was not run.
"""

from __future__ import annotations

import json

from gauntlet.schemas import SCHEMAS_DIR, generate_schemas


def main() -> None:
    for name, schema in generate_schemas().items():
        out = SCHEMAS_DIR / f"{name}.schema.json"
        out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
        print(f"wrote {out.relative_to(SCHEMAS_DIR.parent.parent)}")


if __name__ == "__main__":
    main()
