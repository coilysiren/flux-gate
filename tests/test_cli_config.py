from __future__ import annotations

import os
from pathlib import Path

import yaml
from click.testing import CliRunner

from gauntlet.cli import _load_config_file, main


def test_load_config_file_returns_empty_when_no_default(tmp_path: Path) -> None:
    """When no explicit path and no default file exists, return empty dict."""
    original = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert _load_config_file(None) == {}
    finally:
        os.chdir(original)


def test_load_config_file_reads_explicit_path(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.dump({"url": "http://example.com", "threshold": 0.5}))
    result = _load_config_file(str(cfg))
    assert result["url"] == "http://example.com"
    assert result["threshold"] == 0.5


def test_load_config_file_reads_default(tmp_path: Path) -> None:
    """When no explicit path is given, .gauntlet/config.yaml is loaded if present."""
    (tmp_path / ".gauntlet").mkdir()
    (tmp_path / ".gauntlet" / "config.yaml").write_text(
        yaml.dump({"url": "http://default.local", "weapon": "/custom/weapons"})
    )
    original = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = _load_config_file(None)
        assert result["url"] == "http://default.local"
        assert result["weapon"] == "/custom/weapons"
    finally:
        os.chdir(original)


def test_load_config_file_exits_on_missing_explicit(tmp_path: Path) -> None:
    """An explicit --config pointing to a missing file causes exit."""
    runner = CliRunner()
    result = runner.invoke(main, ["--config", str(tmp_path / "nope.yaml")])
    assert result.exit_code != 0
    assert "config file not found" in result.output


def test_cli_url_from_config_file(tmp_path: Path) -> None:
    """URL can be provided via config file instead of positional argument."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.dump({"url": "http://from-config.local"}))
    runner = CliRunner()
    # Will fail due to missing env vars, but should get past URL validation
    result = runner.invoke(main, ["--config", str(cfg)])
    assert "URL is required" not in result.output
    assert "missing required environment variables" in result.output


def test_cli_flag_overrides_config(tmp_path: Path) -> None:
    """CLI flags take precedence over config file values."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.dump({"url": "http://config-url.local", "threshold": 0.5}))
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["http://cli-url.local", "--config", str(cfg), "--threshold", "0.75"],
    )
    # Should get past URL validation with CLI url, fail on env vars
    assert "URL is required" not in result.output
    assert "missing required environment variables" in result.output


def test_cli_requires_url(tmp_path: Path) -> None:
    """Without URL in args or config, an error is shown."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.dump({"weapon": "/some/path"}))
    runner = CliRunner()
    result = runner.invoke(main, ["--config", str(cfg)])
    assert result.exit_code != 0
    assert "URL is required" in result.output


def test_config_fail_fast_hyphen(tmp_path: Path) -> None:
    """Config files using 'fail-fast' (with hyphen) are handled correctly."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.dump({"url": "http://example.com", "fail-fast": False}))
    result = _load_config_file(str(cfg))
    assert result["fail-fast"] is False
