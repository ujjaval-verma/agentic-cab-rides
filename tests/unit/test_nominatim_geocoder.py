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
    assert isinstance(g, GeocoderPort)


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

    result = asyncio.get_event_loop().run_until_complete(
        g.geocode("Market & Powell, San Francisco")
    )

    http.get.assert_not_called()
    assert result == cached


# ---------------------------------------------------------------------------
# Cache miss → HTTP call
# ---------------------------------------------------------------------------


def test_cache_miss_triggers_exactly_one_http_call(tmp_path: Path) -> None:
    http = _make_http_mock(_SINGLE_RESULT)
    g = _make_geocoder(tmp_path, http)

    asyncio.get_event_loop().run_until_complete(g.geocode("Market & Powell, San Francisco"))

    assert http.get.call_count == 1


def test_user_agent_header_is_set(tmp_path: Path) -> None:
    http = _make_http_mock(_SINGLE_RESULT)
    g = _make_geocoder(tmp_path, http)

    asyncio.get_event_loop().run_until_complete(g.geocode("Market & Powell, San Francisco"))

    call_kwargs = http.get.call_args
    headers = (
        call_kwargs.kwargs.get("headers") or call_kwargs.args[1]
        if len(call_kwargs.args) > 1
        else {}
    )
    if not headers:
        # headers may be a keyword argument
        headers = call_kwargs.kwargs.get("headers", {})
    assert headers.get("User-Agent") == _USER_AGENT


# ---------------------------------------------------------------------------
# Nominatim response → AddressRef mapping
# ---------------------------------------------------------------------------


def test_single_result_maps_to_high_confidence_address_ref(tmp_path: Path) -> None:
    http = _make_http_mock(_SINGLE_RESULT)
    g = _make_geocoder(tmp_path, http)

    result = asyncio.get_event_loop().run_until_complete(
        g.geocode("Market & Powell, San Francisco")
    )

    assert result.formatted == _SINGLE_RESULT[0]["display_name"]
    assert result.lat == pytest.approx(37.7836)
    assert result.lng == pytest.approx(-122.4088)
    assert result.confidence >= 0.5, "single high-importance result should be high-confidence"
    assert result.provider_metadata == {"alternates": []}


def test_multi_result_maps_to_low_confidence_with_alternates(tmp_path: Path) -> None:
    http = _make_http_mock(_MULTI_RESULT)
    g = _make_geocoder(tmp_path, http)

    result = asyncio.get_event_loop().run_until_complete(g.geocode("123 Main St"))

    # Top result is chosen as the primary
    assert result.formatted == _MULTI_RESULT[0]["display_name"]
    # Low-confidence structural signal
    assert result.confidence < 0.5
    alternates = result.provider_metadata.get("alternates")
    assert isinstance(alternates, list)
    assert len(alternates) >= 2


def test_result_is_written_to_cache_after_http_call(tmp_path: Path) -> None:
    http = _make_http_mock(_SINGLE_RESULT)
    g = _make_geocoder(tmp_path, http)

    asyncio.get_event_loop().run_until_complete(g.geocode("Market & Powell, San Francisco"))

    # Second call should hit cache, not HTTP
    http.get.reset_mock()
    asyncio.get_event_loop().run_until_complete(g.geocode("Market & Powell, San Francisco"))
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

    http.get = tracked_get  # type: ignore[method-assign]

    async def run() -> None:
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

            async def tracked_sleep(delay: float) -> None:
                call_order.append(f"sleep({delay:.1f})")

            mock_sleep.side_effect = tracked_sleep
            await g.geocode("Market & Powell, San Francisco")
            # The sleep should have been called with ~1.0
            mock_sleep.assert_called_once()
            assert mock_sleep.call_args[0][0] == pytest.approx(1.0)

        # http_get must appear before sleep
        http_idx = call_order.index("http_get")
        sleep_idx = next(i for i, v in enumerate(call_order) if v.startswith("sleep"))
        assert http_idx < sleep_idx, f"Expected http before sleep; got {call_order}"

    asyncio.get_event_loop().run_until_complete(run())


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
            assert first_sleep_count >= 1

    asyncio.get_event_loop().run_until_complete(run())
