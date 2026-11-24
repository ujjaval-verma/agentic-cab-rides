from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
_ULID = "01KS5HMK9QJZ8PCJ92B9VM7BRA"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("replay", ROOT / "scripts" / "replay.py")
    assert spec is not None  # noqa: S101 pytest assertion
    assert spec.loader is not None  # noqa: S101 pytest assertion
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sid: str) -> int:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["replay.py", sid])
    return _load_script().main()


@pytest.mark.parametrize("bad", ["../x", "/etc/passwd", _ULID.lower()])
def test_replay_rejects_invalid_session_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    bad: str,
) -> None:
    (tmp_path / "x.jsonl").write_text("{}\n")
    assert _run(monkeypatch, tmp_path, bad) == 2  # noqa: S101 pytest assertion
    captured = capsys.readouterr()
    assert "invalid session_id" in captured.err  # noqa: S101 pytest assertion
    assert captured.out == ""  # noqa: S101 pytest assertion


def test_replay_valid_ulid_without_log_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _run(monkeypatch, tmp_path, _ULID) == 1  # noqa: S101 pytest assertion


def test_replay_valid_ulid_with_log_exits_0(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    row = {"session_id": _ULID, "kind": "requested"}
    (logs / f"{_ULID}.jsonl").write_text(json.dumps(row) + "\n")
    assert _run(monkeypatch, tmp_path, _ULID) == 0  # noqa: S101 pytest assertion
    assert _ULID in capsys.readouterr().out  # noqa: S101 pytest assertion


def test_makefile_replay_quotes_session() -> None:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = re.search(r"^replay:.*\n((?:\t.*\n)+)", text, re.MULTILINE)
    assert recipe is not None  # noqa: S101 pytest assertion
    assert 'scripts/replay.py "$(SESSION)"' in recipe.group(1)  # noqa: S101 pytest assertion
