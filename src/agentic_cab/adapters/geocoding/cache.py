"""GeocodeCache — file-system fixture cache for geocoded addresses.

Storage layout:
  <fixtures_dir>/<sha1(query.strip().lower())>.json

Each file contains AddressRef.model_dump_json() output (Pydantic v2 serialization).

Key derivation is deterministic: hashlib.sha1 (cache, not crypto), query
lowercased and stripped. This is stable across process restarts.

See spec §10, CLAUDE.md invariants 7 (persistence split) and 8 (PII redaction —
raw query text is never logged; only the hash prefix may appear if needed).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Self

from agentic_cab.core.types import AddressRef


class GeocodeCache:
    """Read/write cache backed by one JSON file per query."""

    def __init__(self: Self, fixtures_dir: Path) -> None:
        self._dir = fixtures_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self: Self, query: str) -> AddressRef | None:
        """Return the cached AddressRef for *query*, or None on miss."""
        path = self._path_for(query)
        if not path.exists():
            return None
        return AddressRef.model_validate_json(path.read_text())

    def put(self: Self, query: str, ref: AddressRef) -> None:
        """Write *ref* to the cache under the key derived from *query*."""
        path = self._path_for(query)
        path.write_text(ref.model_dump_json())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _path_for(self: Self, query: str) -> Path:
        key = hashlib.sha1(query.strip().lower().encode()).hexdigest()  # cache key, not crypto
        return self._dir / f"{key}.json"
