"""WebConfirmationChannel — gate over SSE pending-future.

Lives in web/ rather than adapters/ because it depends on the SSE bus;
placing it under adapters/ would create an upward layer edge.

The browser posts its decision to the web app's confirmation route, which calls
resolve(); the agent's await on request() then unblocks with the user's decision.

The approver identity is owned by the channel: it is set server-side from the
session's `principal_id` at construction. `resolve()` accepts only the y/n
decision, so no client-supplied value can reach `ConfirmationResult.approver*`.

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
from typing import TYPE_CHECKING, Any, Self

from agentic_cab.core.ports import (
    Clock,
    ConfirmationChannel,
    ConfirmationRequest,
    ConfirmationResult,
    StateStore,
)
from agentic_cab.web.sse import SessionEventBus

if TYPE_CHECKING:
    pass

_WEB_APPROVER = "web-user"

# Satisfy the runtime_checkable Protocol check.
assert isinstance(ConfirmationChannel, type) or hasattr(ConfirmationChannel, "__protocol_attrs__")


class WebConfirmationChannel:
    """ConfirmationChannel implementation that suspends until the browser resolves.

    Takes an optional StateStore and Clock for restart-survival (T30). When a store
    is provided, request() durably persists the pending confirmation before awaiting
    the future, and pops the row when the future resolves (approved/declined/timeout).
    On process restart, the app startup hook calls list_pending_confirmations() and
    re-publishes each via the bus so the reloaded browser tab can show the modal.
    """

    def __init__(
        self: Self,
        bus: SessionEventBus,
        session_id: str,
        *,
        principal_id: str,
        store: StateStore | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._bus = bus
        self._session_id = session_id
        self._principal_id = principal_id
        self._store = store
        self._clock = clock
        self._pending: dict[str, asyncio.Future[ConfirmationResult]] = {}

    async def request(self: Self, req: ConfirmationRequest) -> ConfirmationResult:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ConfirmationResult] = loop.create_future()
        self._pending[req.subject_id] = future

        # Durably persist BEFORE awaiting so a process restart can recover the row.
        if self._store is not None:
            self._store.put_pending_confirmation(
                subject_id=req.subject_id,
                session_id=self._session_id,
                request_json=req.model_dump_json(),
                expires_at=req.expires_at,
            )

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
                approver_id=None,
                channel="web",
            )
        finally:
            self._pending.pop(req.subject_id, None)
            # Clear durable state regardless of how the future resolved.
            if self._store is not None:
                self._store.pop_pending_confirmation(req.subject_id)

    def has_pending(self: Self) -> bool:
        """True if this channel still holds an unresolved pending confirmation.

        Used by the web session-eviction sweep (app.py) to pin a session in
        memory rather than stranding a human waiting at the gate — pending
        confirmations carry their own `expires_at`, so a pinned session cannot
        pin memory indefinitely.
        """
        return bool(self._pending)

    def resolve(self: Self, subject_id: str, *, approved: bool) -> bool:
        """Resolve a pending confirmation request from the browser.

        Takes only the decision: the approver identity comes from this channel's
        server-issued `principal_id`, never from the caller.

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
                approver=_WEB_APPROVER,
                approver_id=self._principal_id,
                channel="web",
            )
        else:
            result = ConfirmationResult(
                approved=False,
                reason="declined",
                approved_at=None,
                approver=None,
                approver_id=None,
                channel="web",
            )
        future.set_result(result)
        # Also clear durable state eagerly; the request() finally block is belt-and-suspenders.
        if self._store is not None:
            self._store.pop_pending_confirmation(subject_id)
        return True
