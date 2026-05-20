"""Tests for confirm_booking 'in_progress' branch — spec §8 amendment 2026-05-20 + inv. 6.

When claim_or_recall returns status='in_progress', confirm_booking must:
- emit verified with checks.idempotency == 'in_progress' and result == 'rejected'
- emit outcome with status == 'in_progress'
- NOT emit an executed row (invariant 6: rejected → no executed)
- NOT call provider.confirm
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.core.ports import ClaimResult, ConfirmationResult
from agentic_cab.core.types import Booking, BookingEvent, Draft, Money, Option, RideOptionDetails
from agentic_cab.tools.confirm import ConfirmArgs, confirm_booking

# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------


def _opt() -> Option:
    return Option(
        option_id="opt-ip",
        provider="uber-sim",
        title="UberX",
        price=Money(amount_cents=1500, currency="USD"),
        eta_seconds=180,
        when=None,
        details=RideOptionDetails(vehicle_class="uberx", capacity=4),
        expires_at=datetime(2026, 5, 20, 13, 0, tzinfo=UTC),
    )


def _draft(draft_id: str = "D-ip") -> Draft:
    from agentic_cab.core.fingerprint import fingerprint

    opt = _opt()
    return Draft(
        draft_id=draft_id,
        option_snapshot=opt,
        fingerprint=fingerprint(opt),
        created_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
        expires_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC) + timedelta(minutes=5),
    )


class _InProgressStore:
    """StateStore stub that always returns in_progress for claim_or_recall."""

    def __init__(self, draft: Draft) -> None:
        self._draft = draft

    def put_draft(self, draft: Any) -> None: ...

    def get_draft(self, draft_id: str) -> Draft | None:
        return self._draft if draft_id == self._draft.draft_id else None

    def consume_draft(self, draft_id: str, at: Any) -> bool:
        return True

    def get_draft_consumed_at(self, draft_id: str) -> None:
        return None

    def put_booking(self, booking: Booking) -> None: ...

    def get_booking(self, booking_id: str) -> None:
        return None

    def update_booking_status(self, booking_id: str, status: str, event: BookingEvent) -> None: ...

    def list_active_bookings(self) -> list[Booking]:
        return []

    def claim_or_recall(self, key: str, scope: Literal["confirm", "cancel"]) -> ClaimResult:
        return ClaimResult(status="in_progress", booking_id=None)

    def finalize_idempotency(self, key: str, booking: Booking) -> None: ...

    def put_pending_confirmation(
        self, subject_id: str, session_id: str, request_json: str, expires_at: Any
    ) -> None: ...

    def pop_pending_confirmation(self, subject_id: str) -> None:
        return None

    def list_pending_confirmations(self, now: Any) -> list:
        return []


class _NeverCalledChannel:
    """ConfirmationChannel that fails if request() is called."""

    async def request(self, req: Any) -> ConfirmationResult:
        raise AssertionError("ConfirmationChannel.request() must not be called for in_progress")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_progress_claim_returns_in_progress_outcome(tmp_path: Path) -> None:
    """claim_or_recall returning in_progress must produce outcome.status='in_progress'
    with no executed row (invariant 6) and no provider.confirm call."""
    draft = _draft()
    store = _InProgressStore(draft=draft)
    log = JSONLActionLog(log_dir=tmp_path / "logs")
    clock = FrozenClock(start=datetime(2026, 5, 20, 12, 0, tzinfo=UTC))

    # Provider mock: confirm must never be called.
    provider = MagicMock()
    provider.name = "uber-sim-mock"
    provider.confirm = AsyncMock(side_effect=AssertionError("provider.confirm must not be called"))
    # discover is not called because draft has no intent_snapshot.
    provider.discover = AsyncMock(
        side_effect=AssertionError("provider.discover must not be called")
    )

    channel = _NeverCalledChannel()

    result = await confirm_booking(
        ConfirmArgs(draft_id=draft.draft_id),
        provider=provider,  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        log=log,
        channel=channel,  # type: ignore[arg-type]
        clock=clock,
        session_id="S-ip",
        turn_id="T-ip",
    )

    # Outcome shape
    assert result["status"] == "in_progress"
    assert result["reason"] == "concurrent claim"
    action_id = result["action_id"]

    rows = [r for r in log.read("S-ip") if r["action_id"] == action_id]
    kinds = [r["kind"] for r in rows]

    # Invariant 6: no executed row after a rejected verified.
    assert "executed" not in kinds, f"executed row must not appear; rows: {kinds}"

    # Verified must carry the idempotency marker and result=rejected.
    verified = next(r for r in rows if r["kind"] == "verified")
    assert verified["result"] == "rejected"
    assert verified["checks"]["idempotency"] == "in_progress"

    # Outcome must close the action.
    outcome = next(r for r in rows if r["kind"] == "outcome")
    assert outcome["status"] == "in_progress"

    # Row order: requested → verified → outcome (no executed).
    assert kinds == ["requested", "verified", "outcome"]
