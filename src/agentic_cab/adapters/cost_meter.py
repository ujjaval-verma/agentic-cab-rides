"""In-memory CostMeter: a per-turn buffer of priced LLM calls.

There is deliberately no persistent CostMeter. Durability is the action log's job —
the meter only holds calls until the caller drains them into a log row.
"""

from __future__ import annotations

from typing import Self

from agentic_cab.core.types import LLMCall


class InMemoryCostMeter:
    def __init__(self: Self) -> None:
        self._calls: list[LLMCall] = []

    def record(self: Self, call: LLMCall) -> None:
        self._calls.append(call)

    def drain(self: Self) -> list[LLMCall]:
        drained = self._calls
        self._calls = []
        return drained
