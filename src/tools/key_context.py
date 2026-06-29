"""Request-scoped market-data provider keys (Phase 3 BYOK for Massive/Finnhub).

The Massive (Polygon) and Finnhub clients live in ``src`` and historically read
their key straight from ``os.environ`` — the owner's shared key. To support
per-user keys + an approved-emails allowlist for the shared key, the app layer
resolves the right key for the current request and binds it here; the clients
read it through :func:`massive_api_key` / :func:`finnhub_api_key`.

This module is deliberately dependency-free (no ``app`` imports) so ``src`` keeps
its layering. The binding is a context var, so it rides into the morning-scan
worker thread (``asyncio.to_thread`` copies the context).

Semantics:
- **Unset** (the default — CLI, tests, and the whole app when auth is off): the
  getters fall back to ``os.environ``, exactly as before.
- **Bound to a non-empty string**: that key is used (a per-user key, or the
  shared env key for an approved user).
- **Bound to an empty string**: the user has NO key for this provider and is not
  approved for the shared one — the getter returns "" and the client raises its
  "key not set" error, so data simply doesn't load for them.
"""
from __future__ import annotations

import os
from contextvars import ContextVar, Token

__all__ = [
    "massive_api_key",
    "finnhub_api_key",
    "financial_datasets_api_key",
    "set_provider_keys",
    "reset_provider_keys",
]

# A sentinel distinct from None/"" so "unset" (use env) is separable from
# "explicitly no key" ("").
_UNSET = "\x00__unset__"

_massive_key: ContextVar[str] = ContextVar("massive_api_key", default=_UNSET)
_finnhub_key: ContextVar[str] = ContextVar("finnhub_api_key", default=_UNSET)
# financialdatasets.ai — the legacy market-data fallback. Owner-shared only (no
# per-user BYOK for it), but it still must NOT be spendable by a non-approved
# user, so it is bound here too.
_fds_key: ContextVar[str] = ContextVar("financial_datasets_api_key", default=_UNSET)


def massive_api_key() -> str:
    """The Massive/Polygon key for the current request (bound value, else env)."""
    v = _massive_key.get()
    return os.environ.get("MASSIVE_API_KEY", "") if v == _UNSET else v


def finnhub_api_key() -> str:
    """The Finnhub key for the current request (bound value, else env)."""
    v = _finnhub_key.get()
    return os.environ.get("FINNHUB_API_KEY", "") if v == _UNSET else v


def financial_datasets_api_key() -> str:
    """The financialdatasets.ai key for the current request (bound value, else env)."""
    v = _fds_key.get()
    return os.environ.get("FINANCIAL_DATASETS_API_KEY", "") if v == _UNSET else v


def set_provider_keys(*, massive: str | None, finnhub: str | None, financial_datasets: str | None) -> list[Token]:
    """Bind the resolved per-request market-data keys. ``None`` -> ``""``
    (explicitly no key, do NOT fall back to env). Returns tokens for
    :func:`reset_provider_keys`."""
    return [
        _massive_key.set("" if massive is None else massive),
        _finnhub_key.set("" if finnhub is None else finnhub),
        _fds_key.set("" if financial_datasets is None else financial_datasets),
    ]


def reset_provider_keys(tokens: list[Token]) -> None:
    """Undo :func:`set_provider_keys`."""
    _massive_key.reset(tokens[0])
    _finnhub_key.reset(tokens[1])
    _fds_key.reset(tokens[2])
