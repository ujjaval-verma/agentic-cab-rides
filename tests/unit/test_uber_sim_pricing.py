"""Unit tests for UberSim pricing module — T28 Commit 1."""

from __future__ import annotations

from agentic_cab.adapters.uber_sim.pricing import base_price
from agentic_cab.core.types import Money


def test_base_price_distinct_per_vehicle_class() -> None:
    """Same distance + duration must yield distinct prices for each class."""
    km, dur = 5.0, 10.0
    prices = {
        vc: base_price(vc, km, dur).amount_cents for vc in ("uberx", "comfort", "uberxl", "black")
    }  # type: ignore[arg-type]
    assert len(set(prices.values())) == 4, f"Expected 4 distinct prices, got {prices}"  # noqa: S101 pytest assertion
    # Ordering: uberx < comfort < uberxl < black
    assert prices["uberx"] < prices["comfort"] < prices["uberxl"] < prices["black"]  # noqa: S101 pytest assertion


def test_base_price_scales_with_distance() -> None:
    """Doubling distance must increase price."""
    p1 = base_price("uberx", 5.0, 10.0)
    p2 = base_price("uberx", 10.0, 10.0)
    assert p2.amount_cents > p1.amount_cents  # noqa: S101 pytest assertion


def test_base_price_scales_with_duration() -> None:
    """Doubling duration must increase price."""
    p1 = base_price("uberx", 5.0, 10.0)
    p2 = base_price("uberx", 5.0, 20.0)
    assert p2.amount_cents > p1.amount_cents  # noqa: S101 pytest assertion


def test_base_price_returns_money_with_currency() -> None:
    """Return type is Money with currency='USD'."""
    m = base_price("uberx", 3.0, 8.0)
    assert isinstance(m, Money)  # noqa: S101 pytest assertion
    assert m.currency == "USD"  # noqa: S101 pytest assertion
    assert m.amount_cents > 0  # noqa: S101 pytest assertion


def test_base_price_no_float_leakage() -> None:
    """amount_cents must be a Python int (no float stored)."""
    m = base_price("black", 7.5, 15.0)
    assert isinstance(m.amount_cents, int)  # noqa: S101 pytest assertion
