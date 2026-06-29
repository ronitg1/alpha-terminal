"""Request-scoped current-user context (Phase 3, step 2).

The file/DB services (``app/backend/services/*``) are plain module-level
functions called from routes, the scan engine, and tests — they don't take a
``user_id`` argument. To make them multi-tenant without threading a parameter
through dozens of call sites, the owning user for the *current request* is held
in a :class:`contextvars.ContextVar`, set once at the request edge (by
:class:`app.backend.middleware.UserContextMiddleware`) and read deep in the stack
by :func:`current_user_id`.

Why a contextvar is correct here (and where the footguns are):

- ``contextvars`` are per-task/per-thread. The middleware sets the var for the
  whole ASGI request, so every service call — including the SSE
  ``StreamingResponse`` body, which runs *inside* the app call — observes it.
- ``asyncio.to_thread`` runs its target in a **copy** of the calling context
  (``contextvars.copy_context()``), so the var propagates into the morning-scan
  worker thread too. (The scan worker itself operates on an explicitly-passed
  sleeve config and does not read user-scoped stores, but result *persistence*
  runs back on the event loop in-context.)
- The default is :data:`DEFAULT_USER_ID`, so when auth is off (or in a direct
  unit-test call that never set the var) the services behave exactly as the
  single-tenant app does today — no behavior change, safe to ship dormant.

:data:`UNAUTHENTICATED_USER_ID` is a deliberately non-existent owner. When auth
is *on* but a request has no valid token, the middleware sets this sentinel
rather than the real default user — so a route that has not yet opted into the
:func:`app.backend.auth.get_current_user_id` dependency reads an *empty* dataset
instead of leaking the owner's data. Enforcement (the 401) still lives in the
dependency; this is defense in depth.
"""
from __future__ import annotations

from contextvars import ContextVar, Token

from app.backend.database.app_models import DEFAULT_USER_ID

__all__ = [
    "DEFAULT_USER_ID",
    "UNAUTHENTICATED_USER_ID",
    "current_user_id",
    "set_current_user_id",
    "reset_current_user_id",
    "current_user_email",
    "current_user_email_verified",
    "set_current_user_identity",
    "reset_current_user_identity",
]

# A sentinel owner id that no Clerk-issued account can hold (Clerk ids look like
# ``user_...``). Used when auth is on but the request is unauthenticated, so any
# un-gated store read returns nothing. (A future non-Clerk IdP should re-check
# this assumption.)
UNAUTHENTICATED_USER_ID = "__unauthenticated__"

_current_user_id: ContextVar[str] = ContextVar("current_user_id", default=DEFAULT_USER_ID)


def current_user_id() -> str:
    """The owner id for the current request. Defaults to :data:`DEFAULT_USER_ID`
    when nothing set it (auth off, or a non-request call site)."""
    return _current_user_id.get()


def set_current_user_id(user_id: str) -> Token[str]:
    """Bind the current-request owner id. Returns a token to pass to
    :func:`reset_current_user_id` so the binding is undone cleanly."""
    return _current_user_id.set(user_id)


def reset_current_user_id(token: Token[str]) -> None:
    """Undo a :func:`set_current_user_id`, restoring the previous binding."""
    _current_user_id.reset(token)


# The current request's email + whether it is verified — used to decide shared
# data-key access (the Massive/Finnhub allowlist). Default empty/false so a
# non-request call site is never treated as approved.
_current_user_email: ContextVar[str | None] = ContextVar("current_user_email", default=None)
_current_user_email_verified: ContextVar[bool] = ContextVar("current_user_email_verified", default=False)


def current_user_email() -> str | None:
    """The current request's (normalized) email, or None."""
    return _current_user_email.get()


def current_user_email_verified() -> bool:
    """Whether the current request's email is verified."""
    return _current_user_email_verified.get()


def set_current_user_identity(user_id: str, email: str | None, email_verified: bool) -> list[Token]:
    """Bind user id + email + verified for the request. Returns tokens for
    :func:`reset_current_user_identity`."""
    return [
        _current_user_id.set(user_id),
        _current_user_email.set(email),
        _current_user_email_verified.set(email_verified),
    ]


def reset_current_user_identity(tokens: list[Token]) -> None:
    """Undo :func:`set_current_user_identity`."""
    _current_user_id.reset(tokens[0])
    _current_user_email.reset(tokens[1])
    _current_user_email_verified.reset(tokens[2])
