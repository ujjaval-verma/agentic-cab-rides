"""Top-level pytest fixtures shared across unit/, contract/, and scenarios/.

Keep this file minimal: fixtures here apply repo-wide, so anything narrower
belongs in a nested conftest.py (see tests/contract/conftest.py).
"""

from __future__ import annotations

import pytest

from agentic_cab.adapters.cost_meter import InMemoryCostMeter
from agentic_cab.core.ports import CostMeter


@pytest.fixture
def meter() -> CostMeter:
    """A fresh InMemoryCostMeter per test — the P8 cost-ledger seam."""
    return InMemoryCostMeter()
