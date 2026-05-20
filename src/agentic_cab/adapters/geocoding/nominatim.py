"""NominatimGeocoder — implements GeocoderPort against the Nominatim public API.

Confidence-mapping rationale (spec §10 low-confidence structural signal):
  - Nominatim returns results sorted by `importance` descending (range ~0.0-1.0).
  - If Nominatim returns exactly ONE result, we trust it: confidence = min(importance, 1.0).
  - If Nominatim returns MULTIPLE results whose top importance is below a threshold (0.5),
    we treat the result as ambiguous: confidence = min(top_importance, 0.45).
    This keeps confidence < 0.5, which is the structural low-confidence signal.
    The remaining results (up to 4) populate provider_metadata["alternates"].
  - If Nominatim returns MULTIPLE results whose top importance is >= 0.5, we still return
    the top result as high-confidence (confidence = min(importance, 1.0)) but populate
    alternates so callers can see the runner-up candidates.
  - High-confidence results always have provider_metadata={"alternates": []} when there
    is no ambiguity, or with populated alternates if the top result is clear but others
    exist (confidence >= 0.5 in both cases — the structural signal is < 0.5).

Throttle (Nominatim ToS — max 1 req/sec):
  - One asyncio.Lock guards the HTTP call.
  - The pattern is: acquire lock → call HTTP → sleep(1.0) → release.
  - First call is immediate (no pre-sleep). The sleep always happens AFTER the call.

PII: raw query text is never logged. Only the SHA-1 hash prefix is safe to log.
"""

from __future__ import annotations

import asyncio
from typing import Any, Self

import httpx
import structlog

from agentic_cab.adapters.geocoding.cache import GeocodeCache
from agentic_cab.core.types import AddressRef

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "agentic-cab-rides/0.1 (https://github.com/ujjaval-verma/agentic-cab-rides)"
_THROTTLE_SECS = 1.0
_AMBIGUITY_IMPORTANCE_THRESHOLD = 0.5  # below → low-confidence signal
_MAX_ALTERNATES = 4

log = structlog.get_logger(__name__)


class NominatimGeocoder:
    """GeocoderPort implementation backed by the Nominatim public geocoding API.

    Constructor parameters are dependency-injected so tests can pass mocks.

    Args:
        cache: GeocodeCache instance for fixture storage.
        http: httpx.AsyncClient (real or mocked).
    """

    def __init__(self: Self, cache: GeocodeCache, http: httpx.AsyncClient) -> None:
        self._cache = cache
        self._http = http
        self._lock = asyncio.Lock()
        self._last_call_at: float = 0.0

    async def geocode(self: Self, query: str) -> AddressRef:
        """Geocode *query*, returning a cached result if available.

        Cache is checked first; on miss a Nominatim request is made,
        results are mapped to AddressRef, the result is cached, and returned.
        """
        cached = self._cache.get(query)
        if cached is not None:
            return cached

        result = await self._fetch(query)
        self._cache.put(query, result)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch(self: Self, query: str) -> AddressRef:
        """Make a throttled Nominatim HTTP request and map to AddressRef."""
        async with self._lock:
            params: dict[str, str] = {
                "q": query,
                "format": "json",
                "addressdetails": "1",
                "limit": str(_MAX_ALTERNATES + 1),
            }
            response = await self._http.get(
                _NOMINATIM_URL,
                params=params,
                headers={"User-Agent": _USER_AGENT},
            )
            response.raise_for_status()
            results: list[dict[str, Any]] = response.json()

            # Throttle: sleep AFTER the call so the first call is immediate.
            await asyncio.sleep(_THROTTLE_SECS)

        if not results:
            msg = "Nominatim returned no results"
            raise ValueError(msg)

        return self._map(query, results)

    def _map(self: Self, query: str, results: list[dict[str, Any]]) -> AddressRef:
        """Map a Nominatim result list to AddressRef with the confidence formula."""
        top = results[0]
        top_importance: float = float(top.get("importance", 0.5))
        rest = results[1 : _MAX_ALTERNATES + 1]

        formatted: str = str(top["display_name"])
        lat = float(top["lat"])
        lng = float(top["lon"])

        if len(results) > 1 and top_importance < _AMBIGUITY_IMPORTANCE_THRESHOLD:
            # Ambiguous: low-confidence structural signal
            confidence = min(top_importance, 0.45)
            alternates = [
                {
                    "formatted": str(r["display_name"]),
                    "lat": float(r["lat"]),
                    "lng": float(r["lon"]),
                }
                for r in rest
            ]
        else:
            # Clear winner or single result
            confidence = min(top_importance, 1.0)
            alternates = [
                {
                    "formatted": str(r["display_name"]),
                    "lat": float(r["lat"]),
                    "lng": float(r["lon"]),
                }
                for r in rest
            ]

        return AddressRef(
            raw=query,
            formatted=formatted,
            lat=lat,
            lng=lng,
            confidence=confidence,
            provider_metadata={"alternates": alternates},
        )
