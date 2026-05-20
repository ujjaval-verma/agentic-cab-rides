"""Pydantic model → Anthropic tool schema converter. See spec §7."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def pydantic_to_anthropic_tool(
    *,
    name: str,
    description: str,
    args_model: type[BaseModel],
) -> dict[str, Any]:
    json_schema = args_model.model_json_schema()
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": json_schema.get("properties", {}),
            "required": json_schema.get("required", []),
        },
    }
