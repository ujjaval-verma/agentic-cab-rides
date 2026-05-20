"""Deterministic fingerprint of an Option snapshot. See spec §4 + CLAUDE.md inv. 3.

Pydantic BaseModel only — raw dicts are rejected to avoid the datetime serialization
divergence between str(datetime) and model_dump(mode='json'). Callers must construct
a Pydantic model first.

Strings are NFC-normalised in canonical_json so that geocoder-returned NFD text and
keyboard-input NFC text of the same address hash to the same digest.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any, cast

from pydantic import BaseModel


def _nfc(obj: object) -> object:
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, dict):
        d = cast(dict[object, object], obj)
        return {_nfc(k): _nfc(v) for k, v in d.items()}
    if isinstance(obj, list):
        lst = cast(list[object], obj)
        return [_nfc(x) for x in lst]
    return obj


def canonical_json(obj: Any) -> str:
    """JSON with NFC-normalised strings, sorted keys, no whitespace."""
    return json.dumps(_nfc(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(model: BaseModel) -> str:
    """Return sha256 hex digest of the canonical JSON of a Pydantic BaseModel.

    Serialises via model_dump(mode='json') so datetimes become ISO-8601 strings
    and nested models are fully expanded before hashing. Strings are NFC-normalised
    so that geocoder-returned NFD text and keyboard-input NFC text of the same
    address hash to the same digest.

    Raw dicts are not accepted — use a Pydantic model to avoid datetime serialization
    divergence (str(datetime) vs ISO-8601 from model_dump).
    """
    return hashlib.sha256(canonical_json(model.model_dump(mode="json")).encode("utf-8")).hexdigest()
