"""Tests for core/fingerprint.py — CLAUDE.md invariant 3."""

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from agentic_cab.core.fingerprint import canonical_json, fingerprint
from agentic_cab.core.types import Money, Option, RideOptionDetails


def _opt() -> Option:
    return Option(
        option_id="opt-1",
        provider="uber-sim",
        title="UberX",
        price=Money(amount_cents=1500, currency="USD"),
        eta_seconds=180,
        when=None,
        details=RideOptionDetails(vehicle_class="uberx", capacity=4),
        provider_metadata={"zone": "SF-1"},
        expires_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
    )


def test_fingerprint_is_64_hex():
    fp = fingerprint(_opt())
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_changes_on_price_mutation():
    a = _opt()
    b = a.model_copy(update={"price": Money(amount_cents=9999, currency="USD")})
    assert fingerprint(a) != fingerprint(b)


def test_canonical_json_is_dict_order_independent():
    d1 = {"a": 1, "b": {"x": 2, "y": 3}}
    d2 = {"b": {"y": 3, "x": 2}, "a": 1}
    assert canonical_json(d1) == canonical_json(d2)


@given(
    st.dictionaries(
        st.text(min_size=1, max_size=8),
        st.one_of(st.integers(), st.text(max_size=20), st.booleans()),
        min_size=0,
        max_size=8,
    )
)
def test_canonical_json_stable_under_key_permutation(d: dict) -> None:
    # Build a structurally identical dict by walking items in a different order.
    # This proves canonical_json depends only on key/value content, not insertion order.
    items = list(d.items())
    reordered = dict(reversed(items))
    a = canonical_json(d)
    b = canonical_json(reordered)
    assert a == b
