"""Tests for the per-MCP-tool-call structured logging layer."""

from __future__ import annotations

import io
import json
import logging
import math
from collections.abc import Iterator
from pathlib import Path

import pytest

from gauntlet._log import configure_logging, log_tool_call
from gauntlet.server import list_trials


@pytest.fixture()
def json_log_stream() -> Iterator[io.StringIO]:
    """Attach a JSON stderr-equivalent handler to the gauntlet logger.

    We reuse the production ``_JsonFormatter`` by triggering ``configure_logging``
    and then adding our own handler to the ``gauntlet`` logger that writes to
    a StringIO. Records still go through the JSON formatter so the assertion
    exercises the real code path.
    """
    configure_logging()
    logger = logging.getLogger("gauntlet")
    # Match the configured handler's formatter so the captured stream sees the
    # same JSON representation callers would see on stderr.
    formatter = logger.handlers[0].formatter
    assert formatter is not None

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield stream
    finally:
        logger.removeHandler(handler)


def _parse_tool_lines(stream: io.StringIO, tool: str) -> list[dict[str, object]]:
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    return [json.loads(line) for line in lines if f'"tool": "{tool}"' in line]


def test_list_trials_emits_structured_log_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    json_log_stream: io.StringIO,
) -> None:
    monkeypatch.chdir(tmp_path)
    result = list_trials(trials_path=str(tmp_path / "missing"))
    assert result == []

    entries = _parse_tool_lines(json_log_stream, "list_trials")
    assert entries, "expected at least one log entry for list_trials"
    entry = entries[-1]
    assert entry["tool"] == "list_trials"
    assert entry["status"] == "ok"
    duration = entry["duration_ms"]
    assert isinstance(duration, int | float)
    assert math.isfinite(float(duration))
    assert float(duration) >= 0.0
    # Required JSON-formatter fields on every record.
    for key in ("ts", "level", "logger", "msg"):
        assert key in entry


def test_log_tool_call_captures_extras_and_reraises_errors(
    json_log_stream: io.StringIO,
) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with log_tool_call("demo_tool", run_id="r1", trial_id="w1"):
            raise RuntimeError("boom")

    entries = _parse_tool_lines(json_log_stream, "demo_tool")
    assert entries
    entry = entries[-1]
    assert entry["status"] == "error"
    assert entry["exc_type"] == "RuntimeError"
    assert entry["exc_msg"] == "boom"
    assert entry["run_id"] == "r1"
    assert entry["trial_id"] == "w1"
    assert entry["level"] == "ERROR"


def test_configure_logging_is_idempotent() -> None:
    configure_logging()
    first = list(logging.getLogger("gauntlet").handlers)
    configure_logging()
    second = list(logging.getLogger("gauntlet").handlers)
    # No duplicate handler was appended.
    assert len(first) == len(second)
