"""Unit tests for the P8 cost-meter seam in agent/loop.py::run_turn.

Verifies:
  - run_turn records exactly one LLMCall per model call, including the final
    non-tool_use response of a turn (the "record before the stop_reason branch"
    requirement — a record placed inside the tool_use branch would silently
    drop the last call of every turn).
  - The recorded LLMCall carries the token counts from response.usage, and forwards
    those same counts into core.pricing.price_call (the arithmetic inside price_call
    is pinned by test_pricing.py, not here).
  - usage objects missing or nulling the cache fields yield 0, not a crash.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Self
from unittest.mock import MagicMock

import pytest

from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.agent.loop import run_turn
from agentic_cab.core.constants import MODEL_ID
from agentic_cab.core.pricing import price_call
from agentic_cab.core.types import LLMCall

_CLOCK_START = datetime(2026, 5, 20, 9, 0, tzinfo=UTC)


class FakeCostMeter:
    """List-backed CostMeter — satisfies the Protocol structurally."""

    def __init__(self: Self) -> None:
        self.calls: list[LLMCall] = []

    def record(self: Self, call: LLMCall) -> None:
        self.calls.append(call)

    def drain(self: Self) -> list[LLMCall]:
        drained = self.calls
        self.calls = []
        return drained


class _Usage:
    """Minimal usage stand-in with real attribute-presence semantics (unlike
    MagicMock, which auto-vivifies any accessed attribute)."""

    def __init__(
        self: Self,
        input_tokens: int,
        output_tokens: int,
        cache_read_input_tokens: int | None = 0,
        cache_creation_input_tokens: int | None = 0,
        *,
        omit_cache_fields: bool = False,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        if not omit_cache_fields:
            self.cache_read_input_tokens = cache_read_input_tokens
            self.cache_creation_input_tokens = cache_creation_input_tokens


def _end_turn_response(usage: _Usage, text: str = "Done.") -> MagicMock:
    content_block = MagicMock()
    content_block.type = "text"
    content_block.text = text

    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [content_block]
    response.usage = usage
    return response


def _tool_use_response(usage: _Usage, tool_name: str, tool_input: dict[str, Any]) -> MagicMock:
    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.name = tool_name
    tool_use_block.id = "tu_001"
    tool_use_block.input = tool_input

    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [tool_use_block]
    response.usage = usage
    return response


@pytest.mark.asyncio
async def test_run_turn_records_one_llm_call_on_non_tool_use_final_response() -> None:
    """A turn whose final response has no tool_use block must still be recorded —
    recording only inside the tool_use branch would silently drop it."""
    usage = _Usage(input_tokens=120, output_tokens=30)
    client = MagicMock()
    client.messages.create.return_value = _end_turn_response(usage)

    meter = FakeCostMeter()
    clock = FrozenClock(start=_CLOCK_START)

    await run_turn(
        client,
        user_message="Hi",
        history=[],
        tools=[],
        handlers={},
        meter=meter,
        clock=clock,
    )

    assert len(meter.calls) == 1  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_run_turn_records_one_call_per_iteration_including_tool_use() -> None:
    """A turn with one tool_use round-trip then a final text response records
    exactly two LLMCalls — one per client.messages.create invocation."""
    tool_usage = _Usage(input_tokens=100, output_tokens=20)
    final_usage = _Usage(input_tokens=140, output_tokens=15)

    client = MagicMock()
    client.messages.create.side_effect = [
        _tool_use_response(tool_usage, "list_active_bookings", {}),
        _end_turn_response(final_usage),
    ]

    async def _handler(_args: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok"}

    meter = FakeCostMeter()
    clock = FrozenClock(start=_CLOCK_START)

    await run_turn(
        client,
        user_message="List my bookings",
        history=[],
        tools=[],
        handlers={"list_active_bookings": _handler},
        meter=meter,
        clock=clock,
    )

    assert len(meter.calls) == 2  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_recorded_llm_call_forwards_usage_token_counts_into_price_call() -> None:
    """The loop must hand response.usage's four counts to price_call unchanged.

    This does NOT pin price_call's arithmetic — the expectation is computed by calling
    it — only that the loop forwards the right numbers. price_call's own maths is
    covered by test_pricing.py.
    """
    usage = _Usage(
        input_tokens=200,
        output_tokens=50,
        cache_read_input_tokens=10,
        cache_creation_input_tokens=5,
    )
    client = MagicMock()
    client.messages.create.return_value = _end_turn_response(usage)

    meter = FakeCostMeter()
    clock = FrozenClock(start=_CLOCK_START)

    await run_turn(
        client,
        user_message="Hi",
        history=[],
        tools=[],
        handlers={},
        meter=meter,
        clock=clock,
    )

    call = meter.calls[0]
    assert call.provider == "anthropic"  # noqa: S101 pytest assertion
    assert call.model == MODEL_ID  # noqa: S101 pytest assertion
    assert call.input_tokens == 200  # noqa: S101 pytest assertion
    assert call.output_tokens == 50  # noqa: S101 pytest assertion
    assert call.cache_read_input_tokens == 10  # noqa: S101 pytest assertion
    assert call.cache_creation_input_tokens == 5  # noqa: S101 pytest assertion
    expected_cost = price_call(
        model=MODEL_ID,
        input_tokens=200,
        output_tokens=50,
        cache_read_input_tokens=10,
        cache_creation_input_tokens=5,
    )
    assert call.cost_microcents == expected_cost  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_missing_cache_fields_on_usage_yield_zero_not_a_crash() -> None:
    """Some SDK versions don't carry cache_* fields on usage at all."""
    usage = _Usage(input_tokens=100, output_tokens=10, omit_cache_fields=True)
    client = MagicMock()
    client.messages.create.return_value = _end_turn_response(usage)

    meter = FakeCostMeter()
    clock = FrozenClock(start=_CLOCK_START)

    await run_turn(
        client,
        user_message="Hi",
        history=[],
        tools=[],
        handlers={},
        meter=meter,
        clock=clock,
    )

    call = meter.calls[0]
    assert call.cache_read_input_tokens == 0  # noqa: S101 pytest assertion
    assert call.cache_creation_input_tokens == 0  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_null_cache_fields_on_usage_yield_zero_not_a_crash() -> None:
    """Some SDK versions carry cache_* fields as `int | None`; None must become 0,
    not crash the StrictInt validator on LLMCall."""
    usage = _Usage(
        input_tokens=100,
        output_tokens=10,
        cache_read_input_tokens=None,
        cache_creation_input_tokens=None,
    )
    client = MagicMock()
    client.messages.create.return_value = _end_turn_response(usage)

    meter = FakeCostMeter()
    clock = FrozenClock(start=_CLOCK_START)

    await run_turn(
        client,
        user_message="Hi",
        history=[],
        tools=[],
        handlers={},
        meter=meter,
        clock=clock,
    )

    call = meter.calls[0]
    assert call.cache_read_input_tokens == 0  # noqa: S101 pytest assertion
    assert call.cache_creation_input_tokens == 0  # noqa: S101 pytest assertion


@pytest.mark.asyncio
async def test_recorded_latency_ms_reflects_frozen_clock_advance() -> None:
    """latency_ms is truthful — it comes from Clock.monotonic_ns(), not a hardcoded 0."""
    usage = _Usage(input_tokens=10, output_tokens=5)
    client = MagicMock()
    client.messages.create.return_value = _end_turn_response(usage)

    meter = FakeCostMeter()
    clock = FrozenClock(start=_CLOCK_START)

    await run_turn(
        client,
        user_message="Hi",
        history=[],
        tools=[],
        handlers={},
        meter=meter,
        clock=clock,
    )

    call = meter.calls[0]
    # FrozenClock.monotonic_ns() advances by a fixed 1ms per call (see
    # adapters/clock.py::_FROZEN_MONOTONIC_STEP_NS). run_turn calls it once before
    # and once after client.messages.create, so the single iteration here spans
    # exactly one step: latency_ms must be the real elapsed value (1), not a
    # hardcoded 0 — the regression this test exists to catch.
    assert call.latency_ms == 1  # noqa: S101 pytest assertion
