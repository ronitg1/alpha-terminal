"""Canonical BYOK providers + validate-a-key-on-save (Phase 3).

A user's key is checked with a cheap live call to the provider before it is
stored, so a typo'd or revoked key fails fast at save time instead of surfacing
as a confusing scan/news error later. Each check uses a short timeout and reads
the key from the argument only — it is never logged.

Tests monkeypatch :func:`validate_provider_key` to avoid real network calls; the
per-provider helpers live behind it so the dispatch and allow-list stay testable.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# Canonical provider ids accepted by the BYOK endpoints. Kept lowercase and
# stable; the env var names they map to live in the key resolver (step 4).
DEEPSEEK = "deepseek"
MASSIVE = "massive"
FINNHUB = "finnhub"
OPENROUTER = "openrouter"
ROBINHOOD = "robinhood"
PROVIDERS: frozenset[str] = frozenset({DEEPSEEK, MASSIVE, FINNHUB, OPENROUTER, ROBINHOOD})

# How long to wait on a provider's validation endpoint before giving up.
_TIMEOUT_SECONDS = 10.0


class KeyValidationError(ValueError):
    """The provider rejected the key — it is genuinely bad (maps to HTTP 400)."""


class KeyValidationUnavailable(Exception):
    """The check could not be completed (provider down/slow/rate-limited). The
    key may be fine; the caller should retry later (maps to HTTP 503). Kept
    distinct from :class:`KeyValidationError` so a provider outage never tells a
    user their valid key is bad."""


def is_known_provider(provider: str) -> bool:
    return provider in PROVIDERS


def validate_provider_key(provider: str, key: str) -> None:
    """Verify ``key`` works for ``provider``.

    Raises :class:`KeyValidationError` if the provider authoritatively rejects the
    key, or :class:`KeyValidationUnavailable` if the check couldn't be completed."""
    if provider == DEEPSEEK:
        _check(key, "DeepSeek", "https://api.deepseek.com/models", headers={"Authorization": f"Bearer {key}"})
    elif provider == MASSIVE:
        _check(key, "Massive/Polygon", "https://api.polygon.io/v3/reference/tickers", params={"limit": 1, "apiKey": key})
    elif provider == FINNHUB:
        _check(key, "Finnhub", "https://finnhub.io/api/v1/quote", params={"symbol": "AAPL", "token": key})
    elif provider == OPENROUTER:
        _check(key, "OpenRouter", "https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
    elif provider == ROBINHOOD:
        _check_robinhood_mcp(key)
    else:
        raise KeyValidationError(f"Unknown provider '{provider}'.")


def _check(key: str, label: str, url: str, *, headers: dict | None = None, params: dict | None = None) -> None:
    """Make the validation GET and classify the result. 401/403 => bad key (400);
    transport error / 429 / 5xx => unavailable (503); any other non-200 is treated
    as a bad key (the request itself was malformed)."""
    try:
        # follow_redirects stays False so a provider redirect can never forward
        # the bearer token / apiKey to another host.
        resp = httpx.get(url, headers=headers, params=params, timeout=_TIMEOUT_SECONDS, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise KeyValidationUnavailable(f"Could not reach {label} to validate the key: {exc}") from exc

    if resp.status_code in (401, 403):
        raise KeyValidationError(f"{label} rejected this API key (unauthorized).")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise KeyValidationUnavailable(f"{label} is temporarily unavailable (HTTP {resp.status_code}); try again.")
    if resp.status_code != 200:
        raise KeyValidationError(f"{label} key check failed (HTTP {resp.status_code}).")


def _check_robinhood_mcp(key: str) -> None:
    from app.backend.services.robinhood_mcp import RobinhoodMcpError, parse_mcp_payload, robinhood_mcp_url

    try:
        endpoint = robinhood_mcp_url()
    except RobinhoodMcpError as exc:
        raise KeyValidationUnavailable(f"Robinhood MCP token check is misconfigured: {exc}") from exc
    try:
        resp = httpx.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "MCP-Protocol-Version": "2025-06-18",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "alpha-terminal", "version": "1.7.6"},
                },
            },
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise KeyValidationUnavailable(f"Could not reach Robinhood MCP to validate the token: {exc}") from exc

    if resp.status_code in (401, 403):
        raise KeyValidationError("Robinhood MCP rejected this token (unauthorized).")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise KeyValidationUnavailable(f"Robinhood MCP is temporarily unavailable (HTTP {resp.status_code}); try again.")
    if resp.status_code not in (200, 202):
        raise KeyValidationError(f"Robinhood MCP token check failed (HTTP {resp.status_code}).")
    if not resp.content:
        raise KeyValidationUnavailable("Robinhood MCP token check returned an empty response; try again.")
    try:
        payload = parse_mcp_payload(resp, expected_id=1)
    except RobinhoodMcpError as exc:
        raise KeyValidationUnavailable(f"Robinhood MCP token check could not be parsed: {exc}") from exc
    if payload.get("error"):
        raise KeyValidationError("Robinhood MCP rejected this token.")
