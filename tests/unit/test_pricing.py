import pytest

from agentic_cab.core.constants import MODEL_ID
from agentic_cab.core.pricing import price_call


def test_known_token_mix_prices_exactly():
    # 1000 in @ 500 mc/tok, 500 out @ 2500, 2000 cache-read @ 50, 400 cache-write @ 625.
    cost = price_call(
        model=MODEL_ID,
        input_tokens=1000,
        output_tokens=500,
        cache_read_input_tokens=2000,
        cache_creation_input_tokens=400,
    )
    assert cost == 500_000 + 1_250_000 + 100_000 + 250_000


def test_cache_read_is_cheaper_than_input_for_same_tokens():
    plain = price_call(model=MODEL_ID, input_tokens=10_000, output_tokens=0)
    cached = price_call(
        model=MODEL_ID, input_tokens=0, output_tokens=0, cache_read_input_tokens=10_000
    )
    assert cached < plain
    assert cached * 10 == plain


def test_cache_write_is_more_expensive_than_input_for_same_tokens():
    plain = price_call(model=MODEL_ID, input_tokens=10_000, output_tokens=0)
    written = price_call(
        model=MODEL_ID, input_tokens=0, output_tokens=0, cache_creation_input_tokens=10_000
    )
    assert written > plain
    assert written * 4 == plain * 5


def test_zero_token_call_costs_zero():
    assert price_call(model=MODEL_ID, input_tokens=0, output_tokens=0) == 0


def test_unknown_model_raises():
    with pytest.raises(ValueError, match="No price entry for model"):
        price_call(model="claude-not-a-real-model", input_tokens=100, output_tokens=100)
