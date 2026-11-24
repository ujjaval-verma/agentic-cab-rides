"""Thread-safety test for SqliteStateStore with check_same_thread=False.

Closes the FOLLOWUPS.md entry "SqliteStateStore thread-safety for FastAPI web path."
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.core.types import Draft, Money, Option, RideOptionDetails


def _opt(option_id: str = "opt-1") -> Option:
    return Option(
        option_id=option_id,
        provider="uber-sim",
        title="UberX",
        price=Money(amount_cents=1500, currency="USD"),
        eta_seconds=180,
        when=None,
        details=RideOptionDetails(vehicle_class="uberx", capacity=4),
        expires_at=datetime(2026, 5, 19, 13, 0, tzinfo=UTC),
    )


def _draft(draft_id: str) -> Draft:
    return Draft(
        draft_id=draft_id,
        option_snapshot=_opt(),
        fingerprint="f" * 64,
        created_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
        expires_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC) + timedelta(seconds=60),
    )


@pytest.mark.asyncio
async def test_concurrent_writes_succeed_under_lock(tmp_path: Path) -> None:
    """Two concurrent asyncio.to_thread writers both succeed without corruption."""
    store = SqliteStateStore(db_path=tmp_path / "state.db", check_same_thread=False)
    store.apply_schema()

    draft_a = _draft("draft-A")
    draft_b = _draft("draft-B")

    # Drive concurrent writes from two threads via asyncio.to_thread.
    await asyncio.gather(
        asyncio.to_thread(store.put_draft, draft_a),
        asyncio.to_thread(store.put_draft, draft_b),
    )

    # Both drafts must be retrievable.
    result_a = store.get_draft("draft-A")
    result_b = store.get_draft("draft-B")

    assert result_a is not None  # noqa: S101 pytest assertion
    assert result_a.draft_id == "draft-A"  # noqa: S101 pytest assertion
    assert result_b is not None  # noqa: S101 pytest assertion
    assert result_b.draft_id == "draft-B"  # noqa: S101 pytest assertion
