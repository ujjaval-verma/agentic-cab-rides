"""Harness-level smoke tests.

These exist so `make verify` is meaningful on day zero (pytest exits 5 on
zero tests collected). They also catch packaging regressions: if the
project stops importing or its dependencies become unsatisfiable, these
tests fail loudly.
"""

from __future__ import annotations


def test_package_imports() -> None:
    """The `agentic_cab` package must be importable from a fresh install."""
    import agentic_cab  # noqa: F401


def test_core_runtime_deps_importable() -> None:
    """Spot-check that pinned runtime deps resolved and import without error."""
    import anthropic  # noqa: F401
    import fastapi  # noqa: F401
    import pydantic  # noqa: F401
    import rich  # noqa: F401
    import structlog  # noqa: F401
    import typer  # noqa: F401
    import ulid  # noqa: F401
