"""Per-user-with-shared-fallback API key resolver (Phase 3, step 4).

For each provider, the key for the *current request's* user is resolved as:

    this user's stored (encrypted) key  ->  else the shared owner/env key

then a per-provider **policy** decides whether the shared env fallback is allowed:

- ``deepseek`` (LLM, usage-billed) — **no** shared fallback when auth is on: each
  user must bring their own key so they pay their own LLM spend.
- ``robinhood`` (account access) — **no** shared fallback when auth is on: each
  user must explicitly connect their own MCP token.
- ``massive`` (Polygon market data) and ``finnhub`` (news) — **shared** fallback
  to the owner's env keys; users don't need to provide these today.

Making Massive/Finnhub require a per-user key later is a :data:`_SHARED_FALLBACK`
policy flip **plus** routing the Massive/Finnhub clients (``src/tools/massive/
client.py``, ``src/tools/finnhub/client.py``) through this resolver — today they
read ``os.environ`` directly, which is fine only because their policy is shared
fallback (the resolver would return the same env key anyway).

Dormant when auth is off: with ``AUTH_ENABLED`` unset the resolver never touches
the database and always returns the env key — identical to the single-tenant app
today (and it never needs ``API_KEY_ENCRYPTION_KEY`` unless a user has actually
stored a key). The current user comes from the request-scoped context var, so the
resolver is callable from anywhere without threading a ``user_id`` argument.

Note: BYOK keys always live in Postgres. With ``AUTH_ENABLED`` on, a per-user key
lookup opens a DB session regardless of ``STORAGE_BACKEND`` — so enabling auth
requires a configured database even if other state is file-backed.
"""
from __future__ import annotations

import logging
import os

from app.backend.auth import auth_enabled
from app.backend.context import (
    current_user_email,
    current_user_email_verified,
    current_user_id,
)
from app.backend.crypto import DecryptionError, EncryptionNotConfigured
from app.backend.repositories.api_key_repository import ApiKeyRepository
from app.backend.services._storage import session_scope
from app.backend.services.api_key_validation import DEEPSEEK, FINNHUB, MASSIVE, ROBINHOOD

logger = logging.getLogger(__name__)

__all__ = [
    "resolve_key",
    "require_key",
    "resolved_api_keys",
    "provider_keys_for_request",
    "is_shared_data_approved",
    "is_owner",
    "MissingUserKey",
]

# financialdatasets.ai — legacy market-data fallback. Not a per-user BYOK
# provider (users can't add it via /api-keys); it's owner-shared, so approved
# users get the env key and everyone else gets nothing.
FINANCIAL_DATASETS = "financial_datasets"

# Provider -> shared (owner/env) API key variable.
_ENV_VAR = {
    DEEPSEEK: "DEEPSEEK_API_KEY",
    ROBINHOOD: "ROBINHOOD_MCP_BEARER_TOKEN",
    MASSIVE: "MASSIVE_API_KEY",
    FINNHUB: "FINNHUB_API_KEY",
    FINANCIAL_DATASETS: "FINANCIAL_DATASETS_API_KEY",
}

# Provider -> may fall back to the shared env key when auth is on AND the user is
# approved for shared keys (see is_shared_data_approved). DeepSeek and
# Robinhood never share. Massive/Finnhub share only for the owner + an
# approved-emails allowlist; everyone else must bring their own.
_SHARED_FALLBACK = {
    DEEPSEEK: False,
    ROBINHOOD: False,
    MASSIVE: True,
    FINNHUB: True,
    FINANCIAL_DATASETS: True,
}


def _shared_data_emails() -> set[str]:
    """The approved-emails allowlist for the shared Massive/Finnhub keys, from
    ``SHARED_DATA_EMAILS`` (comma-separated), normalized lowercase."""
    raw = os.environ.get("SHARED_DATA_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_owner(user_id: str, email: str | None, email_verified: bool) -> bool:
    """Whether this user is the deployment owner — matched by the unspoofable
    ``OWNER_USER_ID`` sub, or a VERIFIED email equal to ``OWNER_EMAIL``. Used to
    gate owner-only actions (approving others' access requests)."""
    owner_sub = os.environ.get("OWNER_USER_ID", "").strip()
    if owner_sub and user_id == owner_sub:
        return True
    if not (email and email_verified):
        return False
    owner_email = os.environ.get("OWNER_EMAIL", "").strip().lower()
    return bool(owner_email and email == owner_email)


def _is_email_db_approved(email: str) -> bool:
    """Whether the owner has approved an access request for ``email`` (DB grant)."""
    try:
        with session_scope() as db:
            from app.backend.repositories.access_request_repository import AccessRequestRepository

            return AccessRequestRepository(db).is_email_approved(email)
    except Exception as exc:  # never let an approval-check failure crash a request
        logger.warning("Access-request approval check failed: %s", exc)
        return False


def is_shared_data_approved(user_id: str, email: str | None, email_verified: bool) -> bool:
    """Whether this user may use the OWNER's shared Massive/Finnhub keys.

    Approved if: the owner; or a VERIFIED email in the static ``SHARED_DATA_EMAILS``
    env allowlist; or a VERIFIED email the owner has approved via an access request
    (DB grant). An unverified email never qualifies, so an attacker on open signup
    can't spend the owner's market-data quota by claiming someone else's address."""
    if is_owner(user_id, email, email_verified):
        return True
    if not (email and email_verified):
        return False
    if email in _shared_data_emails():
        return True
    return _is_email_db_approved(email)


class MissingUserKey(Exception):
    """A required per-user key isn't available (auth on, user hasn't added it and
    the provider has no shared fallback). Drives the "add your DeepSeek key"
    soft gate at first LLM use."""

    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(f"No API key available for provider '{provider}'.")


def _env_key(provider: str) -> str | None:
    return os.environ.get(_ENV_VAR.get(provider, ""), "").strip() or None


def _user_key(provider: str) -> str | None:
    """This user's stored, decrypted key for ``provider`` (None if absent). A
    decryption failure is logged and treated as absent so the user is re-prompted
    rather than the request 500-ing deep in a scan."""
    try:
        with session_scope() as db:
            return ApiKeyRepository(db, current_user_id()).get_decrypted(provider)
    except (DecryptionError, EncryptionNotConfigured) as exc:
        logger.warning("Could not read stored key for provider '%s': %s", provider, exc)
        return None


def resolve_key(provider: str) -> str | None:
    """Resolve the active key for ``provider``: this user's key, else the shared
    env key when policy allows. Returns None when nothing is available (auth on,
    no user key, no shared fallback)."""
    if auth_enabled():
        user_key = _user_key(provider)
        if user_key:
            return user_key
        if _SHARED_FALLBACK.get(provider, False) and is_shared_data_approved(
            current_user_id(), current_user_email(), current_user_email_verified()
        ):
            return _env_key(provider)
        return None
    # Auth off: single-tenant — always the shared env key (dormant, no DB hit).
    return _env_key(provider)


def require_key(provider: str) -> str:
    """Like :func:`resolve_key` but raises :class:`MissingUserKey` when no key is
    available, for call sites that cannot proceed without one (e.g. DeepSeek)."""
    key = resolve_key(provider)
    if not key:
        raise MissingUserKey(provider)
    return key


def provider_keys_for_request(
    user_id: str, email: str | None, email_verified: bool
) -> tuple[str | None, str | None, str | None]:
    """Resolve (massive, finnhub, financial_datasets) for one request in a single
    DB read, applying the approved-emails policy. Called by the middleware to bind
    the per-request market-data keys (``src/tools/key_context``) so the data
    clients — including those in the scan worker thread — use the right key
    without an N+1 of per-call lookups. ``None`` for a provider means "no key
    available" (a non-approved user who hasn't supplied their own). FDS has no
    per-user BYOK, so it is env-if-approved else None."""
    stored: dict[str, str] = {}
    try:
        with session_scope() as db:
            stored = ApiKeyRepository(db, user_id).decrypted_map()
    except (DecryptionError, EncryptionNotConfigured) as exc:
        logger.warning("Could not read stored market-data keys for %s: %s", user_id, exc)
        stored = {}
    approved = is_shared_data_approved(user_id, email, email_verified)

    def pick(provider: str) -> str | None:
        if stored.get(provider):
            return stored[provider]
        return _env_key(provider) if approved else None

    # Finnhub is free-tier public data; always fall back to the shared env key
    # for any authenticated user regardless of the approval gate (which applies
    # to the paid Massive subscription). A user with a stored Finnhub key uses
    # their own; everyone else uses the owner's free key.
    finnhub_key = stored.get(FINNHUB) or _env_key(FINNHUB)
    fds = _env_key(FINANCIAL_DATASETS) if approved else None
    return pick(MASSIVE), finnhub_key, fds


def resolved_api_keys() -> dict[str, str]:
    """The ``{ENV_VAR: key}`` dict for graph/agent callers (legacy hedge-fund +
    backtest) that pass an ``api_keys`` map to ``call_llm``/``get_model``.

    DeepSeek/Massive/Finnhub go through :func:`resolve_key` (per-user with the
    policy fallback); the remaining provider slots stay env pass-through so
    ``DATA_PROVIDER=fds`` and the legacy non-DeepSeek LLMs keep working. A missing
    required key resolves to ``""`` so ``get_model`` fails *closed* (it does not
    fall back to the shared env key when an explicit dict is supplied)."""
    # Market-data slots go through resolve_key (per-user / approved-shared) so a
    # non-approved user's graph/backtest run can't reach the owner's shared keys
    # via this dict either. "" when unavailable -> fails closed downstream.
    return {
        "DEEPSEEK_API_KEY": resolve_key(DEEPSEEK) or "",
        "MASSIVE_API_KEY": resolve_key(MASSIVE) or "",
        "FINNHUB_API_KEY": resolve_key(FINNHUB) or "",
        "FINANCIAL_DATASETS_API_KEY": resolve_key(FINANCIAL_DATASETS) or "",
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
    }
