"""Render a scenario run (chat history + action log + invariant results) to Markdown."""

from __future__ import annotations

import json
from typing import Any


def render(
    *,
    scenario_name: str,
    seed: int,
    chat: list[dict[str, Any]],
    action_log: list[dict[str, Any]],
    invariants: list[tuple[str, bool, dict[str, Any]]],
) -> str:
    """Build a Markdown transcript with header, conversation, collapsed log, invariants."""
    lines: list[str] = [
        f"# Transcript — {scenario_name}",
        "",
        f"- **Seed:** {seed}",
        f"- **Repro:** `make transcripts SCENARIO={scenario_name}`",
        "",
        "## Conversation",
        "",
    ]
    for msg in chat:
        role = str(msg.get("role", "?")).title()
        content = msg.get("content")
        if isinstance(content, str):
            lines.append(f"**{role}:** {content}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    lines.append(f"**{role}:** {block.get('text', '')}")
        lines.append("")

    lines += [
        "## Action log",
        "",
        "<details><summary>Click to expand</summary>",
        "",
        "```json",
    ]
    for r in action_log:
        lines.append(json.dumps(r, default=str))
    lines += ["```", "", "</details>", "", "## Invariants", ""]

    for name, passed, params in invariants:
        mark = "PASS" if passed else "FAIL"
        param_str = " ".join(f"{k}={v}" for k, v in params.items()) if params else ""
        lines.append(f"- [{mark}] `{name}` {param_str}".rstrip())
    lines.append("")
    return "\n".join(lines)
