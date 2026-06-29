"""Authentication seam — Clerk JWT verification behind the ``AUTH_ENABLED`` flag.

This mirrors the ``STORAGE_BACKEND`` cutover pattern (see
:mod:`app.backend.services._storage`): the flag is read **at call time** (not at
import) so a deploy can flip it via the environment and tests can toggle it
per-case. While the flag is **off** (the default), the whole module is dormant —
:func:`get_current_user_id` simply yields :data:`DEFAULT_USER_ID`, exactly the
single-tenant behavior every install has today. That keeps the local app and the
current cloud deploy unchanged and makes this safe to ship before Clerk is wired.

When the flag is **on**, :func:`get_current_user_id` requires a valid Clerk
session JWT in the ``Authorization: Bearer <token>`` header. Clerk signs session
tokens with RS256; we verify the signature against Clerk's published JWKS (public
keys), check expiry, and — when ``CLERK_ISSUER`` is configured — pin the issuer.
The Clerk user id (the token ``sub`` claim) becomes the request's ``user_id``.

Config (env vars, read at call time):

- ``AUTH_ENABLED``        — ``1``/``true``/``yes`` turns auth on. Default off.
- ``CLERK_JWKS_URL``      — explicit JWKS endpoint. If unset it is derived from
                            ``CLERK_ISSUER`` as ``<issuer>/.well-known/jwks.json``.
- ``CLERK_ISSUER``        — the token issuer (e.g. ``https://clerk.your-app.com``
                            or the Clerk frontend-API URL). When set, the issuer
                            claim is enforced; recommended in production.

A note on errors: a *client* problem (missing/expired/forged token) maps to
**401**. A *server* problem (auth turned on but no JWKS configured) maps to
**500** — a misconfiguration should fail loudly at deploy time, not look like a
bad token. The two are distinct exception types below so the dependency can map
them to the right status.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import HTTPException, Request
from jwt import PyJWKClient

from app.backend.context import DEFAULT_USER_ID, UNAUTHENTICATED_USER_ID

logger = logging.getLogger(__name__)

# How long (seconds) to wait on Clerk's JWKS endpoint before giving up. The
# fetch happens on the request hot path the first time a new signing key is
# seen, so a hung Clerk endpoint must not pin a worker indefinitely. Kept well
# under any reverse-proxy timeout.
_JWKS_TIMEOUT_SECONDS = 10.0

# Clock-skew tolerance (seconds) applied to `exp`. Clerk session tokens are
# short-lived (~60s) and refreshed aggressively client-side; a few seconds of
# skew between Clerk's signing clock and this server should not 401 an otherwise
# valid token.
_LEEWAY_SECONDS = 30

__all__ = [
    "auth_enabled",
    "get_current_user_id",
    "verify_clerk_token",
    "resolve_auth",
    "AuthResult",
    "ClerkAuthError",
    "ClerkConfigError",
]

# AuthResult.status values.
_STATUS_DISABLED = "disabled"          # auth off — single-tenant default user
_STATUS_OK = "ok"                      # valid token, user resolved
_STATUS_UNAUTHENTICATED = "unauthenticated"  # missing/invalid/expired token -> 401
_STATUS_MISCONFIGURED = "misconfigured"      # auth on but no Clerk JWKS -> 500


class ClerkAuthError(Exception):
    """The presented token is missing, malformed, expired, or fails signature/
    issuer verification — i.e. a client-side authentication failure (HTTP 401)."""


class ClerkConfigError(Exception):
    """Auth is enabled but the server has no Clerk JWKS configured — a server
    misconfiguration (HTTP 500), not a bad token."""


def auth_enabled() -> bool:
    """True when Clerk auth should be enforced. Read at call time so a deploy can
    flip ``AUTH_ENABLED`` via the environment and tests can toggle per-case."""
    return os.environ.get("AUTH_ENABLED", "").strip().lower() in ("1", "true", "yes")


def _clerk_issuer() -> str | None:
    """The configured Clerk issuer, or ``None`` if unset (issuer check skipped)."""
    value = os.environ.get("CLERK_ISSUER", "").strip()
    return value or None


def _jwks_url() -> str | None:
    """Resolve the JWKS endpoint: explicit ``CLERK_JWKS_URL`` wins, else derive
    it from ``CLERK_ISSUER``. ``None`` when neither is configured."""
    explicit = os.environ.get("CLERK_JWKS_URL", "").strip()
    if explicit:
        return explicit
    issuer = _clerk_issuer()
    if issuer:
        return f"{issuer.rstrip('/')}/.well-known/jwks.json"
    return None


# PyJWKClient caches the fetched keys internally and is safe to reuse, so we
# memoize one per JWKS URL rather than refetch on every request. Keyed by URL so
# a config change (or a test pointing at a different endpoint) gets a fresh
# client instead of a stale one.
_jwks_clients: dict[str, PyJWKClient] = {}


def _get_jwks_client() -> PyJWKClient:
    """Return a cached :class:`PyJWKClient` for the configured JWKS URL.

    Raises :class:`ClerkConfigError` when auth is on but no JWKS is configured —
    that is a deploy mistake, surfaced as a 500 by the dependency."""
    url = _jwks_url()
    if not url:
        raise ClerkConfigError(
            "AUTH_ENABLED is set but no Clerk JWKS is configured. Set CLERK_JWKS_URL "
            "or CLERK_ISSUER (see app/backend/auth.py)."
        )
    client = _jwks_clients.get(url)
    if client is None:
        # PyJWKClient caches the fetched JWK set for `lifespan` seconds (default
        # 300) and re-fetches after, which picks up Clerk's key rotation
        # automatically. `cache_keys` additionally memoizes resolved signing
        # keys. `timeout` bounds the on-request network fetch.
        client = PyJWKClient(url, cache_keys=True, lifespan=300, timeout=_JWKS_TIMEOUT_SECONDS)
        _jwks_clients[url] = client
    return client


def verify_clerk_token(token: str) -> dict[str, Any]:
    """Verify a Clerk session JWT and return its claims.

    Verifies the RS256 signature against Clerk's JWKS, requires ``exp`` and
    rejects expired tokens, and pins the issuer when ``CLERK_ISSUER`` is set.
    Raises :class:`ClerkAuthError` for any verification failure and
    :class:`ClerkConfigError` when the server has no JWKS configured."""
    client = _get_jwks_client()  # may raise ClerkConfigError (server misconfig)
    try:
        signing_key = client.get_signing_key_from_jwt(token)
    except ClerkConfigError:
        raise
    except Exception as exc:  # PyJWKClientError, decode errors locating the kid
        # Log the cause server-side but return a generic message — the raw
        # exception can carry library internals / URLs we don't want to echo to
        # an unauthenticated caller.
        logger.warning("Clerk token signing-key resolution failed: %s", exc)
        raise ClerkAuthError("Invalid or expired token") from exc

    issuer = _clerk_issuer()
    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            # Clerk session tokens carry no `aud` by default; pin the issuer
            # instead when it is configured. `require=["exp"]` makes a token with
            # no expiry fail closed rather than live forever. `leeway` absorbs
            # small clock skew on the short-lived `exp`.
            issuer=issuer,
            leeway=_LEEWAY_SECONDS,
            options={
                "verify_aud": False,
                "verify_iss": issuer is not None,
                "require": ["exp"],
            },
        )
    except Exception as exc:  # ExpiredSignatureError, InvalidIssuerError, etc.
        logger.warning("Clerk token verification failed: %s", exc)
        raise ClerkAuthError("Invalid or expired token") from exc

    return claims


def _extract_bearer(authorization: str | None) -> str | None:
    """Pull the token out of an ``Authorization: Bearer <token>`` header value, or
    ``None`` when it is absent or not a well-formed bearer credential."""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


@dataclass(frozen=True)
class AuthResult:
    """The outcome of resolving auth for one request.

    ``user_id`` is the owner to scope data to: the real Clerk ``sub`` when ``ok``,
    :data:`DEFAULT_USER_ID` when auth is ``disabled``, and
    :data:`UNAUTHENTICATED_USER_ID` (a non-existent owner) otherwise — so an
    un-gated route never reads the default user's data on a bad token."""

    user_id: str
    status: str
    detail: str = ""
    email: str | None = None  # from the token's `email` claim, when present
    email_verified: bool = False  # from `email_verified`; only a verified email may claim owner data


def resolve_auth(authorization: str | None) -> AuthResult:
    """Resolve the current request's auth from its ``Authorization`` header.

    Pure (no FastAPI types, no raising) so both the middleware and the
    :func:`get_current_user_id` dependency can share one code path. The dependency
    turns a non-OK status into the right HTTP error; the middleware just records
    the result and binds the context var."""
    if not auth_enabled():
        return AuthResult(DEFAULT_USER_ID, _STATUS_DISABLED)

    token = _extract_bearer(authorization)
    if not token:
        return AuthResult(
            UNAUTHENTICATED_USER_ID, _STATUS_UNAUTHENTICATED, "Missing or malformed bearer token"
        )

    try:
        claims = verify_clerk_token(token)
    except ClerkConfigError as exc:
        return AuthResult(UNAUTHENTICATED_USER_ID, _STATUS_MISCONFIGURED, str(exc))
    except ClerkAuthError as exc:
        return AuthResult(UNAUTHENTICATED_USER_ID, _STATUS_UNAUTHENTICATED, str(exc))

    sub = claims.get("sub")
    if not sub or not isinstance(sub, str):
        return AuthResult(
            UNAUTHENTICATED_USER_ID, _STATUS_UNAUTHENTICATED, "Token has no subject (sub) claim"
        )
    # `email` / `email_verified` are custom Clerk session-token claims (configure
    # them in the JWT template). Used only to match the data-claim owner; absence
    # is fine. The owner data-claim requires email_verified to be true, so an
    # attacker cannot claim by spoofing an unverified email they don't control.
    email = claims.get("email")
    email = email.strip().lower() if isinstance(email, str) and email.strip() else None
    email_verified = claims.get("email_verified") is True
    return AuthResult(sub, _STATUS_OK, email=email, email_verified=email_verified)


async def get_current_user_id(request: Request) -> str:
    """FastAPI dependency: the authenticated user's id for this request.

    - Auth **off** (default): returns :data:`DEFAULT_USER_ID` — single-tenant
      behavior, unchanged from today.
    - Auth **on**: requires a valid Clerk bearer token; returns its ``sub``
      (the Clerk user id). Missing/invalid/expired token -> 401. Server with no
      Clerk JWKS configured -> 500.

    Reuses the result :class:`app.backend.middleware.UserContextMiddleware`
    already computed for this request (stored on ``request.state.auth``); if the
    middleware did not run (e.g. a direct unit-test call), it resolves inline."""
    result: AuthResult | None = getattr(request.state, "auth", None)
    if result is None:
        result = resolve_auth(request.headers.get("Authorization"))

    if result.status == _STATUS_OK:
        # First-login provisioning (create row, claim owner data or seed starter).
        # Only under the DB backend; the in-process cache makes this ~free after
        # the first request. Imported lazily to avoid a module import cycle.
        from app.backend.services._storage import use_db

        if use_db():
            from app.backend.services.provisioning import ensure_provisioned

            ensure_provisioned(result.user_id, result.email, result.email_verified)
        return result.user_id
    if result.status == _STATUS_DISABLED:
        return result.user_id
    if result.status == _STATUS_MISCONFIGURED:
        # Server-side misconfiguration — fail loudly so it's caught at deploy.
        raise HTTPException(status_code=500, detail=result.detail)
    raise HTTPException(status_code=401, detail=result.detail or "Unauthorized")
