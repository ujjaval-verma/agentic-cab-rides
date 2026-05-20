"""Dry-run invariant tests for scenario 04_surge_on_confirm. T28 Commit 6.

These tests exercise the new invariant checkers with synthetic action-log rows
that mirror what the scenario produces — no LLM call required.

The scenario sets force_surge_on_confirm=True so confirm_booking fires
verified(rejected, reason=price_changed) and returns price_changed with a
new_draft_id. Invariant 6 asserts no executed row follows.
"""

from __future__ import annotations

from agentic_cab.scenarios.invariants import (
    check_no_double_booking,
    check_no_executed_after_price_changed,
    check_price_changed_rejected,
)


def _price_changed_rows(action_id: str = "A") -> list[dict]:
    """Synthetic log rows for a price_changed rejection path."""
    return [
        {
            "action_id": action_id,
            "kind": "requested",
            "tool": "confirm_booking",
        },
        {
            "action_id": action_id,
            "kind": "verified",
            "tool": "confirm_booking",
            "result": "rejected",
            "reason": "price_changed",
            "checks": {
                "draft_found": True,
                "fingerprint_match": True,
                "requote_price_drift_cents": 840,
                "new_draft_id": "01JABCDEFGHIJKLMNOPQRSTUV",
            },
        },
        {
            "action_id": action_id,
            "kind": "outcome",
            "tool": "confirm_booking",
            "status": "price_changed",
            "result": {
                "new_draft_id": "01JABCDEFGHIJKLMNOPQRSTUV",
                "new_price_cents": 1260,
            },
        },
    ]


def test_price_changed_rejected_passes_on_valid_rows() -> None:
    rows = _price_changed_rows()
    assert check_price_changed_rejected(rows, tool="confirm_booking") is True


def test_price_changed_rejected_fails_when_no_such_action() -> None:
    rows = [
        {
            "action_id": "A",
            "kind": "requested",
            "tool": "confirm_booking",
        },
        {
            "action_id": "A",
            "kind": "verified",
            "result": "ok",
            "checks": {
                "fingerprint_match": True,
                "confirmation": {"approver": "scripted", "approved": True},
            },
        },
        {"action_id": "A", "kind": "executed"},
        {"action_id": "A", "kind": "outcome", "status": "confirmed"},
    ]
    # Happy path — no price_changed action exists
    assert check_price_changed_rejected(rows, tool="confirm_booking") is False


def test_no_executed_after_price_changed_passes_when_no_executed() -> None:
    rows = _price_changed_rows()
    assert check_no_executed_after_price_changed(rows, tool="confirm_booking") is True


def test_no_executed_after_price_changed_fails_when_executed_present() -> None:
    """Violation: executed row appears after a price_changed outcome."""
    rows = [
        *_price_changed_rows(),
        {
            "action_id": "A",
            "kind": "executed",
            "tool": "confirm_booking",
            "idempotency_key": "K",
            "replayed": False,
        },
    ]
    assert check_no_executed_after_price_changed(rows, tool="confirm_booking") is False


def test_no_double_booking_still_passes_with_price_changed_rows() -> None:
    """price_changed path has no executed rows → no_double_booking trivially passes."""
    rows = _price_changed_rows()
    assert check_no_double_booking(rows) is True


def test_price_changed_checker_ignores_wrong_tool() -> None:
    """Checker must be scoped to the specified tool."""
    rows = _price_changed_rows()
    # Checking against a different tool name returns False (no matching action)
    assert check_price_changed_rejected(rows, tool="cancel_booking") is False


def test_scenario_04_yaml_is_valid() -> None:
    """Smoke-test: scenario YAML loads and has required keys."""
    from pathlib import Path

    import yaml

    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "scenarios" / "04_surge_on_confirm.yaml"
    assert path.exists(), f"Missing scenario file: {path}"
    scn = yaml.safe_load(path.read_text())
    assert scn["name"] == "04_surge_on_confirm"
    assert scn["sim"]["surge_curve"]["force_surge_on_confirm"] is True
    assert "price_changed_rejected" in [inv["kind"] for inv in scn["invariants"]]
    assert "no_executed_after_price_changed" in [inv["kind"] for inv in scn["invariants"]]
