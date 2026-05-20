from agentic_cab.core.ids import (
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
