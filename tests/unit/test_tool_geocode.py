"""Unit tests for tools/geocode.py — read-only tool, emits requested + outcome only."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.core.types import AddressRef
from agentic_cab.tools.geocode import GeocodeArgs, geocode_address

_FROZEN_CLOCK = FrozenClock(start=datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC))

# ---------------------------------------------------------------------------
# Stub GeocoderPort — DI only, no Nominatim in unit tests
# ---------------------------------------------------------------------------


class _StubGeocoder:
    def __init__(self, ref: AddressRef | Exception) -> None:
        self._ref = ref

    async def geocode(self, query: str) -> AddressRef:
        if isinstance(self._ref, Exception):
            raise self._ref
        return self._ref


def _make_ref(
    *,
    alternates: list[object] | None = None,
    confidence: float = 0.9,
) -> AddressRef:
    return AddressRef(
        raw="123 Main St",
        formatted="123 Main St, Springfield, IL 62701, USA",
        lat=39.7817,
        lng=-89.6501,
        confidence=confidence,
        provider_metadata={"alternates": alternates or []},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_emits_requested_then_outcome(tmp_path: Path) -> None:
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    geocoder = _StubGeocoder(_make_ref())
    args = GeocodeArgs(query="123 Main St")

    result = await geocode_address(
        args,
        geocoder=geocoder,
        log=log,
        clock=_FROZEN_CLOCK,
        session_id="S1",
        turn_id="T1",
    )

    rows = log.read("S1")
    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}: {rows}"  # noqa: S101 pytest assertion

    requested, outcome = rows
    assert requested["kind"] == "requested"  # noqa: S101 pytest assertion
    assert outcome["kind"] == "outcome"  # noqa: S101 pytest assertion
    # Both share same action_id
    assert requested["action_id"] == outcome["action_id"]  # noqa: S101 pytest assertion
    # Outcome shape
    assert outcome["status"] == "ok"  # noqa: S101 pytest assertion
    assert outcome["error"] is None  # noqa: S101 pytest assertion
    res = outcome["result"]
    assert res["formatted"] == "123 Main St, Springfield, IL 62701, USA"  # noqa: S101 pytest assertion
    assert res["lat"] == 39.7817  # noqa: S101 pytest assertion
    assert res["lng"] == -89.6501  # noqa: S101 pytest assertion
    assert res["confidence"] == 0.9  # noqa: S101 pytest assertion
    assert res["alternates_count"] == 0  # noqa: S101 pytest assertion
    # Return value matches outcome result
    assert result["formatted"] == res["formatted"]  # noqa: S101 pytest assertion
    assert result["alternates_count"] == 0  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_outcome_includes_alternates_count_for_ambiguous_result(tmp_path: Path) -> None:
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    ref = _make_ref(
        alternates=[{"formatted": "A"}, {"formatted": "B"}],
        confidence=0.3,
    )
    geocoder = _StubGeocoder(ref)
    args = GeocodeArgs(query="ambiguous street")

    await geocode_address(
        args,
        geocoder=geocoder,
        log=log,
        clock=_FROZEN_CLOCK,
        session_id="S2",
        turn_id="T2",
    )

    rows = log.read("S2")
    outcome = rows[1]
    res = outcome["result"]
    assert res["alternates_count"] == 2  # noqa: S101 pytest assertion
    assert res["confidence"] == 0.3  # noqa: S101 pytest assertion
    # Raw alternates list must NOT appear in logs (invariant 8 PII redaction)
    assert "alternates" not in res  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_outcome_alternates_count_zero_for_high_confidence(tmp_path: Path) -> None:
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    ref = _make_ref(alternates=[], confidence=0.95)
    geocoder = _StubGeocoder(ref)
    args = GeocodeArgs(query="1 Infinite Loop, Cupertino")

    await geocode_address(
        args,
        geocoder=geocoder,
        log=log,
        clock=_FROZEN_CLOCK,
        session_id="S3",
        turn_id="T3",
    )

    rows = log.read("S3")
    outcome = rows[1]
    assert outcome["result"]["alternates_count"] == 0  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_geocoder_error_emits_outcome_error(tmp_path: Path) -> None:
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    geocoder = _StubGeocoder(ValueError("nominatim down"))
    args = GeocodeArgs(query="bad address")

    result = await geocode_address(
        args,
        geocoder=geocoder,
        log=log,
        clock=_FROZEN_CLOCK,
        session_id="S4",
        turn_id="T4",
    )

    rows = log.read("S4")
    assert len(rows) == 2  # noqa: S101 pytest assertion
    requested, outcome = rows
    assert requested["kind"] == "requested"  # noqa: S101 pytest assertion
    assert outcome["kind"] == "outcome"  # noqa: S101 pytest assertion
    assert outcome["status"] == "error"  # noqa: S101 pytest assertion
    assert outcome["error"] == "nominatim down"  # noqa: S101 pytest assertion
    assert outcome["result"] is None  # noqa: S101 pytest assertion
    # Return value matches
    assert result["status"] == "error"  # noqa: S101 pytest assertion
    assert result["error"] == "nominatim down"  # noqa: S101 pytest assertion
    assert result["result"] is None  # noqa: S101 pytest assertion
