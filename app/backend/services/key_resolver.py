"""Per-user-with-shared-fallback API key resolver (Phase 3, step 4).

For each provider, the key for the *current request's* user is resolved as:

    this user's stored (encrypted) key  ->  else the shared owner/env key

then a per-provider **policy** decides whether the shared env fallback is allowed:

- ``deepseek`` (LLM, usage-billed) — **no** shared fallback when auth is on: each
  user must bring their own key so they pay their own LLM spend.
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
from app.backend.context import current_user_id
from app.backend.crypto import DecryptionError, EncryptionNotConfigured
from app.backend.repositories.api_key_repository import ApiKeyRepository
from app.backend.services._storage import session_scope
from app.backend.services.api_key_validation import DEEPSEEK, FINNHUB, MASSIVE

logger = logging.getLogger(__name__)

__all__ = ["resolve_key", "require_key", "resolved_api_keys", "MissingUserKey"]

# Provider -> shared (owner/env) API key variable.
_ENV_VAR = {
    DEEPSEEK: "DEEPSEEK_API_KEY",
    MASSIVE: "MASSIVE_API_KEY",
    FINNHUB: "FINNHUB_API_KEY",
}

# Provider -> may fall back to the shared env key when auth is on. Flip a value
# to False to make that provider require a per-user key (no shared fallback).
_SHARED_FALLBACK = {
    DEEPSEEK: False,
    MASSIVE: True,
    FINNHUB: True,
}


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
        if _SHARED_FALLBACK.get(provider, False):
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


def resolved_api_keys() -> dict[str, str]:
    """The ``{ENV_VAR: key}`` dict for graph/agent callers (legacy hedge-fund +
    backtest) that pass an ``api_keys`` map to ``call_llm``/``get_model``.

    DeepSeek/Massive/Finnhub go through :func:`resolve_key` (per-user with the
    policy fallback); the remaining provider slots stay env pass-through so
    ``DATA_PROVIDER=fds`` and the legacy non-DeepSeek LLMs keep working. A missing
    required key resolves to ``""`` so ``get_model`` fails *closed* (it does not
    fall back to the shared env key when an explicit dict is supplied)."""
    return {
        "DEEPSEEK_API_KEY": resolve_key(DEEPSEEK) or "",
        "MASSIVE_API_KEY": resolve_key(MASSIVE) or "",
        "FINNHUB_API_KEY": resolve_key(FINNHUB) or "",
        "FINANCIAL_DATASETS_API_KEY": os.environ.get("FINANCIAL_DATASETS_API_KEY", ""),
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
    }
