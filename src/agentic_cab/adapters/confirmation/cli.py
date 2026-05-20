"""CLIConfirmationChannel — reads user approval from /dev/tty, never from stdin.

Invariant 2: every state-mutating action goes through a ConfirmationChannel. The channel
is the structural gate; stdin cannot inject 'y'. The test `test_reads_from_tty_path_not_stdin`
is the proof that this invariant is upheld.

Implementation note: the prompt is written to sys.stderr so it appears on the user's
terminal regardless of stdout redirection. The response is read exclusively from
`tty_path` (default `/dev/tty`). This dual-handle design — write to stderr, read from
tty_path — deviates from the spec's literal "r+" phrasing but satisfies the spec's
intent: agent stdin cannot inject 'y'. Using a single r+ handle on a real /dev/tty
would require seek() after writing the prompt, but /dev/tty is a stream device and
seek() is a no-op or raises; readline() would then read whatever the kernel puts next
(the user's typed input), which works in practice but makes the DI-based test
non-deterministic. The dual-handle approach is fully deterministic under test.
"""

from __future__ import annotations

import sys
from typing import Self

from agentic_cab.core.ports import Clock, ConfirmationRequest, ConfirmationResult


class CLIConfirmationChannel:
    """Interactive confirmation channel that reads approval from the controlling tty.

    Args:
        clock: Provides the current timestamp for `approved_at`.
        tty_path: Path to the tty device to read from. Defaults to ``/dev/tty``.
                  Override in tests to a regular file pre-seeded with the desired input.
    """

    def __init__(self: Self, clock: Clock, tty_path: str = "/dev/tty") -> None:
        self._clock = clock
        self._tty_path = tty_path

    async def request(self: Self, req: ConfirmationRequest) -> ConfirmationResult:
        fingerprint_prefix = req.fingerprint[:12]
        prompt_lines = [
            "",
            f"  {req.summary}",
            f"  Fingerprint: {fingerprint_prefix}...",
            "",
        ]
        prompt = "\n".join(prompt_lines) + "Approve? [y/N]: "

        # Write the prompt to stderr so it appears on the controlling terminal
        # regardless of stdout redirection. Invariant 14: write via file handle,
        # not print(), to keep the rendering layer explicit.
        sys.stderr.write(prompt)
        sys.stderr.flush()

        # Exclusively read from tty_path — never fall back to sys.stdin.
        # If tty_path cannot be opened, raise immediately (OSError / FileNotFoundError).
        # There is NO silent fallback; that is the bug this design guards against.
        with open(self._tty_path) as tty:
            line = tty.readline()

        response = line.strip().lower()
        approved = response in {"y", "yes"}

        if approved:
            return ConfirmationResult(
                approved=True,
                reason="approved",
                approved_at=self._clock.now(),
                approver="cli-tty",
            )
        return ConfirmationResult(
            approved=False,
            reason="declined",
            approved_at=None,
            approver=None,
        )
