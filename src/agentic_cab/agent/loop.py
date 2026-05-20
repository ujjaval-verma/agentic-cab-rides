"""Minimal Anthropic-SDK agent loop. See spec §7. No retries in P1 (FOLLOWUPS)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from anthropic import Anthropic

from agentic_cab.agent.system_prompt import SYSTEM_PROMPT
from agentic_cab.core.constants import AGENT_MAX_TOKENS, MODEL_ID

log = structlog.get_logger()

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


async def run_turn(
    client: Anthropic,
    *,
    user_message: str,
    history: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    handlers: dict[str, ToolHandler],
) -> tuple[str, list[dict[str, Any]]]:
    """Run one user turn until the model returns a final text response.

    Returns (final_assistant_text, new_history).
    """
    history = [*history, {"role": "user", "content": user_message}]
    while True:
        # Note: claude-opus-4-7 is an extended-thinking model and rejects the
        # 'temperature' parameter (API returns 400).  Determinism is maintained
        # via FrozenClock + seeded RNG in scenario mode (CLAUDE.md inv. 9).
        response = client.messages.create(
            model=MODEL_ID,
            max_tokens=AGENT_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=tools,  # type: ignore[arg-type]
            messages=history,  # type: ignore[arg-type]
        )
        if response.stop_reason == "tool_use":
            # Collect tool_use blocks
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            # Append assistant turn (with all content blocks — text + tool_use)
            history.append(
                {"role": "assistant", "content": [b.model_dump() for b in response.content]}
            )
            # Run each handler and accumulate tool_result blocks
            tool_results: list[dict[str, Any]] = []
            for tu in tool_uses:
                handler = handlers[tu.name]
                result = await handler(dict(tu.input))  # type: ignore[arg-type]
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(result, default=str),
                    }
                )
            history.append({"role": "user", "content": tool_results})
            continue
        # Final response — collect text
        text_parts: list[str] = []
        content_dumps: list[dict[str, Any]] = []
        for b in response.content:
            if b.type == "text":
                text_parts.append(b.text)
            content_dumps.append(b.model_dump())
        history.append({"role": "assistant", "content": content_dumps})
        return "".join(text_parts), history
