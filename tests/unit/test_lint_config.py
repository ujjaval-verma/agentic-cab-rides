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


def _is_bandit(code: str) -> bool:
    # "S" or "S<digits>"; other prefixes starting with S (e.g. SIM) are not bandit.
    return code == "S" or (code.startswith("S") and code[1:].isdigit())


def test_bandit_rules_are_selected() -> None:
    assert "S" in _ruff_lint()["select"]  # noqa: S101 pytest assertion


def test_no_file_level_bandit_ignores() -> None:
    per_file = _ruff_lint().get("per-file-ignores", {})
    offenders = {
        pattern: codes
        for pattern, codes in per_file.items()
        if any(_is_bandit(code) for code in codes)
    }
    assert offenders == {}, "suppress bandit findings line by line with a reason"  # noqa: S101 pytest assertion
