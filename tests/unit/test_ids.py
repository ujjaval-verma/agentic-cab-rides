from agentic_cab.core.ids import (
    cancel_idempotency_key,
    confirm_idempotency_key,
    new_action_id,
    new_draft_id,
    new_idempotency_key,
    new_session_id,
    new_turn_id,
)


def test_ids_are_ulids():
    for factory in (new_session_id, new_turn_id, new_action_id, new_draft_id, new_idempotency_key):
        value = factory()
        assert isinstance(value, str)
        assert len(value) == 26  # ULID canonical length


def test_ids_are_sortable_by_creation():
    a = new_session_id()
    b = new_session_id()
    assert a < b  # ULIDs are time-monotonic at millisecond resolution


# --- confirm_idempotency_key helpers (new signature: intent_hash, generation, nonce) ---


def test_confirm_idempotency_key_is_deterministic() -> None:
    key_a = confirm_idempotency_key("ih-abc", 0, None)
    key_b = confirm_idempotency_key("ih-abc", 0, None)
    assert key_a == key_b
    assert isinstance(key_a, str)
    assert len(key_a) == 64


def test_confirm_idempotency_key_changes_with_intent_hash() -> None:
    key_a = confirm_idempotency_key("ih-abc", 0, None)
    key_b = confirm_idempotency_key("ih-xyz", 0, None)
    assert key_a != key_b


def test_confirm_idempotency_key_changes_with_generation() -> None:
    key_a = confirm_idempotency_key("ih-abc", 0, None)
    key_b = confirm_idempotency_key("ih-abc", 1, None)
    assert key_a != key_b


def test_confirm_idempotency_key_changes_with_nonce() -> None:
    key_a = confirm_idempotency_key("ih-abc", 0, None)
    key_b = confirm_idempotency_key("ih-abc", 0, "nonce-x")
    assert key_a != key_b


# --- cancel_idempotency_key helpers ---


def test_cancel_idempotency_key_is_deterministic() -> None:
    key_a = cancel_idempotency_key("booking-123", 500, "confirmed")
    key_b = cancel_idempotency_key("booking-123", 500, "confirmed")
    assert key_a == key_b
    assert isinstance(key_a, str)
    assert len(key_a) == 64  # sha256 hex digest


def test_cancel_idempotency_key_changes_with_fee() -> None:
    key_a = cancel_idempotency_key("booking-123", 500, "confirmed")
    key_b = cancel_idempotency_key("booking-123", 1000, "confirmed")
    assert key_a != key_b


def test_cancel_idempotency_key_changes_with_status() -> None:
    key_a = cancel_idempotency_key("booking-123", 500, "confirmed")
    key_b = cancel_idempotency_key("booking-123", 500, "in_progress")
    assert key_a != key_b


def test_cancel_idempotency_key_changes_with_booking_id() -> None:
    key_a = cancel_idempotency_key("booking-aaa", 500, "confirmed")
    key_b = cancel_idempotency_key("booking-bbb", 500, "confirmed")
    assert key_a != key_b
