"""Parameterized validator for `scenarios/*.yaml`.

Catches a scenario added with a typo'd invariant kind, a missing
required schema field, or a non-list `user_turns`.  Runs in
`make test-fast` (no LLM, no provider, no network).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentic_cab.scenarios.invariants import CHECKERS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCENARIOS_DIR = _REPO_ROOT / "scenarios"

_REQUIRED_TOP_LEVEL_KEYS = (
    "name",
    "clock",
    "sim",
    "confirmation",
    "user_turns",
    "invariants",
)


def _scenario_paths() -> list[Path]:
    return sorted(_SCENARIOS_DIR.glob("*.yaml"))


def _scenario_ids(paths: list[Path]) -> list[str]:
    return [p.name for p in paths]


_PATHS = _scenario_paths()


@pytest.mark.parametrize("scenario_path", _PATHS, ids=_scenario_ids(_PATHS))
def test_scenario_has_required_top_level_keys(scenario_path: Path) -> None:
    scn = yaml.safe_load(scenario_path.read_text())
    assert isinstance(scn, dict), f"{scenario_path.name}: YAML root must be a mapping"
    missing = [k for k in _REQUIRED_TOP_LEVEL_KEYS if k not in scn]
    assert not missing, f"{scenario_path.name} missing required keys: {missing}"


@pytest.mark.parametrize("scenario_path", _PATHS, ids=_scenario_ids(_PATHS))
def test_scenario_user_turns_is_nonempty_string_list(scenario_path: Path) -> None:
    scn = yaml.safe_load(scenario_path.read_text())
    user_turns = scn.get("user_turns")
    assert isinstance(user_turns, list), f"{scenario_path.name}: user_turns must be a list"
    assert user_turns, f"{scenario_path.name}: user_turns must be non-empty"
    assert all(
        isinstance(t, str) for t in user_turns
    ), f"{scenario_path.name}: every user_turns entry must be a string"


@pytest.mark.parametrize("scenario_path", _PATHS, ids=_scenario_ids(_PATHS))
def test_scenario_confirmation_has_channel(scenario_path: Path) -> None:
    scn = yaml.safe_load(scenario_path.read_text())
    conf = scn.get("confirmation")
    assert isinstance(conf, dict), f"{scenario_path.name}: confirmation must be a mapping"
    assert "channel" in conf, f"{scenario_path.name}: confirmation.channel is required"


def _unknown_invariant_kinds(scn: dict) -> list[str]:
    """Return the list of invariant.kind values in ``scn`` not present in CHECKERS."""
    invariants = scn.get("invariants") or []
    return [inv["kind"] for inv in invariants if inv["kind"] not in CHECKERS]


@pytest.mark.parametrize("scenario_path", _PATHS, ids=_scenario_ids(_PATHS))
def test_scenario_invariant_kinds_are_known(scenario_path: Path) -> None:
    scn = yaml.safe_load(scenario_path.read_text())
    unknown = _unknown_invariant_kinds(scn)
    assert not unknown, (
        f"{scenario_path.name} references unknown invariant kinds: {unknown}. "
        f"Known kinds: {sorted(CHECKERS)}"
    )


def test_scenarios_dir_is_nonempty() -> None:
    """Sanity check — without this the parametrized tests run zero cases silently."""
    assert _PATHS, "no scenarios found under scenarios/"


def test_validator_catches_unknown_invariant_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scenario referencing a non-existent invariant kind must fail the validator."""
    bad_dir = tmp_path / "scenarios"
    bad_dir.mkdir()
    bad = {
        "name": "bad",
        "clock": {"start": "2026-05-19T12:00:00+00:00", "mode": "frozen"},
        "sim": {"rng_seed": 0},
        "confirmation": {"channel": "scripted", "script": []},
        "user_turns": ["hi"],
        "invariants": [{"kind": "no_such_checker"}],
    }
    bad_path = bad_dir / "99_bad.yaml"
    bad_path.write_text(yaml.safe_dump(bad))

    scn = yaml.safe_load(bad_path.read_text())
    unknown = _unknown_invariant_kinds(scn)
    assert unknown == ["no_such_checker"]
