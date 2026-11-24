"""Unit tests for UberSim surge curve — T28 Commit 2."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from agentic_cab.adapters.uber_sim.surge import SurgeCurve, evaluate

_PEAK_START = datetime(2026, 5, 19, 17, 0, tzinfo=UTC)  # 5 PM UTC


def _curve(*, force: bool = False) -> SurgeCurve:
    return SurgeCurve(
        base_multiplier=Decimal("1.0"),
        peak_multiplier=Decimal("2.5"),
        peak_start=_PEAK_START,
        peak_duration_min=60,
        force_surge_on_confirm=force,
    )


def test_evaluate_outside_peak_returns_base() -> None:
    curve = _curve()
    now = _PEAK_START - timedelta(hours=1)  # before peak
    result = evaluate(curve, now, on_confirm=False)
    assert result == Decimal("1.0")  # noqa: S101 pytest assertion


def test_evaluate_inside_peak_returns_peak() -> None:
    curve = _curve()
    now = _PEAK_START + timedelta(minutes=30)  # inside peak window
    result = evaluate(curve, now, on_confirm=False)
    assert result == Decimal("2.5")  # noqa: S101 pytest assertion


def test_force_surge_on_confirm_flips_only_on_confirm() -> None:
    """With force_surge_on_confirm=True:
    - on_confirm=False outside peak → base_multiplier (normal discovery time)
    - on_confirm=True outside peak → peak_multiplier (re-quote during confirm)
    """
    curve = _curve(force=True)
    off_peak = _PEAK_START - timedelta(hours=2)
    assert evaluate(curve, off_peak, on_confirm=False) == Decimal("1.0")  # noqa: S101 pytest assertion
    assert evaluate(curve, off_peak, on_confirm=True) == Decimal("2.5")  # noqa: S101 pytest assertion


def test_force_surge_off_by_default() -> None:
    """Default curve (force_surge_on_confirm=False) respects time-based logic."""
    curve = _curve(force=False)
    off_peak = _PEAK_START - timedelta(hours=2)
    # Both on_confirm=False and on_confirm=True return base outside peak
    assert evaluate(curve, off_peak, on_confirm=False) == Decimal("1.0")  # noqa: S101 pytest assertion
    assert evaluate(curve, off_peak, on_confirm=True) == Decimal("1.0")  # noqa: S101 pytest assertion


def test_evaluate_at_peak_boundary_inclusive() -> None:
    """peak_start itself is within the peak window (inclusive)."""
    curve = _curve()
    assert evaluate(curve, _PEAK_START, on_confirm=False) == Decimal("2.5")  # noqa: S101 pytest assertion


def test_evaluate_at_peak_end_boundary() -> None:
    """Exactly at peak_start + peak_duration_min is within the window (inclusive)."""
    curve = _curve()
    peak_end = _PEAK_START + timedelta(minutes=60)
    assert evaluate(curve, peak_end, on_confirm=False) == Decimal("2.5")  # noqa: S101 pytest assertion


def test_evaluate_after_peak_end_returns_base() -> None:
    """One second after peak window closes → base_multiplier."""
    curve = _curve()
    after_end = _PEAK_START + timedelta(minutes=60, seconds=1)
    assert evaluate(curve, after_end, on_confirm=False) == Decimal("1.0")  # noqa: S101 pytest assertion
