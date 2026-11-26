"""Runtime settings: where an instance keeps its files and which port it serves.

`RuntimeSettings.from_env(environ)` is pure: it reads only the mapping it is
given. Composition roots (`__main__.py`, `web/app.py::create_app`,
`agent/chat.py::run_repl`, `scripts/replay.py`) pass `os.environ` at call time;
nothing here reads the process environment at import (invariant 9). Tools and
adapters never import this module — they take plain `Path`s (invariant 1).

Variables (an empty value counts as unset):
  AGENTIC_CAB_DATA_DIR           SQLite state + live geocode cache   (default: data)
  AGENTIC_CAB_LOG_DIR            action-log JSONL                    (default: logs)
  AGENTIC_CAB_CHAT_DIR           chat-history JSONL                  (default: the log dir)
  AGENTIC_CAB_GEOCODE_CACHE_DIR  writable geocode cache              (default: <data dir>/geocode)
  AGENTIC_CAB_WEB_PORT           web server port, 1-65535            (default: 8000)

The committed `fixtures/geocode` is a read-only seed layer; the writable cache
may not be it or sit inside it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Self

DATA_DIR_ENV = "AGENTIC_CAB_DATA_DIR"
LOG_DIR_ENV = "AGENTIC_CAB_LOG_DIR"
CHAT_DIR_ENV = "AGENTIC_CAB_CHAT_DIR"
GEOCODE_CACHE_DIR_ENV = "AGENTIC_CAB_GEOCODE_CACHE_DIR"
WEB_PORT_ENV = "AGENTIC_CAB_WEB_PORT"

DEFAULT_DATA_DIR = Path("data")
DEFAULT_LOG_DIR = Path("logs")
DEFAULT_GEOCODE_FIXTURES_DIR = Path("fixtures/geocode")
DEFAULT_WEB_PORT = 8000
_GEOCODE_SUBDIR = "geocode"
_MIN_PORT = 1
_MAX_PORT = 65535


def _get(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name, "").strip()
    return value or None


def _parse_port(raw: str | None) -> int:
    if raw is None:
        return DEFAULT_WEB_PORT
    try:
        port = int(raw)
    except ValueError:
        raise ValueError(f"{WEB_PORT_ENV} must be an integer, got {raw!r}") from None
    if not _MIN_PORT <= port <= _MAX_PORT:
        raise ValueError(f"{WEB_PORT_ENV} must be in {_MIN_PORT}-{_MAX_PORT}, got {port}")
    return port


def _is_within(path: Path, base: Path) -> bool:
    resolved, resolved_base = path.resolve(), base.resolve()
    return resolved == resolved_base or resolved_base in resolved.parents


@dataclass(frozen=True)
class RuntimeSettings:
    """Resolved per-instance paths and port."""

    data_dir: Path
    log_dir: Path
    chat_dir: Path
    geocode_cache_dir: Path
    geocode_fixtures_dir: Path
    web_port: int

    def __post_init__(self: Self) -> None:
        if _is_within(self.geocode_cache_dir, self.geocode_fixtures_dir):
            raise ValueError(
                f"{GEOCODE_CACHE_DIR_ENV} must not be inside the read-only geocode fixtures dir"
            )

    @classmethod
    def from_env(cls: type[Self], environ: Mapping[str, str]) -> Self:
        """Resolve settings from *environ*; unset or empty variables take defaults."""
        data_raw = _get(environ, DATA_DIR_ENV)
        data_dir = Path(data_raw) if data_raw else DEFAULT_DATA_DIR
        log_raw = _get(environ, LOG_DIR_ENV)
        log_dir = Path(log_raw) if log_raw else DEFAULT_LOG_DIR
        chat_raw = _get(environ, CHAT_DIR_ENV)
        cache_raw = _get(environ, GEOCODE_CACHE_DIR_ENV)
        return cls(
            data_dir=data_dir,
            log_dir=log_dir,
            chat_dir=Path(chat_raw) if chat_raw else log_dir,
            geocode_cache_dir=Path(cache_raw) if cache_raw else data_dir / _GEOCODE_SUBDIR,
            geocode_fixtures_dir=DEFAULT_GEOCODE_FIXTURES_DIR,
            web_port=_parse_port(_get(environ, WEB_PORT_ENV)),
        )
