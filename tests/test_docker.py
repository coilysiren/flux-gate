import subprocess

import pytest


@pytest.mark.docker
def test_demo_service_runs() -> None:
    """flux-gate runs against the demo API and the gate correctly blocks the seeded flaw."""
    result = subprocess.run(
        ["docker", "compose", "run", "--rm", "demo"],
        capture_output=True,
        text=True,
    )
    # The demo API has a seeded authorization flaw — the gate should block.
    assert result.returncode == 1
    assert "merge_gate" in result.stdout
    assert "block" in result.stdout
    assert "BLOCKED" in result.stderr


@pytest.mark.docker
def test_test_service_passes() -> None:
    """Unit tests pass inside the container."""
    result = subprocess.run(
        ["docker", "compose", "run", "--rm", "test"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
