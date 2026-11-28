"""Top-level pytest fixtures shared across unit/, contract/, and scenarios/.

Keep this file minimal: fixtures here apply repo-wide, so anything narrower
belongs in a nested conftest.py (see tests/contract/conftest.py).
"""

from __future__ import annotations

import pytest

from agentic_cab.adapters.cost_meter import InMemoryCostMeter
from agentic_cab.core.ports import CostMeter

_RUNTIME_ENV_VARS = (
    "AGENTIC_CAB_DATA_DIR",
    "AGENTIC_CAB_LOG_DIR",
    "AGENTIC_CAB_CHAT_DIR",
    "AGENTIC_CAB_GEOCODE_CACHE_DIR",
    "AGENTIC_CAB_WEB_PORT",
)


@pytest.fixture(autouse=True)
def _hermetic_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never inherit the caller's AGENTIC_CAB_* runtime settings.

    A developer (or a second instance) running the suite with these exported
    would otherwise change which dirs and port the composition roots resolve.
    Tests that exercise the variables set them explicitly with monkeypatch.
    """
    for name in _RUNTIME_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def meter() -> CostMeter:
    """A fresh InMemoryCostMeter per test — the P8 cost-ledger seam."""
    return InMemoryCostMeter()
