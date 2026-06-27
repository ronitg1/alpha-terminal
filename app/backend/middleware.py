"""ASGI middleware that binds the current-request user for the whole request.

This is the request edge for per-user data isolation (Phase 3, step 2). It is a
**pure ASGI** middleware (not ``BaseHTTPMiddleware``) on purpose: it sets the
:mod:`app.backend.context` context var in the *same* coroutine context that runs
the endpoint and streams the response, so the binding is visible everywhere
downstream — including the SSE ``StreamingResponse`` body and any
``asyncio.to_thread`` worker (which copies the context). ``BaseHTTPMiddleware``
runs the app in a separate task and has historically dropped context-var
propagation, which is exactly what we must avoid here.

It also records the resolved :class:`app.backend.auth.AuthResult` on
``scope["state"]`` so the :func:`app.backend.auth.get_current_user_id` dependency
reuses it instead of verifying the token a second time.

When auth is off (the default) this binds :data:`DEFAULT_USER_ID` — a no-op vs.
the context-var default — so the app is unchanged and this is safe to ship
dormant.
"""
from __future__ import annotations

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from app.backend.auth import resolve_auth
from app.backend.context import reset_current_user_id, set_current_user_id


class UserContextMiddleware:
    """Resolve auth once per request and bind the owner id to the context var."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only HTTP requests carry an Authorization header / need a user binding.
        # Websocket/lifespan scopes pass straight through.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        result = resolve_auth(headers.get("Authorization"))

        # Stash for get_current_user_id (avoids a second token verification).
        scope.setdefault("state", {})["auth"] = result

        token = set_current_user_id(result.user_id)
        try:
            await self.app(scope, receive, send)
        finally:
            # Belt-and-suspenders: ASGI servers that isolate each request in its
            # own context (uvicorn runs every request via contextvars.Context().run)
            # discard this binding at request end regardless. The reset keeps it
            # correct on servers that don't, and makes the lifecycle explicit.
            reset_current_user_id(token)
