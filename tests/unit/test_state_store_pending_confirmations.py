"""Tests for pending_confirmations table — spec §10a T30 (restart-survival)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.core.ports import ConfirmationRequest


def _req(
    subject_id: str = "draft-pc-1",
    seconds_until_expiry: float = 60.0,
) -> ConfirmationRequest:
    return ConfirmationRequest(
        subject_id=subject_id,
        subject_kind="draft",
        summary="Book UberX $15.00?",
        fingerprint="a" * 64,
        details={"price_cents": 1500},
        expires_at=datetime.now(UTC) + timedelta(seconds=seconds_until_expiry),
    )


@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    s = SqliteStateStore(db_path=tmp_path / "state.db")
    s.apply_schema()
    return s


def test_put_then_list_returns_request(store: SqliteStateStore) -> None:
    req = _req(subject_id="s1")
    store.put_pending_confirmation(
        subject_id=req.subject_id,
        session_id="sess-1",
        request_json=req.model_dump_json(),
        expires_at=req.expires_at,
    )
    results = store.list_pending_confirmations(now=datetime.now(UTC))
    assert len(results) == 1
    assert results[0].session_id == "sess-1"
    assert results[0].request.subject_id == "s1"
    assert results[0].request.summary == req.summary


def test_pop_returns_request_and_removes_it(store: SqliteStateStore) -> None:
    req = _req(subject_id="s2")
    store.put_pending_confirmation(
        subject_id=req.subject_id,
        session_id="sess-2",
        request_json=req.model_dump_json(),
        expires_at=req.expires_at,
    )

    popped = store.pop_pending_confirmation("s2")
    assert popped is not None
    assert popped.subject_id == "s2"

    # Row must be gone now.
    after = store.list_pending_confirmations(now=datetime.now(UTC))
    assert all(r.request.subject_id != "s2" for r in after)


def test_list_filters_expired_entries(store: SqliteStateStore) -> None:
    now = datetime.now(UTC)

    expired_req = _req(subject_id="s-expired", seconds_until_expiry=1.0)
    active_req = _req(subject_id="s-active", seconds_until_expiry=3600.0)

    store.put_pending_confirmation(
        subject_id=expired_req.subject_id,
        session_id="sess-x",
        request_json=expired_req.model_dump_json(),
        expires_at=now - timedelta(seconds=1),  # already expired
    )
    store.put_pending_confirmation(
        subject_id=active_req.subject_id,
        session_id="sess-a",
        request_json=active_req.model_dump_json(),
        expires_at=active_req.expires_at,
    )

    results = store.list_pending_confirmations(now=now)
    ids = {r.request.subject_id for r in results}
    assert "s-active" in ids
    assert "s-expired" not in ids


def test_pop_unknown_subject_returns_none(store: SqliteStateStore) -> None:
    result = store.pop_pending_confirmation("nonexistent-id")
    assert result is None
