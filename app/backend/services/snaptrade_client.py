"""Signed SnapTrade REST client for the read-only Fidelity pull.

SnapTrade (https://snaptrade.com) is a brokerage aggregator. Users authorize
read-only access to their brokerage (Fidelity) through SnapTrade's hosted
connection portal — we never see or store the user's brokerage credentials. This
module speaks SnapTrade's HTTP API directly with ``httpx`` (no SDK dependency, so
no ``poetry.lock`` change under Device Guard).

Auth model:
- **Owner-level** client credentials identify the *app* to SnapTrade:
  ``SNAPTRADE_CLIENT_ID`` and ``SNAPTRADE_CONSUMER_KEY`` (set in the environment,
  never per-user). The consumer key is the HMAC signing secret and is never sent.
- **Per-user** credentials identify one end user: a ``snaptrade_user_id`` (an id
  the app chooses at registration) plus a ``user_secret`` SnapTrade returns from
  ``registerUser`` (issued once — persisted encrypted by the connection service).

Request signing (validated against the live API): every call carries ``clientId``
and a unix ``timestamp`` as query params, and a ``Signature`` header equal to
``base64(HMAC-SHA256(consumer_key, json))`` where ``json`` is the compact,
key-sorted encoding of ``{"content": <body|null>, "path": <path>, "query":
<querystring>}``. ``content`` is the request body dict for POST/DELETE-with-body,
otherwise ``null``. The ``query`` value must be byte-identical to the query string
on the URL, so it is built once and reused for both signing and the request.

All functions raise :class:`SnapTradeNotConfigured` when the owner credentials are
absent (the feature is dormant), :class:`SnapTradeAuthRequired` on 401/403 (bad
signature or an unregistered/deleted user), and :class:`SnapTradeError` for any
other non-2xx or transport failure — never a silent swallow (repo convention).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.snaptrade.com"
_API_PREFIX = "/api/v1"
_TIMEOUT_SECONDS = 30.0

__all__ = [
    "snaptrade_configured",
    "register_user",
    "delete_user",
    "login_portal_url",
    "list_accounts",
    "list_positions",
    "list_option_holdings",
    "SnapTradeError",
    "SnapTradeNotConfigured",
    "SnapTradeAuthRequired",
]


class SnapTradeError(RuntimeError):
    """A SnapTrade request failed (non-2xx or transport error)."""


class SnapTradeNotConfigured(SnapTradeError):
    """``SNAPTRADE_CLIENT_ID`` / ``SNAPTRADE_CONSUMER_KEY`` are not set — the
    integration is dormant."""


class SnapTradeAuthRequired(SnapTradeError):
    """SnapTrade returned 401/403 — a bad signature, or the user is not
    registered/was deleted. The caller should re-register or reconnect."""


def _client_id() -> str:
    return os.environ.get("SNAPTRADE_CLIENT_ID", "").strip()


def _consumer_key() -> str:
    return os.environ.get("SNAPTRADE_CONSUMER_KEY", "").strip()


def snaptrade_configured() -> bool:
    """True when both owner-level credentials are present."""
    return bool(_client_id() and _consumer_key())


def _require_credentials() -> tuple[str, str]:
    client_id, consumer_key = _client_id(), _consumer_key()
    if not (client_id and consumer_key):
        raise SnapTradeNotConfigured(
            "SnapTrade is not configured. Set SNAPTRADE_CLIENT_ID and "
            "SNAPTRADE_CONSUMER_KEY in the environment."
        )
    return client_id, consumer_key


def _sign(consumer_key: str, path: str, query_string: str, body: dict[str, Any] | None) -> str:
    """Compute the SnapTrade ``Signature`` header value.

    The signed payload is the compact, key-sorted JSON of
    ``{"content": body_or_null, "path": path, "query": query_string}``. ``query``
    must match the URL's query string exactly, so the caller passes the same
    string it puts on the wire."""
    sig_object = {"content": body, "path": path, "query": query_string}
    sig_content = json.dumps(sig_object, separators=(",", ":"), sort_keys=True)
    digest = hmac.new(consumer_key.encode(), sig_content.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _request(
    method: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> Any:
    """Make one signed SnapTrade request and return the decoded JSON.

    ``path`` starts with ``/api/v1``. ``query`` holds the endpoint-specific query
    params (e.g. ``userId``/``userSecret``); ``clientId`` and ``timestamp`` are
    added here. ``body`` is the JSON request body (also the signed ``content``)."""
    client_id, consumer_key = _require_credentials()

    # clientId + timestamp are required on every call. Sort keys so the query
    # string is deterministic; the exact string is reused for signing and the URL.
    params = {"clientId": client_id, "timestamp": str(int(time.time()))}
    if query:
        params.update(query)
    query_string = urlencode(sorted(params.items()))

    signature = _sign(consumer_key, path, query_string, body)
    url = f"{_BASE_URL}{path}?{query_string}"
    headers = {
        "Signature": signature,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    try:
        resp = httpx.request(
            method,
            url,
            headers=headers,
            json=body if body is not None else None,
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise SnapTradeError(f"Could not reach SnapTrade: {exc}") from exc

    if resp.status_code in (401, 403):
        raise SnapTradeAuthRequired(
            "SnapTrade rejected the request (signature invalid or user not registered)."
        )
    if resp.status_code >= 400:
        detail = _safe_detail(resp)
        raise SnapTradeError(f"SnapTrade error (HTTP {resp.status_code}): {detail}")
    if not resp.content:
        return None
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise SnapTradeError("SnapTrade returned a non-JSON response.") from exc


def _safe_detail(resp: httpx.Response) -> str:
    """A short, non-secret error detail from a SnapTrade error response."""
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return resp.text[:200]
    if isinstance(data, dict):
        return str(data.get("detail") or data.get("message") or data.get("code") or data)[:200]
    return str(data)[:200]


# ─── endpoints ───────────────────────────────────────────────────────────────

def register_user(snaptrade_user_id: str) -> str:
    """Register ``snaptrade_user_id`` with SnapTrade and return the issued
    ``user_secret``. SnapTrade issues the secret exactly once, so the caller must
    persist it. Raises :class:`SnapTradeError` if the response lacks a secret."""
    data = _request(
        "POST",
        f"{_API_PREFIX}/snapTrade/registerUser",
        body={"userId": snaptrade_user_id},
    )
    secret = data.get("userSecret") if isinstance(data, dict) else None
    if not secret:
        raise SnapTradeError("SnapTrade registerUser did not return a userSecret.")
    return str(secret)


def delete_user(snaptrade_user_id: str) -> None:
    """Delete a SnapTrade user (used to reset a connection whose secret was lost).
    Idempotent-ish: SnapTrade returns 200 with a queued deletion."""
    _request(
        "DELETE",
        f"{_API_PREFIX}/snapTrade/deleteUser",
        query={"userId": snaptrade_user_id},
    )


def login_portal_url(
    snaptrade_user_id: str,
    user_secret: str,
    *,
    custom_redirect: str | None = None,
    broker: str | None = None,
) -> str:
    """Return a one-time SnapTrade connection-portal URL for the user to link a
    brokerage (Fidelity). ``custom_redirect`` is where the portal returns the user
    afterward; ``broker`` optionally deep-links to a specific institution."""
    body: dict[str, Any] = {}
    if custom_redirect:
        body["customRedirect"] = custom_redirect
    if broker:
        body["broker"] = broker
    data = _request(
        "POST",
        f"{_API_PREFIX}/snapTrade/login",
        query={"userId": snaptrade_user_id, "userSecret": user_secret},
        body=body or None,
    )
    redirect = data.get("redirectURI") if isinstance(data, dict) else None
    if not redirect:
        raise SnapTradeError("SnapTrade login did not return a redirect URL.")
    return str(redirect)


def list_accounts(snaptrade_user_id: str, user_secret: str) -> list[dict[str, Any]]:
    """List the user's connected brokerage accounts."""
    data = _request(
        "GET",
        f"{_API_PREFIX}/accounts",
        query={"userId": snaptrade_user_id, "userSecret": user_secret},
    )
    return data if isinstance(data, list) else []


def list_positions(
    snaptrade_user_id: str, user_secret: str, account_id: str
) -> list[dict[str, Any]]:
    """List stock/ETF positions for one connected account."""
    data = _request(
        "GET",
        f"{_API_PREFIX}/accounts/{account_id}/positions",
        query={"userId": snaptrade_user_id, "userSecret": user_secret},
    )
    return data if isinstance(data, list) else []


def list_option_holdings(
    snaptrade_user_id: str, user_secret: str, account_id: str
) -> list[dict[str, Any]]:
    """List option positions for one connected account (SnapTrade's separate
    option-holdings endpoint)."""
    data = _request(
        "GET",
        f"{_API_PREFIX}/accounts/{account_id}/options",
        query={"userId": snaptrade_user_id, "userSecret": user_secret},
    )
    if isinstance(data, list):
        return data
    # Some SnapTrade responses wrap option positions under a key.
    if isinstance(data, dict):
        for key in ("option_positions", "positions", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []
