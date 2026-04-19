from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _enable_gauntlet_log_propagation() -> Iterator[None]:
    """Let pytest's ``caplog`` observe records under the ``gauntlet.*`` namespace.

    ``gauntlet/_log.configure_logging`` sets ``propagate=False`` on the
    ``gauntlet`` logger so production runs don't double-print through the host's
    root handler. Tests need the opposite: caplog attaches to the root logger
    by default, so it needs records to propagate. This fixture flips propagate
    on per-test and restores it after.
    """
    logger = logging.getLogger("gauntlet")
    previous = logger.propagate
    logger.propagate = True
    try:
        yield
    finally:
        logger.propagate = previous
