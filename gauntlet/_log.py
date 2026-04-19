"""Structured stderr logging for per-MCP-tool-call observability.

One JSON line per tool invocation, written to stderr via the stdlib
``logging`` module. The host pipes stderr wherever it wants (terminal, file,
log aggregator). No new dependencies — the formatter is a hand-rolled
``json.dumps`` wrapper.

Fields per record:

- ``ts``: ISO 8601 UTC timestamp
- ``level``: standard logging level name
- ``logger``: logger name
- ``msg``: rendered log message
- any extras passed via ``logger.log(..., extra=...)`` or via
  :func:`log_tool_call`

Deliberately NOT in this module:

- A summary file written at end-of-run
- A per-call timings JSONL alongside the buffers
- OpenTelemetry / tracing

See [TODO.md](../TODO.md) "In-flight structured logging" for the scope
rationale.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

# Reserved keys from ``LogRecord`` that aren't user extras. We diff the record
# dict against this set to pick out arbitrary structured fields the caller
# attached via ``extra={...}``.
_RESERVED_LOGRECORD_KEYS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "taskName",
    "thread",
    "threadName",
}


class _JsonFormatter(logging.Formatter):
    """Serialize each ``LogRecord`` as a one-line JSON object on stderr."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Forward any structured extras the caller attached.
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOGRECORD_KEYS or key.startswith("_"):
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = repr(value)
            payload[key] = value
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["exc_msg"] = str(record.exc_info[1]) if record.exc_info[1] else None
        return json.dumps(payload, default=str)


_CONFIGURED = False


def configure_logging() -> None:
    """Attach a JSON stderr handler to the ``gauntlet`` logger namespace.

    Idempotent — safe to call repeatedly. Honors ``GAUNTLET_LOG_LEVEL`` (any
    of the standard logging level names; defaults to ``INFO``).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = os.environ.get("GAUNTLET_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger("gauntlet")
    logger.setLevel(level)
    # propagate=False keeps records out of the root logger so host logging
    # setup (e.g. MCP stdio) doesn't double-print.
    logger.propagate = False

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    handler.setLevel(level)
    logger.addHandler(handler)
    _CONFIGURED = True


@contextmanager
def log_tool_call(tool: str, **extras: Any) -> Iterator[None]:
    """Context manager: emit one structured log line per MCP tool call.

    On successful exit, logs ``status="ok"`` plus ``duration_ms``. On
    exception, logs ``status="error"`` with ``exc_type`` and ``exc_msg``, then
    re-raises. Any keyword arguments bound at entry (e.g. ``run_id``,
    ``weapon_id``) are included on the log line.
    """
    logger = logging.getLogger("gauntlet.tool")
    start = time.perf_counter()
    try:
        yield
    except BaseException as exc:
        duration_ms = (time.perf_counter() - start) * 1000.0
        logger.error(
            "tool_call_error",
            extra={
                "tool": tool,
                "status": "error",
                "duration_ms": round(duration_ms, 3),
                "exc_type": type(exc).__name__,
                "exc_msg": str(exc),
                **extras,
            },
        )
        raise
    else:
        duration_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "tool_call_ok",
            extra={
                "tool": tool,
                "status": "ok",
                "duration_ms": round(duration_ms, 3),
                **extras,
            },
        )


__all__ = ["configure_logging", "log_tool_call"]
