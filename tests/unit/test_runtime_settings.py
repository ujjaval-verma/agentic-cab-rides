"""RuntimeSettings.from_env — env overrides for runtime paths and the web port."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_cab.core import settings as settings_module
from agentic_cab.core.settings import RuntimeSettings


def test_defaults_when_env_is_empty() -> None:
    s = RuntimeSettings.from_env({})
    assert s.data_dir == Path("data")  # noqa: S101 pytest assertion
    assert s.log_dir == Path("logs")  # noqa: S101 pytest assertion
    assert s.chat_dir == s.log_dir  # noqa: S101 pytest assertion
    assert s.geocode_cache_dir == Path("data/geocode")  # noqa: S101 pytest assertion
    assert s.geocode_fixtures_dir == Path("fixtures/geocode")  # noqa: S101 pytest assertion
    assert s.web_port == 8000  # noqa: S101 pytest assertion


def test_each_variable_overrides_its_field(tmp_path: Path) -> None:
    env = {
        "AGENTIC_CAB_DATA_DIR": str(tmp_path / "d"),
        "AGENTIC_CAB_LOG_DIR": str(tmp_path / "l"),
        "AGENTIC_CAB_CHAT_DIR": str(tmp_path / "c"),
        "AGENTIC_CAB_GEOCODE_CACHE_DIR": str(tmp_path / "g"),
        "AGENTIC_CAB_WEB_PORT": "8123",
    }
    s = RuntimeSettings.from_env(env)
    assert s.data_dir == tmp_path / "d"  # noqa: S101 pytest assertion
    assert s.log_dir == tmp_path / "l"  # noqa: S101 pytest assertion
    assert s.chat_dir == tmp_path / "c"  # noqa: S101 pytest assertion
    assert s.geocode_cache_dir == tmp_path / "g"  # noqa: S101 pytest assertion
    assert s.web_port == 8123  # noqa: S101 pytest assertion


def test_chat_dir_follows_log_dir_when_unset(tmp_path: Path) -> None:
    s = RuntimeSettings.from_env({"AGENTIC_CAB_LOG_DIR": str(tmp_path / "l")})
    assert s.chat_dir == tmp_path / "l"  # noqa: S101 pytest assertion


def test_geocode_cache_follows_data_dir_when_unset(tmp_path: Path) -> None:
    s = RuntimeSettings.from_env({"AGENTIC_CAB_DATA_DIR": str(tmp_path / "d")})
    assert s.geocode_cache_dir == tmp_path / "d" / "geocode"  # noqa: S101 pytest assertion


def test_empty_string_counts_as_unset() -> None:
    env = {
        "AGENTIC_CAB_DATA_DIR": "",
        "AGENTIC_CAB_LOG_DIR": "",
        "AGENTIC_CAB_CHAT_DIR": "",
        "AGENTIC_CAB_GEOCODE_CACHE_DIR": "",
        "AGENTIC_CAB_WEB_PORT": "",
    }
    assert RuntimeSettings.from_env(env) == RuntimeSettings.from_env({})  # noqa: S101 pytest assertion


@pytest.mark.parametrize("value", ["abc", "80.5", "0", "65536", "-1"])
def test_invalid_port_raises_naming_the_variable(value: str) -> None:
    with pytest.raises(ValueError, match="AGENTIC_CAB_WEB_PORT"):
        RuntimeSettings.from_env({"AGENTIC_CAB_WEB_PORT": value})


@pytest.mark.parametrize("sub", ["", "nested"])
def test_cache_dir_inside_fixtures_dir_is_refused(sub: str) -> None:
    target = Path("fixtures/geocode") / sub if sub else Path("fixtures/geocode")
    with pytest.raises(ValueError, match="AGENTIC_CAB_GEOCODE_CACHE_DIR"):
        RuntimeSettings.from_env({"AGENTIC_CAB_GEOCODE_CACHE_DIR": str(target)})


def test_with_overrides_moves_cache_and_chat_with_their_parents(tmp_path: Path) -> None:
    s = RuntimeSettings.from_env({}).with_overrides(
        data_dir=tmp_path / "d", log_dir=tmp_path / "l", geocode_fixtures_dir=tmp_path / "f"
    )
    assert s.data_dir == tmp_path / "d"  # noqa: S101 pytest assertion
    assert s.geocode_cache_dir == tmp_path / "d" / "geocode"  # noqa: S101 pytest assertion
    assert s.log_dir == tmp_path / "l"  # noqa: S101 pytest assertion
    assert s.chat_dir == tmp_path / "l"  # noqa: S101 pytest assertion
    assert s.geocode_fixtures_dir == tmp_path / "f"  # noqa: S101 pytest assertion


def test_with_overrides_keeps_a_separate_chat_dir(tmp_path: Path) -> None:
    base = RuntimeSettings.from_env({"AGENTIC_CAB_CHAT_DIR": str(tmp_path / "c")})
    assert base.with_overrides(log_dir=tmp_path / "l").chat_dir == tmp_path / "c"  # noqa: S101 pytest assertion


def test_with_overrides_no_args_is_identity() -> None:
    base = RuntimeSettings.from_env({})
    assert base.with_overrides() == base  # noqa: S101 pytest assertion


def test_module_reads_no_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The module never reads os.environ itself (invariant 9): only the mapping passed in."""
    monkeypatch.setenv("AGENTIC_CAB_DATA_DIR", str(tmp_path / "from-env"))
    monkeypatch.setenv("AGENTIC_CAB_WEB_PORT", "9999")
    assert "os" not in vars(settings_module)  # noqa: S101 pytest assertion
    assert not any(  # noqa: S101 pytest assertion
        "from-env" in str(v) for v in vars(settings_module).values() if isinstance(v, (str, Path))
    )
    s = RuntimeSettings.from_env({})
    assert s.data_dir == Path("data")  # noqa: S101 pytest assertion
    assert s.web_port == 8000  # noqa: S101 pytest assertion
