"""Pure pricing functions for UberSim multi-class pricing. T28 Commit 1.

All arithmetic is in integer cents. No floats escape into Money.
"""

from __future__ import annotations

from agentic_cab.core.types import Money, VehicleClass

# Per-vehicle-class rate tables (cents).
# Rates tuned so uberx < comfort < uberxl < black at typical distances.
_BASE_CENTS: dict[VehicleClass, int] = {
    "uberx": 200,
    "comfort": 300,
    "uberxl": 400,
    "black": 600,
}
_PER_KM_CENTS: dict[VehicleClass, int] = {
    "uberx": 120,
    "comfort": 180,
    "uberxl": 220,
    "black": 350,
}
_PER_MIN_CENTS: dict[VehicleClass, int] = {
    "uberx": 25,
    "comfort": 35,
    "uberxl": 45,
    "black": 70,
}


def base_price(vehicle_class: VehicleClass, distance_km: float, duration_min: float) -> Money:
    """Return the base fare for a ride of `distance_km` km and `duration_min` minutes.

    All arithmetic in integer cents; no float stored in the returned Money.
    """
    cents = (
        _BASE_CENTS[vehicle_class]
        + int(_PER_KM_CENTS[vehicle_class] * distance_km)
        + int(_PER_MIN_CENTS[vehicle_class] * duration_min)
    )
    return Money(amount_cents=cents, currency="USD")


__all__ = ["base_price"]
