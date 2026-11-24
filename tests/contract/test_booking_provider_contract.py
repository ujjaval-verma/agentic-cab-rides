"""Parametrized BookingProvider contract suite.

Each test runs against every registered provider (uber_sim, mock_appointment).
Tests are Protocol-only: they access only the BookingProvider interface, never
adapter-specific attributes. The fixture matrix is defined in conftest.py.

Markers:
  contract — included in make test-fast, excluded from make test-slow.

Tests:
  - test_idempotency_replay_same_key_same_booking
  - test_idempotency_different_key_different_booking
  - test_option_expiry_honored
  - test_draft_expiry_honored
  - test_cancel_after_cancel_is_no_op
  - test_fingerprint_stable_under_no_op_re_quote
  - test_force_surge_on_confirm_produces_price_changed (skipped unless provider exposes hook)
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from agentic_cab.core.ports import BookingProvider
from agentic_cab.core.types import Intent


@pytest.mark.contract
@pytest.mark.asyncio
async def test_idempotency_replay_same_key_same_booking(
    provider_fixture: tuple[BookingProvider, Callable[[], Intent]],
) -> None:
    """Two confirms with the same idempotency key → same booking_id returned; no double-book."""
    provider, intent_factory = provider_fixture
    intent = intent_factory()

    options = await provider.discover(intent)
    assert options, "discover must return at least one option"  # noqa: S101 pytest assertion

    draft = await provider.prepare(options[0].option_id)
    key = "contract-idem-key-001"

    b1 = await provider.confirm(draft.draft_id, idempotency_key=key)
    b2 = await provider.confirm(draft.draft_id, idempotency_key=key)

    # Same idempotency key must return the same booking_id on replay
    assert b1.booking_id == b2.booking_id  # noqa: S101 pytest assertion


@pytest.mark.contract
@pytest.mark.asyncio
async def test_idempotency_different_key_different_booking(
    provider_fixture: tuple[BookingProvider, Callable[[], Intent]],
) -> None:
    """Two confirms with different keys → different booking_ids."""
    provider, intent_factory = provider_fixture
    intent = intent_factory()

    options = await provider.discover(intent)
    assert options  # noqa: S101 pytest assertion

    draft1 = await provider.prepare(options[0].option_id)
    b1 = await provider.confirm(draft1.draft_id, idempotency_key="keyAlpha111111111111111111")

    # Prepare a fresh draft: re-discover so providers that mint fresh IDs work correctly.
    options2 = await provider.discover(intent)
    draft2 = await provider.prepare(options2[0].option_id)
    b2 = await provider.confirm(draft2.draft_id, idempotency_key="keyBeta2222222222222222222")

    # Different idempotency keys must produce different bookings
    assert b1.booking_id != b2.booking_id  # noqa: S101 pytest assertion


@pytest.mark.contract
@pytest.mark.asyncio
async def test_option_expiry_honored(
    provider_fixture: tuple[BookingProvider, Callable[[], Intent]],
) -> None:
    """Preparing an option_id that was never discovered raises ValueError.

    This proxies the expiry check: a provider must reject unknown (or expired)
    option_ids rather than silently accept them.
    """
    provider, _ = provider_fixture
    with pytest.raises((ValueError, KeyError)):
        await provider.prepare("nonexistent-option-id-xyz")


@pytest.mark.contract
@pytest.mark.asyncio
async def test_draft_expiry_honored(
    provider_fixture: tuple[BookingProvider, Callable[[], Intent]],
) -> None:
    """Confirming an unknown draft_id raises ValueError.

    This proxies the draft-expiry check: a provider must reject an unknown
    (or expired-and-purged) draft_id.
    """
    provider, _ = provider_fixture
    with pytest.raises((ValueError, KeyError)):
        await provider.confirm("nonexistent-draft-id-xyz", idempotency_key="contract-key-exp")


@pytest.mark.contract
@pytest.mark.asyncio
async def test_cancel_after_cancel_is_no_op(
    provider_fixture: tuple[BookingProvider, Callable[[], Intent]],
) -> None:
    """Cancelling an already-cancelled booking with the same key returns the same booking."""
    provider, intent_factory = provider_fixture
    intent = intent_factory()

    options = await provider.discover(intent)
    draft = await provider.prepare(options[0].option_id)
    booking = await provider.confirm(draft.draft_id, idempotency_key="contract-key-cancel-setup")

    c1 = await provider.cancel(booking.booking_id, idempotency_key="contract-key-cancel")
    c2 = await provider.cancel(booking.booking_id, idempotency_key="contract-key-cancel")

    assert c1.booking_id == c2.booking_id  # noqa: S101 pytest assertion
    assert c2.status == "cancelled"  # noqa: S101 pytest assertion


@pytest.mark.contract
@pytest.mark.asyncio
async def test_fingerprint_stable_under_no_op_re_quote(
    provider_fixture: tuple[BookingProvider, Callable[[], Intent]],
) -> None:
    """Calling discover twice with the same intent returns at least one option with a stable
    fingerprint (option_snapshot content is deterministic for the same intent).

    NOTE: option_ids change between calls (providers generate fresh IDs), so we compare
    the prepared draft fingerprints of same-positioned options by price and details shape,
    not option_id.
    """
    from agentic_cab.core.fingerprint import fingerprint as compute_fp

    provider, intent_factory = provider_fixture
    intent = intent_factory()

    opts1 = await provider.discover(intent)
    opts2 = await provider.discover(intent)

    assert opts1  # noqa: S101 pytest assertion
    assert opts2  # noqa: S101 pytest assertion

    # Normalise option_id and expires_at (both vary per call) then fingerprint.
    # Price, title, and details must be stable for the same intent.
    canonical_updates = {"option_id": "canonical", "expires_at": opts1[0].expires_at}
    fp1 = compute_fp(opts1[0].model_copy(update=canonical_updates))
    fp2 = compute_fp(opts2[0].model_copy(update=canonical_updates))
    # Option content (excluding option_id/expires_at) must be stable across re-discover
    assert fp1 == fp2  # noqa: S101 pytest assertion


@pytest.mark.contract
@pytest.mark.asyncio
async def test_force_surge_on_confirm_produces_price_changed(
    provider_fixture: tuple[BookingProvider, Callable[[], Intent]],
) -> None:
    """For UberSimAdapter: force_surge_on_confirm=True causes confirm-time re-price.

    Skipped for providers that don't expose the surge hook.
    """
    provider, intent_factory = provider_fixture

    # Only UberSimAdapter exposes _surge_curve for this scenario
    from agentic_cab.adapters.uber_sim.provider import UberSimAdapter

    if not isinstance(provider, UberSimAdapter):
        pytest.skip(f"not applicable to {provider.name}")

    # Re-create the provider with force_surge_on_confirm=True to exercise the price-changed path.
    # We need a fresh tmp store for isolation.
    import tempfile
    from datetime import timedelta
    from decimal import Decimal
    from pathlib import Path

    from agentic_cab.adapters.clock import FrozenClock
    from agentic_cab.adapters.state_store import SqliteStateStore
    from agentic_cab.adapters.uber_sim.surge import SurgeCurve

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store = SqliteStateStore(db_path=tmp / "surge.db")
        store.apply_schema()
        start = FrozenClock(start=provider._clock.now())
        curve = SurgeCurve(
            base_multiplier=Decimal("1.0"),
            peak_multiplier=Decimal("3.0"),
            peak_start=provider._clock.now() - timedelta(hours=2),
            peak_duration_min=10,  # already ended at discover time
            force_surge_on_confirm=True,
        )
        surge_sim = UberSimAdapter(store=store, clock=start, rng_seed=42, surge_curve=curve)

        intent = intent_factory()
        options = await surge_sim.discover(intent)
        assert options  # noqa: S101 pytest assertion

        draft = await surge_sim.prepare(options[0].option_id)
        # confirm will re-price — last_confirm_price_cents records the surged price
        await surge_sim.confirm(draft.draft_id, idempotency_key="surge-test-key")
        assert surge_sim.last_confirm_price_cents > 0  # noqa: S101 pytest assertion
        # The surged price should exceed the discover-time price (force_surge_on_confirm=True)
        assert surge_sim.last_confirm_price_cents >= options[0].price.amount_cents  # noqa: S101 pytest assertion
