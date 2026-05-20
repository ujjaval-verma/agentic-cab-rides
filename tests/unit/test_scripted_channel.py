from datetime import UTC, datetime

import pytest

from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.scripted import ScriptedConfirmationChannel
from agentic_cab.core.ports import ConfirmationRequest


@pytest.mark.asyncio
async def test_match_approve() -> None:
    clock = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    ch = ScriptedConfirmationChannel(
        script=[{"match": "UberX", "action": "approve"}],
        clock=clock,
    )
    res = await ch.request(
        ConfirmationRequest(
            subject_id="D",
            subject_kind="draft",
            summary="Confirm UberX for $15",
            fingerprint="f",
            details={},
            expires_at=datetime(2026, 5, 19, 12, 5, tzinfo=UTC),
        )
    )
    assert res.approved is True
    assert res.reason == "approved"
    assert res.approver == "scripted"


@pytest.mark.asyncio
async def test_match_decline() -> None:
    clock = FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC))
    ch = ScriptedConfirmationChannel(
        script=[{"match": "UberX", "action": "decline"}],
        clock=clock,
    )
    res = await ch.request(
        ConfirmationRequest(
            subject_id="D",
            subject_kind="draft",
            summary="Confirm UberX for $15",
            fingerprint="f",
            details={},
            expires_at=datetime(2026, 5, 19, 12, 5, tzinfo=UTC),
        )
    )
    assert res.approved is False
    assert res.reason == "declined"


@pytest.mark.asyncio
async def test_no_match_raises() -> None:
    ch = ScriptedConfirmationChannel(
        script=[{"match": "something-else", "action": "approve"}],
        clock=FrozenClock(start=datetime(2026, 5, 19, 12, 0, tzinfo=UTC)),
    )
    with pytest.raises(AssertionError):
        await ch.request(
            ConfirmationRequest(
                subject_id="D",
                subject_kind="draft",
                summary="UberX",
                fingerprint="f",
                details={},
                expires_at=datetime(2026, 5, 19, 12, 5, tzinfo=UTC),
            )
        )
