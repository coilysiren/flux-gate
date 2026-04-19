"""Run-scoped buffer for iteration records and holdout results.

Owned by Gauntlet so every consumer inherits the same per-run filesystem
layout. The orchestrator calls ``start_run`` once at the top of a hardening
loop, then per-role subagents append iteration and holdout artifacts via the
MCP tool surface. The buffer is short-lived: one run, one host session. No
history across runs is preserved or required.

Storage layout under ``root/<run_id>/``::

    manifest.json                     # run metadata: weapon_ids, started_at
    <weapon_id>/iterations.jsonl      # one IterationRecord per line
    <weapon_id>/holdouts.jsonl        # one HoldoutResult per line

JSONL is chosen so multiple subagent processes can append concurrently (one
process per role, possibly across separate Claude Code sessions) without
needing a shared lock — each writer appends a self-contained line. On POSIX,
``_append`` takes an ``fcntl.flock`` on the file handle for the duration of
the write to prevent byte interleaving under concurrent writers. On non-POSIX
platforms (Windows CI) ``fcntl`` is unavailable and the code falls back to
plain append, which is still safe for single-process writers.

Train/test enforcement: ``record_iteration`` raises if any finding carries
``violated_blocker``. The Inspector role never sees blocker text, so a
populated ``violated_blocker`` here means either the role discipline broke
or the buffer is being misused as a holdout sink.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX (Windows) fallback
    fcntl = None  # type: ignore[assignment]

from .models import HoldoutResult, IterationRecord

DEFAULT_RUNS_PATH = ".gauntlet/runs"

# Bumped when the on-disk buffer layout changes in a way readers must key off.
# Old buffers without a ``schema_version`` key are treated as version 1.
SCHEMA_VERSION = 1

_RUN_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
_logger = logging.getLogger(__name__)

_LOG = logging.getLogger(__name__)


class RunStore:
    """Filesystem-backed buffer for one or more concurrent runs.

    A single ``RunStore`` instance is shared by every MCP tool call in a host
    session. Methods that mutate the buffer (``start_run``, ``record_*``)
    create directories on demand; readers return empty lists when the buffer
    has not been written yet.
    """

    def __init__(self, root: str | Path = DEFAULT_RUNS_PATH) -> None:
        self._root = Path(root)
        # Per-file counter of JSONL lines that failed to parse on read. Keyed
        # by ``(run_id, weapon_id)`` — file-name agnostic because corrupt
        # records in either the iteration or holdout buffer are treated the
        # same way by the host.
        self._corrupt_counts: dict[tuple[str, str], int] = {}

    def start_run(self, weapon_ids: list[str]) -> str:
        """Initialize a new run, persist its manifest, and return the run id."""
        run_id = self._new_run_id()
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "run_id": run_id,
            "weapon_ids": list(weapon_ids),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": SCHEMA_VERSION,
        }
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        for weapon_id in weapon_ids:
            self._weapon_dir(run_id, weapon_id).mkdir(parents=True, exist_ok=True)
        return run_id

    def list_weapon_ids(self, run_id: str) -> list[str]:
        """Return the weapons declared at ``start_run`` time."""
        manifest_path = self._run_dir(run_id) / "manifest.json"
        if not manifest_path.exists():
            raise ValueError(f"No run with id {run_id!r}")
        data = json.loads(manifest_path.read_text())
        # Missing ``schema_version`` is tolerated (treat as 1). No migration
        # code yet — the field is established so future readers can key off it.
        _ = int(data.get("schema_version", 1))
        ids: list[str] = list(data.get("weapon_ids", []))
        return ids

    def record_iteration(self, run_id: str, weapon_id: str, record: IterationRecord) -> None:
        """Append one ``IterationRecord`` to the weapon's iteration buffer.

        Raises ``ValueError`` if any finding has ``violated_blocker`` set —
        the train/test split forbids blocker text from entering this buffer.

        Emits a warning log for any finding that arrives without a populated
        ``replay_bundle``. Reproducibility is a load-bearing promise of a
        Gauntlet finding; a missing bundle means the Inspector skipped the
        capture step. Warnings rather than rejections for now so the
        discipline can bed in without breaking in-flight hosts.
        """
        for finding in record.findings:
            if finding.violated_blocker is not None:
                raise ValueError(
                    "IterationRecord findings must not carry 'violated_blocker' — "
                    "the Inspector context never sees blocker text. Set it to None "
                    "or route the finding through the holdout buffer instead."
                )
            if finding.replay_bundle is None:
                _LOG.warning(
                    "Finding recorded without replay_bundle "
                    "(run_id=%s weapon_id=%s issue=%s). The Inspector should "
                    "populate replay_bundle by copying ReplayStep data from "
                    "the ExecutionStepResult(s) that produced this finding.",
                    run_id,
                    weapon_id,
                    finding.issue,
                )
        self._append(run_id, weapon_id, "iterations.jsonl", record.model_dump_json())

    def read_iteration_records(self, run_id: str, weapon_id: str) -> list[IterationRecord]:
        """Return every ``IterationRecord`` previously appended for the weapon.

        Corrupt lines (invalid JSON or failing schema validation) are skipped
        with a logged warning and tallied in :meth:`corrupt_record_counts`.
        """
        records: list[IterationRecord] = []
        for line in self._read_lines(run_id, weapon_id, "iterations.jsonl"):
            try:
                records.append(IterationRecord.model_validate_json(line))
            except (ValueError, TypeError) as exc:
                self._corrupt_counts[(run_id, weapon_id)] = (
                    self._corrupt_counts.get((run_id, weapon_id), 0) + 1
                )
                _logger.warning(
                    "Skipping corrupt IterationRecord in run_id=%s weapon_id=%s: %s",
                    run_id,
                    weapon_id,
                    exc,
                )
        return records

    def record_holdout_result(self, run_id: str, weapon_id: str, result: HoldoutResult) -> None:
        """Append one ``HoldoutResult`` to the weapon's holdout buffer."""
        if result.weapon_id != weapon_id:
            raise ValueError(
                f"HoldoutResult.weapon_id ({result.weapon_id!r}) does not match "
                f"weapon_id argument ({weapon_id!r})"
            )
        self._append(run_id, weapon_id, "holdouts.jsonl", result.model_dump_json())

    def read_holdout_results(self, run_id: str, weapon_id: str) -> list[HoldoutResult]:
        """Return every ``HoldoutResult`` previously appended for the weapon.

        Corrupt lines (invalid JSON or failing schema validation) are skipped
        with a logged warning and tallied in :meth:`corrupt_record_counts`.
        """
        results: list[HoldoutResult] = []
        for line in self._read_lines(run_id, weapon_id, "holdouts.jsonl"):
            try:
                results.append(HoldoutResult.model_validate_json(line))
            except (ValueError, TypeError) as exc:
                self._corrupt_counts[(run_id, weapon_id)] = (
                    self._corrupt_counts.get((run_id, weapon_id), 0) + 1
                )
                _logger.warning(
                    "Skipping corrupt HoldoutResult in run_id=%s weapon_id=%s: %s",
                    run_id,
                    weapon_id,
                    exc,
                )
        return results

    def corrupt_record_counts(self) -> dict[tuple[str, str], int]:
        """Return a snapshot of ``(run_id, weapon_id) -> corrupt-line count``.

        Read-only: callers receive a copy and cannot mutate the internal
        counter. Only records skipped during a ``read_*`` call on this store
        instance are counted; restarting the process resets the counts.
        """
        return dict(self._corrupt_counts)

    # --- internal -----------------------------------------------------------

    @staticmethod
    def _new_run_id() -> str:
        return uuid.uuid4().hex[:12]

    def _run_dir(self, run_id: str) -> Path:
        if not _RUN_ID_RE.match(run_id):
            raise ValueError(f"Invalid run_id {run_id!r}")
        return self._root / run_id

    def _weapon_dir(self, run_id: str, weapon_id: str) -> Path:
        if not weapon_id or "/" in weapon_id or "\\" in weapon_id or weapon_id in {".", ".."}:
            raise ValueError(f"Invalid weapon_id {weapon_id!r}")
        return self._run_dir(run_id) / weapon_id

    def _append(self, run_id: str, weapon_id: str, filename: str, payload: str) -> None:
        weapon_dir = self._weapon_dir(run_id, weapon_id)
        weapon_dir.mkdir(parents=True, exist_ok=True)
        path = weapon_dir / filename
        with path.open("a") as fh:
            if fcntl is not None:
                # Exclusive advisory lock — serializes concurrent writers from
                # separate subagent processes. Released when the handle closes.
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.write(payload + "\n")
                fh.flush()
            finally:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _read_lines(self, run_id: str, weapon_id: str, filename: str) -> list[str]:
        path = self._weapon_dir(run_id, weapon_id) / filename
        if not path.exists():
            return []
        return [line for line in path.read_text().splitlines() if line.strip()]


__all__ = ["DEFAULT_RUNS_PATH", "SCHEMA_VERSION", "RunStore"]
