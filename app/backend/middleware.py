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

from app.backend.auth import auth_enabled, resolve_auth
from app.backend.context import reset_current_user_identity, set_current_user_identity
from app.backend.services._storage import use_db
from src.tools.key_context import reset_provider_keys, set_provider_keys


class UserContextMiddleware:
    """Resolve auth once per request and bind the owner id + market-data keys."""

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

        identity_tokens = set_current_user_identity(result.user_id, result.email, result.email_verified)

        # Bind the per-request Massive/Finnhub keys so the (src) data clients —
        # including those in the scan worker thread — use this user's key, or the
        # shared key only if approved. Done once here (one DB read) to avoid an
        # N+1 of per-client lookups. Imported lazily to avoid an import cycle.
        key_tokens = None
        # BYOK keys live in the DB, so this only runs under the DB backend with
        # auth on — exactly the deployed multi-user config. Off/file backend: keys
        # stay unbound and the clients use the shared env keys (dormant).
        if auth_enabled() and use_db():
            from app.backend.services.key_resolver import provider_keys_for_request

            massive, finnhub, fds = provider_keys_for_request(
                result.user_id, result.email, result.email_verified
            )
            key_tokens = set_provider_keys(massive=massive, finnhub=finnhub, financial_datasets=fds)

        try:
            await self.app(scope, receive, send)
        finally:
            # Belt-and-suspenders cleanup (uvicorn isolates each request's context
            # anyway; this keeps it correct on servers that don't).
            reset_current_user_identity(identity_tokens)
            if key_tokens is not None:
                reset_provider_keys(key_tokens)
