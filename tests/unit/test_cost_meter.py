from agentic_cab.adapters.cost_meter import InMemoryCostMeter
from agentic_cab.core.types import LLMCall


def _call(output_tokens: int) -> LLMCall:
    return LLMCall(
        provider="anthropic",
        model="claude-opus-4-7",
        input_tokens=100,
        output_tokens=output_tokens,
        cost_microcents=50_000,
        latency_ms=120,
    )


def test_drain_returns_recorded_calls_in_order():
    meter = InMemoryCostMeter()
    meter.record(_call(1))
    meter.record(_call(2))
    assert [c.output_tokens for c in meter.drain()] == [1, 2]


def test_second_drain_is_empty():
    meter = InMemoryCostMeter()
    meter.record(_call(1))
    meter.drain()
    assert meter.drain() == []


def test_drain_after_more_records_returns_only_the_new_ones():
    meter = InMemoryCostMeter()
    meter.record(_call(1))
    meter.drain()
    meter.record(_call(2))
    meter.record(_call(3))
    assert [c.output_tokens for c in meter.drain()] == [2, 3]
