"""WebConfirmationChannel — gate over SSE pending-future.

Lives in web/ rather than adapters/ because it depends on the SSE bus;
placing it under adapters/ would create an upward layer edge.

The browser posts to /confirmations/{subject_id} which calls resolve(); the agent's
await on request() then unblocks with the user's decision.

Lifecycle:
- request() creates a Future, stores it, publishes a pending_confirmation SSE event,
  then awaits the future with a deadline from req.expires_at.
- On timeout, the pending entry is cleaned up and ConfirmationResult(approved=False,
  reason="timeout") is returned — the confirmation gate is structurally denied.
- resolve() pops the future by subject_id and sets its result. Returns False if no
  matching pending request exists (idempotent — safe to call twice).
- The future is always cleaned up via try/finally so a closed tab (timeout) never
  leaks a pending entry.

Invariant 2: the agent never sees confirmed=True without this channel resolving it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Self

from agentic_cab.core.ports import ConfirmationChannel, ConfirmationRequest, ConfirmationResult
from agentic_cab.web.sse import SessionEventBus

# Satisfy the runtime_checkable Protocol check.
assert isinstance(ConfirmationChannel, type) or hasattr(ConfirmationChannel, "__protocol_attrs__")


class WebConfirmationChannel:
    """ConfirmationChannel implementation that suspends until the browser resolves."""

    def __init__(self: Self, bus: SessionEventBus, session_id: str) -> None:
        self._bus = bus
        self._session_id = session_id
        self._pending: dict[str, asyncio.Future[ConfirmationResult]] = {}

    async def request(self: Self, req: ConfirmationRequest) -> ConfirmationResult:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ConfirmationResult] = loop.create_future()
        self._pending[req.subject_id] = future

        # Publish the pending prompt so the browser can render a modal.
        event: dict[str, Any] = {
            "kind": "pending_confirmation",
            "subject_id": req.subject_id,
            "subject_kind": req.subject_kind,
            "summary": req.summary,
            "fingerprint": req.fingerprint,
            "expires_at": req.expires_at.isoformat(),
        }
        self._bus.publish(self._session_id, event)

        now = datetime.now(UTC)
        timeout_seconds = max(0.0, (req.expires_at - now).total_seconds())

        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout_seconds)
        except TimeoutError:
            return ConfirmationResult(
                approved=False,
                reason="timeout",
                approved_at=None,
                approver=None,
            )
        finally:
            self._pending.pop(req.subject_id, None)

    def resolve(self: Self, subject_id: str, *, approved: bool, approver: str | None) -> bool:
        """Resolve a pending confirmation request from the browser.

        Returns True if a matching pending request was found and resolved,
        False if no pending request exists for subject_id (safe to call multiple times).
        """
        future = self._pending.pop(subject_id, None)
        if future is None or future.done():
            return False

        if approved:
            result = ConfirmationResult(
                approved=True,
                reason="approved",
                approved_at=datetime.now(UTC),
                approver=approver or "web-user",
            )
        else:
            result = ConfirmationResult(
                approved=False,
                reason="declined",
                approved_at=None,
                approver=None,
            )
        future.set_result(result)
        return True
