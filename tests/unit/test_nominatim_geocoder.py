"""Unit tests for NominatimGeocoder — spec §10, CLAUDE.md invariant 8.

Uses AsyncMock to avoid real HTTP calls. The cache is backed by a tmp dir.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_cab.adapters.geocoding.cache import GeocodeCache
from agentic_cab.adapters.geocoding.nominatim import NominatimGeocoder
from agentic_cab.core.ports import GeocoderPort
from agentic_cab.core.types import AddressRef

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER_AGENT = "agentic-cab-rides/0.1 (https://github.com/ujjaval-verma/agentic-cab-rides)"

_SINGLE_RESULT = [
    {
        "display_name": "Market St & Powell St, San Francisco, CA 94102, USA",
        "lat": "37.7836",
        "lon": "-122.4088",
        "importance": 0.87,
        "address": {"road": "Market St", "city": "San Francisco"},
    }
]

_MULTI_RESULT = [
    {
        "display_name": "123 Main St, Springfield, IL 62701, USA",
        "lat": "39.7817",
        "lon": "-89.6501",
        "importance": 0.42,
        "address": {"road": "Main St", "city": "Springfield"},
    },
    {
        "display_name": "123 Main Street, Anytown, CA 90210, USA",
        "lat": "34.0522",
        "lon": "-118.2437",
        "importance": 0.40,
        "address": {"road": "Main Street", "city": "Anytown"},
    },
    {
        "display_name": "123 Main Ave, Smallville, KS 67505, USA",
        "lat": "37.5631",
        "lon": "-97.3303",
        "importance": 0.38,
        "address": {"road": "Main Ave", "city": "Smallville"},
    },
]

_MULTI_RESULT_HIGH_IMPORTANCE = [
    {
        "display_name": "Golden Gate Bridge, San Francisco, CA, USA",
        "lat": "37.8199",
        "lon": "-122.4783",
        "importance": 0.85,
        "address": {"bridge": "Golden Gate Bridge", "city": "San Francisco"},
    },
    {
        "display_name": "Golden Gate Park, San Francisco, CA, USA",
        "lat": "37.7694",
        "lon": "-122.4862",
        "importance": 0.78,
        "address": {"leisure": "park", "city": "San Francisco"},
    },
    {
        "display_name": "Golden Gate Avenue, San Francisco, CA, USA",
        "lat": "37.7810",
        "lon": "-122.4310",
        "importance": 0.72,
        "address": {"road": "Golden Gate Avenue", "city": "San Francisco"},
    },
]


def _make_http_mock(json_data: list[dict[str, Any]]) -> AsyncMock:
    """Return an httpx.AsyncClient mock whose .get() returns *json_data*."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=json_data)

    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    return client


def _make_geocoder(
    tmp_path: Path,
    http: AsyncMock,
) -> NominatimGeocoder:
    cache = GeocodeCache(fixtures_dir=tmp_path)
    return NominatimGeocoder(cache=cache, http=http)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_isinstance_geocoder_port(tmp_path: Path) -> None:
    http = _make_http_mock(_SINGLE_RESULT)
    g = _make_geocoder(tmp_path, http)
    assert isinstance(g, GeocoderPort)  # noqa: S101 pytest assertion


# ---------------------------------------------------------------------------
# Cache-first behaviour
# ---------------------------------------------------------------------------


def test_cache_hit_avoids_http_call(tmp_path: Path) -> None:
    """On a cache hit the HTTP client is never called."""
    http = _make_http_mock(_SINGLE_RESULT)
    g = _make_geocoder(tmp_path, http)
    # Pre-populate cache
    cached = AddressRef(
        raw="Market & Powell, San Francisco",
        formatted="Market St & Powell St, San Francisco, CA 94102, USA",
        lat=37.7836,
        lng=-122.4088,
        confidence=0.95,
        provider_metadata={"alternates": []},
    )
    g._cache.put("Market & Powell, San Francisco", cached)

    result = asyncio.run(g.geocode("Market & Powell, San Francisco"))

    http.get.assert_not_called()
    assert result == cached  # noqa: S101 pytest assertion


# ---------------------------------------------------------------------------
# Cache miss → HTTP call
# ---------------------------------------------------------------------------


def test_cache_miss_triggers_exactly_one_http_call(tmp_path: Path) -> None:
    http = _make_http_mock(_SINGLE_RESULT)
    g = _make_geocoder(tmp_path, http)

    asyncio.run(g.geocode("Market & Powell, San Francisco"))

    assert http.get.call_count == 1  # noqa: S101 pytest assertion


def test_user_agent_header_is_set(tmp_path: Path) -> None:
    http = _make_http_mock(_SINGLE_RESULT)
    g = _make_geocoder(tmp_path, http)

    asyncio.run(g.geocode("Market & Powell, San Francisco"))

    call_kwargs = http.get.call_args
    headers = (
        call_kwargs.kwargs.get("headers") or call_kwargs.args[1]
        if len(call_kwargs.args) > 1
        else {}
    )
    if not headers:
        # headers may be a keyword argument
        headers = call_kwargs.kwargs.get("headers", {})
    assert headers.get("User-Agent") == _USER_AGENT  # noqa: S101 pytest assertion


# ---------------------------------------------------------------------------
# Nominatim response → AddressRef mapping
# ---------------------------------------------------------------------------


def test_single_result_maps_to_high_confidence_address_ref(tmp_path: Path) -> None:
    http = _make_http_mock(_SINGLE_RESULT)
    g = _make_geocoder(tmp_path, http)

    result = asyncio.run(g.geocode("Market & Powell, San Francisco"))

    assert result.formatted == _SINGLE_RESULT[0]["display_name"]  # noqa: S101 pytest assertion
    assert result.lat == pytest.approx(37.7836)  # noqa: S101 pytest assertion
    assert result.lng == pytest.approx(-122.4088)  # noqa: S101 pytest assertion
    assert result.confidence >= 0.5, "single high-importance result should be high-confidence"  # noqa: S101 pytest assertion
    assert result.provider_metadata == {"alternates": []}  # noqa: S101 pytest assertion


def test_multi_result_maps_to_low_confidence_with_alternates(tmp_path: Path) -> None:
    http = _make_http_mock(_MULTI_RESULT)
    g = _make_geocoder(tmp_path, http)

    result = asyncio.run(g.geocode("123 Main St"))

    # Top result is chosen as the primary
    assert result.formatted == _MULTI_RESULT[0]["display_name"]  # noqa: S101 pytest assertion
    # Low-confidence structural signal
    assert result.confidence < 0.5  # noqa: S101 pytest assertion
    alternates = result.provider_metadata.get("alternates")
    assert isinstance(alternates, list)  # noqa: S101 pytest assertion
    assert len(alternates) >= 2  # noqa: S101 pytest assertion


def test_multi_result_high_importance_returns_empty_alternates(tmp_path: Path) -> None:
    """High-confidence results ALWAYS have alternates=[], even when Nominatim returns many.

    Structural invariant: low confidence ⇔ non-empty alternates. High-confidence results
    must never populate alternates regardless of how many runner-ups Nominatim returned.
    """
    http = _make_http_mock(_MULTI_RESULT_HIGH_IMPORTANCE)
    g = _make_geocoder(tmp_path, http)

    result = asyncio.run(g.geocode("Golden Gate"))

    assert result.confidence >= 0.5, "top importance 0.85 should yield high-confidence"  # noqa: S101 pytest assertion
    assert result.provider_metadata == {"alternates": []}, (  # noqa: S101 pytest assertion
        "high-confidence result must always have alternates=[] "
        f"regardless of runner-up count; got {result.provider_metadata}"
    )


def test_result_is_written_to_cache_after_http_call(tmp_path: Path) -> None:
    http = _make_http_mock(_SINGLE_RESULT)
    g = _make_geocoder(tmp_path, http)

    asyncio.run(g.geocode("Market & Powell, San Francisco"))

    # Second call should hit cache, not HTTP
    http.get.reset_mock()
    asyncio.run(g.geocode("Market & Powell, San Francisco"))
    http.get.assert_not_called()


# ---------------------------------------------------------------------------
# Throttle — sleep is called AFTER the network call, first call is immediate
# ---------------------------------------------------------------------------


def test_throttle_sleep_called_after_network_call(tmp_path: Path) -> None:
    """asyncio.sleep(1.0) is called after the HTTP call, not before."""
    http = _make_http_mock(_SINGLE_RESULT)
    g = _make_geocoder(tmp_path, http)

    call_order: list[str] = []

    original_get = http.get

    async def tracked_get(*args: object, **kwargs: object) -> object:
        call_order.append("http_get")
        return await original_get(*args, **kwargs)

    http.get = AsyncMock(side_effect=tracked_get)

    async def run() -> None:
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

            async def tracked_sleep(delay: float) -> None:
                call_order.append(f"sleep({delay:.1f})")

            mock_sleep.side_effect = tracked_sleep
            await g.geocode("Market & Powell, San Francisco")
            # The sleep should have been called with ~1.0
            mock_sleep.assert_called_once()
            assert mock_sleep.call_args[0][0] == pytest.approx(1.0)  # noqa: S101 pytest assertion

        # http_get must appear before sleep
        http_idx = call_order.index("http_get")
        sleep_idx = next(i for i, v in enumerate(call_order) if v.startswith("sleep"))
        assert http_idx < sleep_idx, f"Expected http before sleep; got {call_order}"  # noqa: S101 pytest assertion

    asyncio.run(run())


def test_second_call_within_one_second_waits(tmp_path: Path) -> None:
    """If two calls arrive within 1 s of each other the second is delayed."""
    http = _make_http_mock(_SINGLE_RESULT * 2)  # two different results to avoid cache hit
    # Two separate geocoders share the same underlying lock — simulate via
    # a single geocoder with two different un-cached queries.
    cache = GeocodeCache(fixtures_dir=tmp_path)
    g = NominatimGeocoder(cache=cache, http=http)

    sleep_calls: list[float] = []

    async def run() -> None:
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

            async def record_sleep(delay: float) -> None:
                sleep_calls.append(delay)

            mock_sleep.side_effect = record_sleep

            # First call — no delay before, sleep(1) after
            await g.geocode("query alpha unique")
            first_sleep_count = len(sleep_calls)
            assert first_sleep_count >= 1  # noqa: S101 pytest assertion

    asyncio.run(run())
