"""Same-origin check for state-changing web requests.

A request with an unsafe method that carries an `Origin` header must come from
the server's own origin, `f"{scheme}://{Host}"` (compared case-insensitively),
or it is refused with 403 before any route runs. Requests without `Origin`
pass: non-browser clients send none, and browsers always send it on POST.

This relies on `TrustedHostMiddleware` running first (outermost), which has
already confined `Host` to the allowlist; an `Origin` that matches `Host` is
therefore on an allowed host with the scheme and port the browser actually used.

A plain ASGI class rather than `BaseHTTPMiddleware`, which interferes with the
SSE stream on `/events`.

Layer: web/ only; imports starlette and structlog, nothing from the app.
"""

from __future__ import annotations

import structlog
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_log = structlog.get_logger(__name__)

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Same body as the session-auth 403, so a refusal reveals nothing about why.
_FORBIDDEN_BODY = {"detail": "forbidden"}


def _is_own_origin(origin: str, *, scheme: str, host: str) -> bool:
    return bool(host) and origin.lower() == f"{scheme}://{host}".lower()


class SameOriginMiddleware:
    """Refuse unsafe-method requests whose `Origin` is present and not our own."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin is None or _is_own_origin(
            origin, scheme=scope["scheme"], host=headers.get("host", "")
        ):
            await self.app(scope, receive, send)
            return
        # Method and path only: never the Origin value or any cookie (invariant 15).
        _log.warning("web.origin.rejected", method=scope["method"], path=scope["path"])
        response = JSONResponse(_FORBIDDEN_BODY, status_code=403)
        await response(scope, receive, send)
