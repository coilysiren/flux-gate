"""Small cross-run findings store.

Each Gauntlet run is otherwise fully ephemeral: ``.gauntlet/runs/<run_id>/``
is wiped between runs in practice, and nothing else outlives a host
session. This store is the one deliberate exception — a tiny JSONL file
per trial recording ``{run_id, timestamp, finding}`` entries so a host
can ask "has this same confirmed failure shown up in multiple recent
runs?".

Deleted once, reintroduced once; keep it minimal. Consumers are:

- ``assemble_run_report`` writes confirmed-failure Findings as a side
  effect (wrapped in try/except so the report call never fails on a
  cross-run-store write error).
- ``recurring_failures`` reads and groups by ``finding.issue``.

Schema versioning and per-line skip-on-corrupt read; no append locking at
the OS level yet — the same shape as ``RunStore._append`` — so concurrent
writers can still interleave bytes. Acceptable for now because the store
is append-only and the read side drops corrupt lines rather than failing.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Finding

DEFAULT_FINDINGS_PATH = ".gauntlet/findings"

_SCHEMA_VERSION = 1

_LOG = logging.getLogger(__name__)


class FindingsStore:
    """Filesystem-backed per-trial findings log with JSONL records.

    Records for trial ``W`` live under ``<root>/<W>.jsonl`` with one JSON
    object per line::

        {"schema_version": 1, "run_id": ..., "timestamp": ..., "finding": {...}}

    Readers skip any line that fails to validate (corrupted writes,
    schema mismatches) and log a warning. The store is safe to delete
    wholesale; nothing else in Gauntlet reads from it.
    """

    def __init__(self, root: str | Path = DEFAULT_FINDINGS_PATH) -> None:
        self._root = Path(root)

    def record(self, trial_id: str, run_id: str, finding: Finding) -> None:
        """Append one ``{run_id, timestamp, finding}`` line to the trial's file."""
        self._validate_trial_id(trial_id)
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "finding": json.loads(finding.model_dump_json()),
        }
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._trial_file(trial_id)
        with path.open("a") as fh:
            fh.write(json.dumps(payload) + "\n")

    def recurring(self, trial_id: str, lookback: int = 5) -> list[dict[str, Any]]:
        """Group findings by ``issue`` across the most recent ``lookback`` runs.

        Returns a list of ``{"issue": ..., "occurrences": N, "run_ids":
        [...]}`` for every issue that appeared in at least 2 distinct
        runs within the lookback window. Sorted by (``-occurrences``,
        ``issue``) so the most frequent issues come first.

        A run is counted once even if it produced the same issue multiple
        times (e.g. separate iterations each flagged ``cross_user_patch``).
        """
        self._validate_trial_id(trial_id)
        path = self._trial_file(trial_id)
        if not path.exists():
            return []

        entries = list(self._iter_entries(path))
        run_ids_in_order: list[str] = []
        for entry in entries:
            rid = entry.get("run_id")
            if isinstance(rid, str) and rid not in run_ids_in_order:
                run_ids_in_order.append(rid)
        if lookback <= 0:
            return []
        window = set(run_ids_in_order[-lookback:])

        # issue -> ordered list of run_ids the issue appeared in
        per_issue: dict[str, list[str]] = {}
        for entry in entries:
            rid = entry.get("run_id")
            finding_dict = entry.get("finding")
            if not isinstance(rid, str) or rid not in window:
                continue
            if not isinstance(finding_dict, dict):
                continue
            issue = finding_dict.get("issue")
            if not isinstance(issue, str):
                continue
            bucket = per_issue.setdefault(issue, [])
            if rid not in bucket:
                bucket.append(rid)

        recurring: list[dict[str, Any]] = []
        for issue, run_ids in per_issue.items():
            if len(run_ids) >= 2:
                recurring.append(
                    {
                        "issue": issue,
                        "occurrences": len(run_ids),
                        "run_ids": run_ids,
                    }
                )
        recurring.sort(key=lambda r: (-int(r["occurrences"]), str(r["issue"])))
        return recurring

    def clear(self, trial_id: str) -> None:
        """Delete the trial's findings file. No-op if it doesn't exist."""
        self._validate_trial_id(trial_id)
        path = self._trial_file(trial_id)
        if path.exists():
            path.unlink()

    # --- internal -----------------------------------------------------------

    def _trial_file(self, trial_id: str) -> Path:
        return self._root / f"{trial_id}.jsonl"

    @staticmethod
    def _validate_trial_id(trial_id: str) -> None:
        if not trial_id or "/" in trial_id or "\\" in trial_id or trial_id in {".", ".."}:
            raise ValueError(f"Invalid trial_id {trial_id!r}")

    @staticmethod
    def _iter_entries(path: Path) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                _LOG.warning(
                    "Skipping corrupt line in findings store at %s: %r",
                    path,
                    line[:120],
                )
                continue
            if not isinstance(entry, dict):
                _LOG.warning("Skipping non-object line in findings store at %s", path)
                continue
            entries.append(entry)
        return entries


__all__ = ["DEFAULT_FINDINGS_PATH", "FindingsStore"]
