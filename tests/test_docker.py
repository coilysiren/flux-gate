import subprocess

import pytest


@pytest.mark.docker
def test_app_service_runs():
    result = subprocess.run(
        ["docker", "compose", "run", "--rm", "app"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "risk_report" in result.stdout


@pytest.mark.docker
def test_test_service_passes():
    result = subprocess.run(
        ["docker", "compose", "run", "--rm", "test"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
