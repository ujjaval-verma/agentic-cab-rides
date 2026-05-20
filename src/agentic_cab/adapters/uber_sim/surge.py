"""Surge curve parameterisation for UberSim. T28 Commit 2.

SurgeCurve is pure data; evaluate() is a pure function — no side effects,
no module-level randomness.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel


class SurgeCurve(BaseModel):
    """Surge pricing curve configuration.

    force_surge_on_confirm: when True, evaluate(..., on_confirm=True) always
    returns peak_multiplier regardless of the current time. This lets scenarios
    simulate a surge price change between discover (prepare time) and confirm
    (re-quote time) without requiring a real time advance.
    """

    base_multiplier: Decimal
    peak_multiplier: Decimal
    peak_start: datetime
    peak_duration_min: int
    force_surge_on_confirm: bool = False


def evaluate(curve: SurgeCurve, now: datetime, *, on_confirm: bool) -> Decimal:
    """Return the effective multiplier for *now*.

    When `on_confirm is True` and `curve.force_surge_on_confirm is True`,
    always returns peak_multiplier (simulates a price spike between prepare
    and confirm, regardless of the clock value).

    Otherwise: returns peak_multiplier if now falls within the peak window
    [peak_start, peak_start + peak_duration_min minutes], else base_multiplier.
    """
    if on_confirm and curve.force_surge_on_confirm:
        return curve.peak_multiplier

    peak_end = curve.peak_start + timedelta(minutes=curve.peak_duration_min)
    if curve.peak_start <= now <= peak_end:
        return curve.peak_multiplier
    return curve.base_multiplier


__all__ = ["SurgeCurve", "evaluate"]
