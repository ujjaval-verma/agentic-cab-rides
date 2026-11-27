"""The runtime-settings surface: .env.example and the Makefile expose AGENTIC_CAB_*."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_VARS = {
    "AGENTIC_CAB_DATA_DIR": "data",
    "AGENTIC_CAB_LOG_DIR": "logs",
    "AGENTIC_CAB_CHAT_DIR": "logs",
    "AGENTIC_CAB_GEOCODE_CACHE_DIR": "data/geocode",
    "AGENTIC_CAB_WEB_PORT": "8000",
}


def _makefile() -> str:
    return (ROOT / "Makefile").read_text(encoding="utf-8")


def _recipe(target: str) -> str:
    match = re.search(rf"^{target}:.*\n((?:\t.*\n)+)", _makefile(), re.MULTILINE)
    assert match is not None, f"no {target} recipe"  # noqa: S101 pytest assertion
    return match.group(1)


@pytest.mark.parametrize(("name", "default"), sorted(_VARS.items()))
def test_env_example_documents_variable_commented_with_default(name: str, default: str) -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(rf"^# {name}={re.escape(default)}$", text, re.MULTILINE), name  # noqa: S101 pytest assertion


def test_makefile_web_port_defaults_from_env() -> None:
    assert "WEB_PORT ?= $(or $(AGENTIC_CAB_WEB_PORT),8000)" in _makefile()  # noqa: S101 pytest assertion
    assert "--port $(WEB_PORT)" in _recipe("web")  # noqa: S101 pytest assertion
    assert "--port $(WEB_PORT)" in _recipe("demo")  # noqa: S101 pytest assertion


def test_makefile_reset_uses_configured_dirs() -> None:
    text = _makefile()
    for var in ("DATA_DIR", "LOG_DIR", "CHAT_DIR", "GEOCODE_CACHE_DIR"):
        assert re.search(rf"^{var} :?= \$\(or \$\(AGENTIC_CAB_{var}\),", text, re.MULTILINE), var  # noqa: S101 pytest assertion
    geocode_default = (
        "GEOCODE_CACHE_DIR := $(or $(AGENTIC_CAB_GEOCODE_CACHE_DIR),$(DATA_DIR)/geocode)"
    )
    assert geocode_default in text.splitlines()  # noqa: S101 pytest assertion
    recipe = _recipe("reset")
    assert 'rm -rf "$(DATA_DIR)" "$(LOG_DIR)" "$(CHAT_DIR)" "$(GEOCODE_CACHE_DIR)"' in recipe  # noqa: S101 pytest assertion
    assert "rm -rf data logs chat" not in recipe  # noqa: S101 pytest assertion


def test_readme_documents_running_two_instances() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    # Section ends at the next level-2+ heading (bash comments in fences use a single `#`).
    match = re.search(
        r"^##+ Running two instances\n(.*?)(?=^##+ |\Z)", text, re.MULTILINE | re.DOTALL
    )
    assert match is not None, "README needs a 'Running two instances' section"  # noqa: S101 pytest assertion
    section = match.group(1)
    assert "AGENTIC_CAB_DATA_DIR" in section  # noqa: S101 pytest assertion
    assert "AGENTIC_CAB_WEB_PORT" in section  # noqa: S101 pytest assertion
    assert "`fixtures/geocode` is read-only" in section  # noqa: S101 pytest assertion


def test_makefile_web_help_does_not_hardcode_port() -> None:
    web_line = next(line for line in _makefile().splitlines() if line.startswith("web:"))
    assert ":8000" not in web_line  # noqa: S101 pytest assertion
