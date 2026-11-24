"""Guard the ruff security configuration in pyproject.toml.

Bandit rules (`S`) must stay selected so security findings are either fixed or
suppressed line by line with a reason.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _ruff_lint() -> dict[str, Any]:
    data = tomllib.loads(_PYPROJECT.read_text())
    return data["tool"]["ruff"]["lint"]


def test_bandit_rules_are_selected() -> None:
    assert "S" in _ruff_lint()["select"]
