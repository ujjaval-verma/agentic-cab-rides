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
from agentic_cab.core.ids import new_principal_id
from agentic_cab.core.ports import ConfirmationRequest

_FROZEN_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)
_FAKE_FINGERPRINT = "abcdef123456789012345678"
_PRINCIPAL_ID = new_principal_id()


def _make_request() -> ConfirmationRequest:
    return ConfirmationRequest(
        subject_id="01JTEST00000000000000001",
        subject_kind="draft",
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

        If it passes, the channel cannot be bypassed by piping 'y' to stdin,
        which is the exact attack vector Invariant 2 guards against.
        """
        fake_tty = tmp_path / "fake_tty"
        fake_tty.write_text("y\n")

        # Redirect stdin to 'n' — channel must NOT read from here.
        monkeypatch.setattr(sys, "stdin", io.StringIO("n\n"))

        channel = CLIConfirmationChannel(
            clock=_make_clock(),
            principal_id=_PRINCIPAL_ID,
            tty_path=str(fake_tty),
        )
        result = await channel.request(_make_request())

        assert result.approved is True, (  # noqa: S101 pytest assertion
            "Channel should approve because tty contains 'y', even though stdin says 'n'. "
            "If this fails, the channel is reading from stdin — Invariant 2 is violated."
        )
        assert result.reason == "approved"  # noqa: S101 pytest assertion
        assert result.approver == "cli-tty"  # noqa: S101 pytest assertion
        assert result.approved_at == _FROZEN_NOW  # noqa: S101 pytest assertion

    @pytest.mark.asyncio
    async def test_approve_records_injected_principal_and_cli_channel(self, tmp_path: Path) -> None:
        fake_tty = tmp_path / "fake_tty"
        fake_tty.write_text("y\n")
        channel = CLIConfirmationChannel(
            clock=_make_clock(),
            principal_id=_PRINCIPAL_ID,
            tty_path=str(fake_tty),
        )
        result = await channel.request(_make_request())
        assert result.approved is True  # noqa: S101 pytest assertion
        assert result.approver_id == _PRINCIPAL_ID  # noqa: S101 pytest assertion
        assert result.channel == "cli"  # noqa: S101 pytest assertion

    @pytest.mark.asyncio
    async def test_decline_records_no_principal_and_cli_channel(self, tmp_path: Path) -> None:
        fake_tty = tmp_path / "fake_tty"
        fake_tty.write_text("n\n")
        channel = CLIConfirmationChannel(
            clock=_make_clock(),
            principal_id=_PRINCIPAL_ID,
            tty_path=str(fake_tty),
        )
        result = await channel.request(_make_request())
        assert result.approved is False  # noqa: S101 pytest assertion
        assert result.approver_id is None  # noqa: S101 pytest assertion
        assert result.channel == "cli"  # noqa: S101 pytest assertion

    @pytest.mark.asyncio
    async def test_never_reads_os_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invariant 8: the approver identity is the injected principal, never the OS user."""
        import getpass
        import os

        def _forbidden(*_a: object, **_k: object) -> str:
            raise AssertionError("CLI channel must not read OS identity")

        monkeypatch.setattr(getpass, "getuser", _forbidden)
        monkeypatch.setattr(os, "getlogin", _forbidden)
        fake_tty = tmp_path / "fake_tty"
        fake_tty.write_text("y\n")
        channel = CLIConfirmationChannel(
            clock=_make_clock(),
            principal_id=_PRINCIPAL_ID,
            tty_path=str(fake_tty),
        )
        result = await channel.request(_make_request())
        assert result.approver_id == _PRINCIPAL_ID  # noqa: S101 pytest assertion

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tty_input", ["n\n", "\n", "maybe\n"])
    async def test_declines_on_non_y(self, tmp_path: Path, tty_input: str) -> None:
        """Any response that is not 'y' or 'yes' (case-insensitive) must decline."""
        fake_tty = tmp_path / "fake_tty"
        fake_tty.write_text(tty_input)

        channel = CLIConfirmationChannel(
            clock=_make_clock(),
            principal_id=_PRINCIPAL_ID,
            tty_path=str(fake_tty),
        )
        result = await channel.request(_make_request())

        assert result.approved is False  # noqa: S101 pytest assertion
        assert result.reason == "declined"  # noqa: S101 pytest assertion
        assert result.approved_at is None  # noqa: S101 pytest assertion
        assert result.approver is None  # noqa: S101 pytest assertion

    @pytest.mark.asyncio
    async def test_raises_when_tty_unopenable(self) -> None:
        """Channel must raise OSError when tty_path cannot be opened — no silent fallback."""
        channel = CLIConfirmationChannel(
            clock=_make_clock(),
            principal_id=_PRINCIPAL_ID,
            tty_path="/nonexistent/tty",
        )
        with pytest.raises(OSError, match=r"No such file or directory"):
            await channel.request(_make_request())

    @pytest.mark.asyncio
    async def test_eof_on_readline_returns_declined(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """EOFError on tty.readline() (disconnected tty) must produce a decline — not propagate."""
        from unittest import mock

        fake_tty = mock.MagicMock()
        fake_tty.__enter__ = mock.Mock(return_value=fake_tty)
        fake_tty.__exit__ = mock.Mock(return_value=False)
        fake_tty.readline.side_effect = EOFError

        monkeypatch.setattr("builtins.open", mock.Mock(return_value=fake_tty))

        channel = CLIConfirmationChannel(
            clock=_make_clock(),
            principal_id=_PRINCIPAL_ID,
            tty_path="/dev/tty",
        )
        result = await channel.request(_make_request())

        assert result.approved is False  # noqa: S101 pytest assertion
        assert result.reason == "declined"  # noqa: S101 pytest assertion
        assert result.approved_at is None  # noqa: S101 pytest assertion
        assert result.approver is None  # noqa: S101 pytest assertion
