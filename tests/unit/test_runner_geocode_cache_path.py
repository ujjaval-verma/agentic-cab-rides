"""Assert that the scenario runner's geocode cache points at the committed fixtures.

Invariant 9 (determinism): geocode calls in scenario runs must hit the
committed fixture cache, not live Nominatim HTTP.  A runner that resolves
the fixtures dir relative to `data_dir` misses entirely and falls through
to the network, breaking CI in offline environments.

This test:
1. Verifies that _DEFAULT_GEOCODE_FIXTURES matches the repo-committed path.
2. Verifies that a GeocodeCache constructed at that path returns a non-None
   AddressRef for "Market & Powell, San Francisco" — a query committed as
   fixtures/geocode/1606a863acd4e916163fa91f5da3011fb65ab61b.json.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_cab.adapters.geocoding.cache import GeocodeCache
from agentic_cab.scenarios.runner import _DEFAULT_GEOCODE_FIXTURES

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMMITTED_FIXTURES = _REPO_ROOT / "fixtures" / "geocode"

# Known query committed in fixtures/geocode/1606a....json
_KNOWN_QUERY = "Market & Powell, San Francisco"


def test_default_geocode_fixtures_points_at_committed_path() -> None:
    """_DEFAULT_GEOCODE_FIXTURES resolves to fixtures/geocode at repo root."""
    assert _DEFAULT_GEOCODE_FIXTURES.resolve() == _COMMITTED_FIXTURES.resolve(), (  # noqa: S101 pytest assertion
        f"runner._DEFAULT_GEOCODE_FIXTURES is {_DEFAULT_GEOCODE_FIXTURES!r} "
        f"but expected {_COMMITTED_FIXTURES!r}. "
        "Scenario runs will miss the fixture cache and hit live Nominatim."
    )


def test_committed_fixture_cache_returns_address_ref_for_known_query(tmp_path: Path) -> None:
    """The committed fixtures, as a read-only layer, return an AddressRef for a known query."""
    cache = GeocodeCache(cache_dir=tmp_path, read_only_dirs=(_DEFAULT_GEOCODE_FIXTURES,))
    result = cache.get(_KNOWN_QUERY)
    assert result is not None, (  # noqa: S101 pytest assertion
        f"GeocodeCache.get({_KNOWN_QUERY!r}) returned None. "
        "Either the fixture file is missing or _DEFAULT_GEOCODE_FIXTURES is wrong."
    )
    assert result.lat == pytest.approx(37.7836)  # noqa: S101 pytest assertion
    assert result.lng == pytest.approx(-122.4088)  # noqa: S101 pytest assertion
