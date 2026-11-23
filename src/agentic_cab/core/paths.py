"""Session-scoped file path builders that refuse to leave their base directory.

Every `logs/<session_id>...` path goes through here. The final path is resolved and
must sit directly inside the resolved base dir, so absolute paths, `..` segments,
nested segments and symlink escapes all fail closed.
"""

from __future__ import annotations

from pathlib import Path


class UnsafePathError(ValueError):
    """A session-derived path resolved outside its base directory."""


def _contained(base: Path, filename: str) -> Path:
    resolved_base = base.resolve()
    resolved = (resolved_base / filename).resolve()
    if resolved.parent != resolved_base:
        raise UnsafePathError(f"path escapes base directory: {filename!r}")
    return resolved


def session_log_path(base: Path, session_id: str) -> Path:
    return _contained(base, f"{session_id}.jsonl")


def session_chat_path(base: Path, session_id: str) -> Path:
    return _contained(base, f"{session_id}.chat.jsonl")
