"""CLI honours RuntimeSettings: `web` port from AGENTIC_CAB_WEB_PORT, `init-db` data dir."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentic_cab import __main__ as cli


def capture_web_call(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], *args: str
) -> dict[str, Any]:
    """Invoke `web` with *env*, capturing uvicorn.run kwargs and create_app kwargs."""
    captured: dict[str, Any] = {}

    def _fake_create_app(**kwargs: Any) -> str:
        captured["create_app"] = kwargs
        return "app"

    def _fake_run(app: Any, **kwargs: Any) -> None:
        captured["run"] = kwargs

    monkeypatch.setattr(cli, "create_app", _fake_create_app)
    monkeypatch.setattr(cli.uvicorn, "run", _fake_run)
    result = CliRunner().invoke(cli.app, ["web", *args], env=env)
    assert result.exit_code == 0, result.output  # noqa: S101 pytest assertion
    return captured


def test_web_port_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = capture_web_call(monkeypatch, {"AGENTIC_CAB_WEB_PORT": "8123"})
    assert captured["run"]["port"] == 8123  # noqa: S101 pytest assertion
    assert captured["create_app"]["settings"].web_port == 8123  # noqa: S101 pytest assertion


def test_web_port_flag_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = capture_web_call(monkeypatch, {"AGENTIC_CAB_WEB_PORT": "8123"}, "--port", "8200")
    assert captured["run"]["port"] == 8200  # noqa: S101 pytest assertion


def test_web_port_defaults_to_8000(monkeypatch: pytest.MonkeyPatch) -> None:
    assert capture_web_call(monkeypatch, {})["run"]["port"] == 8000  # noqa: S101 pytest assertion


def test_web_passes_env_dirs_to_create_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured = capture_web_call(monkeypatch, {"AGENTIC_CAB_DATA_DIR": str(tmp_path / "d")})
    assert captured["create_app"]["settings"].data_dir == tmp_path / "d"  # noqa: S101 pytest assertion


def test_init_db_uses_env_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "custom-data"
    result = CliRunner().invoke(cli.app, ["init-db"], env={"AGENTIC_CAB_DATA_DIR": str(data)})
    assert result.exit_code == 0, result.output  # noqa: S101 pytest assertion
    assert (data / "state.db").exists()  # noqa: S101 pytest assertion
    assert not (tmp_path / "data").exists()  # noqa: S101 pytest assertion
