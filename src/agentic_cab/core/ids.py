"""ULID factories. One factory per ID kind so call sites read as intent, not 'new_ulid'."""

from __future__ import annotations

from ulid import ULID


def _new() -> str:
    return str(ULID())


def new_session_id() -> str:
    return _new()


def new_turn_id() -> str:
    return _new()


def new_action_id() -> str:
    return _new()


def new_draft_id() -> str:
    return _new()


def new_idempotency_key() -> str:
    return _new()
