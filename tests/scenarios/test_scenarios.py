"""Pure-function tests for invariant checkers (deterministic, no LLM call)."""

from agentic_cab.scenarios.invariants import (
    check_fingerprint_verified_before_execute,
    check_four_row_pattern,
    check_gate_invoked_for_tool,
    check_no_double_booking,
)


def test_four_row_happy_path_passes() -> None:
    rows = [
        {"action_id": "A", "kind": "requested", "tool": "confirm_booking"},
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
        {"action_id": "A", "kind": "outcome", "status": "ok"},
    ]
    assert check_four_row_pattern(rows, tool="confirm_booking") is True
    assert check_fingerprint_verified_before_execute(rows, tool="confirm_booking") is True
    assert check_gate_invoked_for_tool(rows, tool="confirm_booking") is True


def test_rejected_path_does_not_require_executed() -> None:
    rows = [
        {"action_id": "A", "kind": "requested", "tool": "confirm_booking"},
        {
            "action_id": "A",
            "kind": "verified",
            "result": "rejected",
            "reason": "fingerprint mismatch",
            "checks": {"fingerprint_match": False},
        },
        {"action_id": "A", "kind": "outcome", "status": "rejected"},
    ]
    assert check_four_row_pattern(rows, tool="confirm_booking") is True


def test_double_booking_detected() -> None:
    rows = [
        {"action_id": "A", "kind": "executed", "idempotency_key": "K", "replayed": False},
        {"action_id": "B", "kind": "executed", "idempotency_key": "K", "replayed": False},
    ]
    assert check_no_double_booking(rows) is False


def test_replayed_executed_does_not_count_as_double_booking() -> None:
    rows = [
        {"action_id": "A", "kind": "executed", "idempotency_key": "K", "replayed": False},
        {"action_id": "B", "kind": "executed", "idempotency_key": "K", "replayed": True},
    ]
    assert check_no_double_booking(rows) is True
