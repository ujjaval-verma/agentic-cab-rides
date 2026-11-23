import re
from pathlib import Path

import pytest

from agentic_cab.core.paths import UnsafePathError, session_chat_path, session_log_path

_ULID = "01KS5HMK9QJZ8PCJ92B9VM7BRA"


def test_valid_ulid_paths(tmp_path: Path) -> None:
    assert session_log_path(tmp_path, _ULID) == tmp_path.resolve() / f"{_ULID}.jsonl"
    assert session_chat_path(tmp_path, _ULID) == tmp_path.resolve() / f"{_ULID}.chat.jsonl"


@pytest.mark.parametrize("builder", [session_log_path, session_chat_path])
@pytest.mark.parametrize("bad", ["/tmp/x", "../escape", "a/../../b", "a/b"])
def test_escaping_ids_rejected(tmp_path: Path, builder, bad: str) -> None:
    with pytest.raises(UnsafePathError):
        builder(tmp_path, bad)


@pytest.mark.parametrize("builder", [session_log_path, session_chat_path])
def test_symlink_escape_rejected(tmp_path: Path, builder) -> None:
    base = tmp_path / "logs"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (base / "link").symlink_to(outside)
    with pytest.raises(UnsafePathError):
        builder(base, "link/x")
    (base / f"{_ULID}.jsonl").symlink_to(outside / "target.jsonl")
    (base / f"{_ULID}.chat.jsonl").symlink_to(outside / "target.chat.jsonl")
    with pytest.raises(UnsafePathError):
        builder(base, _ULID)


def test_no_adhoc_session_path_joins() -> None:
    root = Path(__file__).resolve().parents[2]
    allowed = root / "src" / "agentic_cab" / "core" / "paths.py"
    pattern = re.compile(r"\{(?:session_id|sid)\}\.")
    offenders = [
        str(path.relative_to(root))
        for folder in ("src", "scripts")
        for path in (root / folder).rglob("*.py")
        if path != allowed and pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_unsafe_path_error_is_value_error() -> None:
    assert issubclass(UnsafePathError, ValueError)
