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
    assert schema["name"] == "prepare_booking"  # noqa: S101 pytest assertion
    assert schema["description"] == "Create a draft from an option."  # noqa: S101 pytest assertion
    inp = schema["input_schema"]
    assert inp["type"] == "object"  # noqa: S101 pytest assertion
    assert "option_id" in inp["properties"]  # noqa: S101 pytest assertion
    assert "option_id" in inp["required"]  # noqa: S101 pytest assertion
    assert "note" not in inp["required"]  # noqa: S101 pytest assertion
