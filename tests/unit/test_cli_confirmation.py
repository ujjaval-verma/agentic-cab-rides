"""Tests for CLIConfirmationChannel.

Structural proof: the channel reads from the configured tty_path, never from sys.stdin.
Invariant 2: every state-mutating action goes through a ConfirmationChannel; stdin cannot
inject 'y' — only the tty can approve.
"""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.confirmation.cli import CLIConfirmationChannel
from agentic_cab.core.ports import ConfirmationRequest

_FROZEN_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)
_FAKE_FINGERPRINT = "abcdef123456789012345678"


def _make_request() -> ConfirmationRequest:
    return ConfirmationRequest(
        draft_id="01JTEST00000000000000001",
        summary="Confirm UberX from 123 Main St to 456 Oak Ave — $12.50",
        fingerprint=_FAKE_FINGERPRINT,
        details={"price_cents": 1250, "currency": "USD"},
        expires_at=datetime(2026, 5, 20, 12, 5, 0, tzinfo=UTC),
    )


def _make_clock() -> FrozenClock:
    return FrozenClock(start=_FROZEN_NOW)


class TestCLIConfirmationChannel:
    @pytest.mark.asyncio
    async def test_reads_from_tty_path_not_stdin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Structural proof (Invariant 2): stdin says 'n', tty says 'y' — channel must honor tty.

        This test is the deliverable for T23. If it passes, the channel cannot be
        bypassed by piping 'y' to stdin, which is the exact attack vector the spec
        guards against.
        """
        fake_tty = tmp_path / "fake_tty"
        fake_tty.write_text("y\n")

        # Redirect stdin to 'n' — channel must NOT read from here.
        monkeypatch.setattr(sys, "stdin", io.StringIO("n\n"))

        channel = CLIConfirmationChannel(
            clock=_make_clock(),
            tty_path=str(fake_tty),
        )
        result = await channel.request(_make_request())

        assert result.approved is True, (
            "Channel should approve because tty contains 'y', even though stdin says 'n'. "
            "If this fails, the channel is reading from stdin — Invariant 2 is violated."
        )
        assert result.reason == "approved"
        assert result.approver == "cli-tty"
        assert result.approved_at == _FROZEN_NOW

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tty_input", ["n\n", "\n", "maybe\n"])
    async def test_declines_on_non_y(self, tmp_path: Path, tty_input: str) -> None:
        """Any response that is not 'y' or 'yes' (case-insensitive) must decline."""
        fake_tty = tmp_path / "fake_tty"
        fake_tty.write_text(tty_input)

        channel = CLIConfirmationChannel(
            clock=_make_clock(),
            tty_path=str(fake_tty),
        )
        result = await channel.request(_make_request())

        assert result.approved is False
        assert result.reason == "declined"
        assert result.approved_at is None
        assert result.approver is None

    @pytest.mark.asyncio
    async def test_raises_when_tty_unopenable(self) -> None:
        """Channel must raise OSError when tty_path cannot be opened — no silent fallback."""
        channel = CLIConfirmationChannel(
            clock=_make_clock(),
            tty_path="/nonexistent/tty",
        )
        with pytest.raises(OSError, match=r"No such file or directory|nonexistent"):
            await channel.request(_make_request())
