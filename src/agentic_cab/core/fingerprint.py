"""Deterministic fingerprint of an Option snapshot. See spec §4 + CLAUDE.md inv. 3."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def canonical_json(obj: Any) -> str:
    """JSON with sorted keys, no whitespace, default str fallback for datetimes."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=False,
    )


def fingerprint(model_or_dict: BaseModel | dict[str, Any]) -> str:
    """Return sha256 hex digest of the canonical JSON of a model or dict.

    For BaseModel instances, serialises via model_dump(mode='json') so that
    datetimes become ISO strings and nested models are fully expanded before
    hashing.  For plain dicts the dict is hashed directly (datetimes still
    covered by the default=str fallback in canonical_json).
    """
    if isinstance(model_or_dict, BaseModel):
        payload: Any = model_or_dict.model_dump(mode="json")
    else:
        payload = model_or_dict
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
