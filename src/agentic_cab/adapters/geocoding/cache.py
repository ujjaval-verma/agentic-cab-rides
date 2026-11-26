"""GeocodeCache — layered file-system cache for geocoded addresses.

Storage layout (one JSON file per query, in every layer):
  <dir>/<sha1(query.strip().lower())>.json

Layers:
  cache_dir       writable; `put` writes here only. Live runs point it under the
                  gitignored data dir (default `data/geocode`).
  read_only_dirs  seed layers consulted after `cache_dir`, in order. Never
                  written, never created — the committed `fixtures/geocode` is
                  read-only at runtime.

Each file contains AddressRef.model_dump_json() output (Pydantic v2 serialization).

Key derivation is deterministic: hashlib.sha1 (cache, not crypto), query
lowercased and stripped. This is stable across process restarts.

See spec §10, CLAUDE.md invariants 7 (persistence split) and 8 (PII redaction —
raw query text is never logged; only the hash prefix may appear if needed).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Self

from agentic_cab.core.types import AddressRef


class GeocodeCache:
    """Read/write cache: one writable dir plus optional read-only seed dirs."""

    def __init__(self: Self, cache_dir: Path, read_only_dirs: Sequence[Path] = ()) -> None:
        self._dir = cache_dir
        self._read_only_dirs = tuple(read_only_dirs)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self: Self, query: str) -> AddressRef | None:
        """Return the cached AddressRef for *query* (writable layer first), or None."""
        name = self._name_for(query)
        for layer in (self._dir, *self._read_only_dirs):
            path = layer / name
            if path.exists():
                return AddressRef.model_validate_json(path.read_text())
        return None

    def put(self: Self, query: str, ref: AddressRef) -> None:
        """Write *ref* to the writable layer under the key derived from *query*."""
        (self._dir / self._name_for(query)).write_text(ref.model_dump_json())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _name_for(query: str) -> str:
        key = hashlib.sha1(query.strip().lower().encode(), usedforsecurity=False).hexdigest()
        return f"{key}.json"
