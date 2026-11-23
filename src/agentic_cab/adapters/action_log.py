"""JSONL action log, per-session file. See spec §8 + CLAUDE.md inv. 6, 7, 8."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Self, cast

from agentic_cab.core.paths import session_log_path

# Tightened phone matcher: must look like an actual phone number, not an ISO
# date (`2026-05-19`) or a digit run inside a ULID / hex idempotency key.
# Accepts three shapes:
#   1. E.164-ish with explicit "+" country code: `+1 555-123-4567`
#   2. North-American parenthesised area code: `(555) 123-4567`
#   3. Plain 3-3-4 with separators: `555-123-4567`, `555.123.4567`, `555 123 4567`
# A date like `2026-05-19` has groups of 4-2-2 (not 3-3-4) and so cannot match.
_PHONE = re.compile(
    r"\+\d[\d\-\.\s\(\)]{7,}\d"
    r"|\(\d{3}\)[\s\-\.]?\d{3}[\s\-\.]?\d{4}"
    r"|\b\d{3}[\s\-\.]\d{3}[\s\-\.]\d{4}\b"
)
# Match sequences of 2+ capitalised words that look like proper names
# (e.g. "Sarah Smith", "John van der Berg").  The regex stops at hyphens
# used as separators (plate numbers like "ABC123" are not capitalised words).
_NAME = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")
# Keys whose string values are raw address text and must be redacted (inv. 8).
_ADDRESS_STRING_KEYS = frozenset({"pickup_address", "dropoff_address", "query"})


def _redact_address(addr: dict[str, Any]) -> dict[str, Any]:
    """Drop raw text; keep only formatted/lat/lng/confidence (spec §8 PII rule)."""
    return {
        "formatted": addr.get("formatted", ""),
        "lat": addr.get("lat"),
        "lng": addr.get("lng"),
        "confidence": addr.get("confidence"),
    }


def _redact_summary_title(value: str) -> str:
    """Apply name + phone redaction to a summary_title string (spec §8 + inv. 8).

    summary_title may contain driver names or vehicle plate numbers from a real provider.
    Strip them at the log boundary rather than trusting each adapter to sanitise.
    """
    value = _NAME.sub("[redacted-name]", value)
    return _PHONE.sub("[redacted-phone]", value)


def redact(payload: Any) -> Any:
    """Walk a JSON-shaped payload, redact PII at known address fields + phone-shaped substrings."""
    if isinstance(payload, dict):
        typed = cast(dict[str, Any], payload)
        out: dict[str, Any] = {}
        for k, v in typed.items():
            if k in ("pickup", "dropoff") and isinstance(v, dict) and "raw" in v:
                out[k] = _redact_address(cast(dict[str, Any], v))
            elif k == "summary_title" and isinstance(v, str):
                out[k] = _redact_summary_title(v)
            elif k in _ADDRESS_STRING_KEYS and isinstance(v, str):
                out[k] = "[redacted-address]"
            else:
                out[k] = redact(v)
        return out
    if isinstance(payload, list):
        return [redact(x) for x in cast(list[Any], payload)]
    if isinstance(payload, str):
        return _PHONE.sub("[redacted-phone]", payload)
    return payload


class JSONLActionLog:
    def __init__(self: Self, log_dir: Path) -> None:
        self._dir = Path(log_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self: Self, session_id: str) -> Path:
        return session_log_path(self._dir, session_id)

    def emit(self: Self, row: dict[str, Any]) -> None:
        sid = row.get("session_id")
        if sid is None:
            raise ValueError("row missing session_id")
        redacted = redact(row)
        with self._path(sid).open("a", encoding="utf-8") as f:
            f.write(json.dumps(redacted, separators=(",", ":"), default=str) + "\n")

    def read(self: Self, session_id: str) -> list[dict[str, Any]]:
        path = self._path(session_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
