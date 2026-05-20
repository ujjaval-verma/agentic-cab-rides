"""Unit tests for tools/geocode.py — read-only tool, emits requested + outcome only."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.core.types import AddressRef
from agentic_cab.tools.geocode import GeocodeArgs, geocode_address

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
        session_id="S1",
        turn_id="T1",
    )

    rows = log.read("S1")
    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}: {rows}"

    requested, outcome = rows
    assert requested["kind"] == "requested"
    assert outcome["kind"] == "outcome"
    # Both share same action_id
    assert requested["action_id"] == outcome["action_id"]
    # Outcome shape
    assert outcome["status"] == "ok"
    assert outcome["error"] is None
    res = outcome["result"]
    assert res["formatted"] == "123 Main St, Springfield, IL 62701, USA"
    assert res["lat"] == 39.7817
    assert res["lng"] == -89.6501
    assert res["confidence"] == 0.9
    assert res["alternates_count"] == 0
    # Return value matches outcome result
    assert result["formatted"] == res["formatted"]
    assert result["alternates_count"] == 0


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
        session_id="S2",
        turn_id="T2",
    )

    rows = log.read("S2")
    outcome = rows[1]
    res = outcome["result"]
    assert res["alternates_count"] == 2
    assert res["confidence"] == 0.3
    # Raw alternates list must NOT appear in logs (invariant 8 PII redaction)
    assert "alternates" not in res


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
        session_id="S3",
        turn_id="T3",
    )

    rows = log.read("S3")
    outcome = rows[1]
    assert outcome["result"]["alternates_count"] == 0


@pytest.mark.asyncio
async def test_geocoder_error_emits_outcome_error(tmp_path: Path) -> None:
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    geocoder = _StubGeocoder(ValueError("nominatim down"))
    args = GeocodeArgs(query="bad address")

    result = await geocode_address(
        args,
        geocoder=geocoder,
        log=log,
        session_id="S4",
        turn_id="T4",
    )

    rows = log.read("S4")
    assert len(rows) == 2
    requested, outcome = rows
    assert requested["kind"] == "requested"
    assert outcome["kind"] == "outcome"
    assert outcome["status"] == "error"
    assert outcome["error"] == "nominatim down"
    assert outcome["result"] is None
    # Return value matches
    assert result["status"] == "error"
    assert result["error"] == "nominatim down"
    assert result["result"] is None
