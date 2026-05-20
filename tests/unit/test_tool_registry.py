from pydantic import BaseModel

from agentic_cab.tools.registry import pydantic_to_anthropic_tool


class _Args(BaseModel):
    option_id: str
    note: str | None = None


def test_converter_produces_anthropic_schema() -> None:
    schema = pydantic_to_anthropic_tool(
        name="prepare_booking",
        description="Create a draft from an option.",
        args_model=_Args,
    )
    assert schema["name"] == "prepare_booking"
    assert schema["description"] == "Create a draft from an option."
    inp = schema["input_schema"]
    assert inp["type"] == "object"
    assert "option_id" in inp["properties"]
    assert "option_id" in inp["required"]
    assert "note" not in inp["required"]
