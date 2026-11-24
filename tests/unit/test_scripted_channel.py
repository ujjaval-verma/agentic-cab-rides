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
    assert res.approved is True  # noqa: S101 pytest assertion
    assert res.reason == "approved"  # noqa: S101 pytest assertion
    assert res.approver == "scripted"  # noqa: S101 pytest assertion
    assert res.approver_id == "scripted"  # noqa: S101 pytest assertion
    assert res.channel == "scripted"  # noqa: S101 pytest assertion


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
    assert res.approved is False  # noqa: S101 pytest assertion
    assert res.reason == "declined"  # noqa: S101 pytest assertion
    assert res.approver_id is None  # noqa: S101 pytest assertion
    assert res.channel == "scripted"  # noqa: S101 pytest assertion


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
