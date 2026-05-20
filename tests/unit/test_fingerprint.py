"""Tests for core/fingerprint.py — CLAUDE.md invariant 3."""

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from agentic_cab.core.fingerprint import canonical_json, fingerprint
from agentic_cab.core.types import Money, Option, RideOptionDetails

# Pinned sha256 digest of _opt(). If this breaks because Pydantic changed serialization,
# update the pin AND decide whether existing stored fingerprints need migrating.
_OPT_PINNED_DIGEST = "1ef947fca566803bb4a8cf67fa206eb3d7994fe53725a46e59291d4d7855df22"


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


def test_fingerprint_value_equality_across_instances() -> None:
    """Two independently constructed value-equal Options must hash identically."""
    a = _opt()
    b = _opt()
    assert fingerprint(a) == fingerprint(b)


def test_fingerprint_pinned_digest() -> None:
    """Pinned digest of the _opt() fixture catches Pydantic-version drift in model_dump.

    If this test breaks because Pydantic changed serialization, update the pin AND
    decide whether existing stored fingerprints need migrating.
    """
    assert fingerprint(_opt()) == _OPT_PINNED_DIGEST


def test_fingerprint_survives_json_round_trip() -> None:
    """Round-tripping an Option through JSON must preserve the fingerprint.

    Catches datetime parse-back divergence (e.g. tzinfo representation changes).
    """
    opt = _opt()
    parsed = Option.model_validate_json(opt.model_dump_json())
    assert fingerprint(opt) == fingerprint(parsed)


def test_canonical_json_nfc_normalises_strings() -> None:
    """NFC and NFD representations of the same string must produce the same JSON."""
    nfc = "Café"  # é precomposed (NFC)
    nfd = "Café"  # e + combining acute (NFD)
    assert canonical_json({"name": nfc}) == canonical_json({"name": nfd})
