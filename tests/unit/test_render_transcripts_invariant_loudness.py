"""Guard against silent invariant FAILs in `render-transcripts`.

Background: P3 shipped two scenarios where `make transcripts` quietly
produced markdown with `[FAIL]` invariants because `--assert-invariants`
was opt-in and invariants were only printed under that flag.  This hid a
regression across 5+ runs.  The CLI default is now "loud and strict":
results are always printed, and any FAIL exits non-zero unless the caller
explicitly passes `--no-assert-invariants`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agentic_cab.__main__ import app


def _write_scenario(scenarios_dir: Path) -> None:
    scenarios_dir.mkdir()
    scn = {
        "name": "fake",
        "clock": {"start": "2026-05-19T12:00:00+00:00", "mode": "frozen"},
        "sim": {"rng_seed": 0},
        "confirmation": {"channel": "scripted", "script": []},
        "user_turns": ["hi"],
        "invariants": [{"kind": "no_double_booking"}],
    }
    (scenarios_dir / "99_fake.yaml").write_text(yaml.safe_dump(scn))


def test_invariant_results_always_printed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Per-scenario PASS/FAIL lines must print even without --assert-invariants."""
    monkeypatch.chdir(tmp_path)
    _write_scenario(tmp_path / "scenarios")

    async def _fake_run_scenario(*_args: object, **_kwargs: object) -> str:
        return "01FAKE"

    monkeypatch.setattr("agentic_cab.__main__.run_scenario", _fake_run_scenario)
    monkeypatch.setattr(
        "agentic_cab.__main__.JSONLActionLog",
        lambda log_dir: type("L", (), {"read": staticmethod(lambda _sid: [])})(),
    )
    monkeypatch.setattr(
        "agentic_cab.__main__.evaluate",
        lambda _rows, _inv: [("no_double_booking", True, {})],
    )
    monkeypatch.setattr("agentic_cab.__main__.render", lambda **_kw: "stub")

    result = CliRunner().invoke(app, ["render-transcripts", "--no-assert-invariants"])
    assert result.exit_code == 0, result.output
    assert "PASS no_double_booking" in result.output


def test_default_exits_nonzero_on_invariant_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default invocation (no flag) must exit 1 when any invariant FAILs."""
    monkeypatch.chdir(tmp_path)
    _write_scenario(tmp_path / "scenarios")

    async def _fake_run_scenario(*_args: object, **_kwargs: object) -> str:
        return "01FAKE"

    monkeypatch.setattr("agentic_cab.__main__.run_scenario", _fake_run_scenario)
    monkeypatch.setattr(
        "agentic_cab.__main__.JSONLActionLog",
        lambda log_dir: type("L", (), {"read": staticmethod(lambda _sid: [])})(),
    )
    monkeypatch.setattr(
        "agentic_cab.__main__.evaluate",
        lambda _rows, _inv: [("no_double_booking", False, {})],
    )
    monkeypatch.setattr("agentic_cab.__main__.render", lambda **_kw: "stub")

    result = CliRunner().invoke(app, ["render-transcripts"])
    assert result.exit_code == 1, result.output
    assert "FAIL no_double_booking" in result.output


def test_no_assert_invariants_lets_failures_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--no-assert-invariants` renders even when invariants FAIL (escape hatch)."""
    monkeypatch.chdir(tmp_path)
    _write_scenario(tmp_path / "scenarios")

    async def _fake_run_scenario(*_args: object, **_kwargs: object) -> str:
        return "01FAKE"

    monkeypatch.setattr("agentic_cab.__main__.run_scenario", _fake_run_scenario)
    monkeypatch.setattr(
        "agentic_cab.__main__.JSONLActionLog",
        lambda log_dir: type("L", (), {"read": staticmethod(lambda _sid: [])})(),
    )
    monkeypatch.setattr(
        "agentic_cab.__main__.evaluate",
        lambda _rows, _inv: [("no_double_booking", False, {})],
    )
    monkeypatch.setattr("agentic_cab.__main__.render", lambda **_kw: "stub")

    result = CliRunner().invoke(app, ["render-transcripts", "--no-assert-invariants"])
    assert result.exit_code == 0, result.output
    assert "FAIL no_double_booking" in result.output
