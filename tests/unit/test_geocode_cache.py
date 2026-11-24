"""Unit tests for GeocodeCache — spec §10, CLAUDE.md invariant 7.

Tests run against a tmp dir so they are hermetic. Fixtures/ is never written
during unit tests.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentic_cab.adapters.geocoding.cache import GeocodeCache
from agentic_cab.core.types import AddressRef


def _make_addr(**kwargs: object) -> AddressRef:
    defaults: dict[str, object] = {
        "raw": "Market & Powell, San Francisco, CA",
        "formatted": "Market St & Powell St, San Francisco, CA 94102, USA",
        "lat": 37.7836,
        "lng": -122.4088,
        "confidence": 0.95,
        "provider_metadata": {"alternates": []},
    }
    defaults.update(kwargs)
    return AddressRef.model_validate(defaults)


def test_cache_miss_returns_none(tmp_path: Path) -> None:
    cache = GeocodeCache(fixtures_dir=tmp_path)
    result = cache.get("some query that was never stored")
    assert result is None  # noqa: S101 pytest assertion


def test_cache_hit_returns_stored_address_ref(tmp_path: Path) -> None:
    cache = GeocodeCache(fixtures_dir=tmp_path)
    addr = _make_addr()
    cache.put("Market & Powell, San Francisco", addr)
    result = cache.get("Market & Powell, San Francisco")
    assert result is not None  # noqa: S101 pytest assertion
    assert result == addr  # noqa: S101 pytest assertion


def test_key_derivation_is_case_insensitive(tmp_path: Path) -> None:
    cache = GeocodeCache(fixtures_dir=tmp_path)
    addr = _make_addr()
    cache.put("market & powell, san francisco", addr)
    # Query with different casing should hit
    assert cache.get("Market & Powell, San Francisco") == addr  # noqa: S101 pytest assertion
    assert cache.get("MARKET & POWELL, SAN FRANCISCO") == addr  # noqa: S101 pytest assertion


def test_key_derivation_is_whitespace_insensitive(tmp_path: Path) -> None:
    cache = GeocodeCache(fixtures_dir=tmp_path)
    addr = _make_addr()
    cache.put("  Market & Powell  ", addr)
    # Leading/trailing whitespace stripped
    assert cache.get("Market & Powell") == addr  # noqa: S101 pytest assertion


def test_round_trip_preserves_all_fields_including_provider_metadata(tmp_path: Path) -> None:
    cache = GeocodeCache(fixtures_dir=tmp_path)
    addr = _make_addr(
        raw="123 Main St",
        formatted="123 Main St, Anytown, CA",
        lat=34.0522,
        lng=-118.2437,
        confidence=0.3,
        provider_metadata={
            "alternates": [
                {"formatted": "123 Main Street, Anytown, CA", "lat": 34.0523, "lng": -118.2438},
                {"formatted": "123 Main Ave, Anytown, CA", "lat": 34.0510, "lng": -118.2440},
            ]
        },
    )
    cache.put("123 Main St", addr)
    result = cache.get("123 Main St")
    assert result is not None  # noqa: S101 pytest assertion
    assert result.raw == addr.raw  # noqa: S101 pytest assertion
    assert result.formatted == addr.formatted  # noqa: S101 pytest assertion
    assert result.lat == addr.lat  # noqa: S101 pytest assertion
    assert result.lng == addr.lng  # noqa: S101 pytest assertion
    assert result.confidence == addr.confidence  # noqa: S101 pytest assertion
    assert result.provider_metadata == addr.provider_metadata  # noqa: S101 pytest assertion


def test_cache_file_is_valid_json(tmp_path: Path) -> None:
    """The on-disk format is valid JSON (AddressRef.model_dump_json output)."""
    cache = GeocodeCache(fixtures_dir=tmp_path)
    addr = _make_addr()
    cache.put("test query", addr)
    files = list(tmp_path.iterdir())
    assert len(files) == 1  # noqa: S101 pytest assertion
    raw = files[0].read_text()
    parsed = json.loads(raw)
    assert parsed["formatted"] == addr.formatted  # noqa: S101 pytest assertion


def test_cache_file_name_is_stable_sha1_of_normalised_query(tmp_path: Path) -> None:
    """Existing on-disk fixtures must keep matching: the key is sha1("test query")."""
    cache = GeocodeCache(fixtures_dir=tmp_path)
    cache.put("  Test Query ", _make_addr())
    assert [p.name for p in tmp_path.iterdir()] == ["4cbdfc765c265da260dc725d94660db139f2edac.json"]  # noqa: S101 pytest assertion


def test_overwrite_updates_existing_cache_entry(tmp_path: Path) -> None:
    cache = GeocodeCache(fixtures_dir=tmp_path)
    addr1 = _make_addr(confidence=0.5)
    addr2 = _make_addr(confidence=0.99)
    cache.put("some query", addr1)
    cache.put("some query", addr2)
    result = cache.get("some query")
    assert result is not None  # noqa: S101 pytest assertion
    assert result.confidence == 0.99  # noqa: S101 pytest assertion
