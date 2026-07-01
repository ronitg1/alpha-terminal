"""HTTP client for Finnhub (free-tier backup for Massive's coverage gaps).

Wraps the small subset of REST endpoints we use as a fallback:

* ``/stock/insider-transactions``  — Form 4 buys/sells (Massive returns none)
* ``/stock/metric?metric=all``     — growth / turnover / DSO ratios Massive omits
* ``/stock/recommendation``        — analyst recommendation trends
* ``/stock/earnings``              — historical EPS actual-vs-estimate (beat/miss)
* ``/calendar/earnings``           — next earnings date
* ``/stock/peers``                 — peer tickers
* ``/stock/profile2``              — company profile
* ``/company-news`` / ``/news``    — per-ticker and macro news feeds
* ``/quote``                       — 20-min-delayed quote (free tier)

Auth is the ``token`` query param. Free tier is 60 calls/min (30/sec cap), so
callers should cache. Forward analyst estimates (price targets, revenue/EPS
estimates) are premium-gated and intentionally not exposed here.

Retries mirror the Massive client: exponential backoff with jitter on 429 and
5xx, capped at 5 attempts; 4xx-non-429 is terminal.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Any
from urllib.parse import urlencode

import requests

from src.tools.key_context import finnhub_api_key

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://finnhub.io/api/v1"
DEFAULT_TIMEOUT_SECONDS = 20
MAX_RETRY_ATTEMPTS = 4
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 20.0

# Finnhub free tier allows 60 calls/min (and 30/sec). We self-limit BELOW that
# so the burst of independent callers (left-nav names, Market financials, News,
# Portfolio snapshot, chat) can never collectively trip a 429 → long backoff.
# Sustained ~50/min with a small burst keeps us comfortably under the ceiling.
_FINNHUB_RATE_PER_SEC = 50.0 / 60.0  # ~0.83 tokens/sec → ~50/min sustained
_FINNHUB_BURST = 8.0  # worst-case minute ≈ 8 + 50 = 58, comfortably under 60


class _TokenBucket:
    """Thread-safe token bucket shared across all Finnhub calls in the process.

    Tokens refill continuously at ``rate`` per second up to ``capacity``. Each
    call consumes one. ``acquire`` blocks briefly for the next token; if the
    budget is deeply exhausted it returns False fast so the caller degrades
    gracefully (skip/empty) rather than queuing — excess work simply fills in on
    the next refresh once the budget recovers.
    """

    def __init__(self, rate: float, capacity: float) -> None:
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, max_wait: float = 2.0) -> bool:
        start = time.monotonic()
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                wait = (1.0 - self._tokens) / self.rate
            if (time.monotonic() - start) + wait > max_wait:
                return False
            time.sleep(min(wait, 0.2))


_finnhub_limiter = _TokenBucket(_FINNHUB_RATE_PER_SEC, _FINNHUB_BURST)


class FinnhubError(RuntimeError):
    """Raised when the Finnhub API returns a terminal error."""

    def __init__(self, status_code: int, message: str, url: str) -> None:
        super().__init__(f"{status_code} from {url}: {message}")
        self.status_code = status_code
        self.url = url
        self.message = message


class FinnhubClient:
    """Thin REST client for the Finnhub API.

    Each public method maps to one endpoint and returns the raw parsed JSON
    (a dict or a list, depending on the endpoint). Conversion to the
    agent-facing models lives in the callers / converters.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        # Per-request key (per-user BYOK / approved-shared) when bound; else env.
        self.api_key = api_key or finnhub_api_key()
        if not self.api_key:
            raise FinnhubError(0, "FINNHUB_API_KEY not set", base_url or DEFAULT_BASE_URL)
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    # ─── HTTP plumbing ──────────────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        # Shared rate-limit gate: keeps total Finnhub traffic under the free-tier
        # 60/min ceiling across every caller. If the budget is exhausted, fail
        # fast so the caller degrades gracefully instead of forcing a 429.
        if not _finnhub_limiter.acquire(max_wait=2.0):
            raise FinnhubError(429, "local rate limit — Finnhub 60/min budget exhausted", path)

        query = {k: v for k, v in (params or {}).items() if v is not None}
        query["token"] = self.api_key
        url = f"{self.base_url}{path}"
        full_url = f"{url}?{urlencode(query)}"
        # The URL with the token is never logged — we log the path only.

        for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
            try:
                response = self._session.get(full_url, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt == MAX_RETRY_ATTEMPTS:
                    raise FinnhubError(0, f"network error: {exc}", path) from exc
                self._sleep_for_retry(attempt, reason=f"network: {exc.__class__.__name__}")
                continue

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise FinnhubError(200, f"non-JSON body: {exc}", path) from exc

            if response.status_code == 429 or response.status_code >= 500:
                if attempt == MAX_RETRY_ATTEMPTS:
                    raise FinnhubError(response.status_code, response.text[:300], path)
                self._sleep_for_retry(attempt, reason=str(response.status_code))
                continue

            # 4xx other than 429 — terminal (403 = premium endpoint, 401 = bad key).
            raise FinnhubError(response.status_code, response.text[:300], path)

        raise FinnhubError(0, "exhausted retries", path)

    @staticmethod
    def _sleep_for_retry(attempt: int, *, reason: str) -> None:
        delay = min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)))
        jitter = delay * 0.25
        sleep_for = max(0.0, delay + random.uniform(-jitter, jitter))
        logger.warning("Finnhub retry %d (%s) — sleeping %.2fs", attempt, reason, sleep_for)
        time.sleep(sleep_for)

    # ─── Fundamentals / insider (the Massive gaps) ──────────────────────────

    def basic_financials(self, ticker: str) -> dict[str, Any]:
        """`/stock/metric?metric=all` — 130+ ratios incl. growth/turnover/DSO."""
        return self._get("/stock/metric", {"symbol": ticker.upper(), "metric": "all"})

    def insider_transactions(
        self, ticker: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> dict[str, Any]:
        """`/stock/insider-transactions` — Form 4 transactions."""
        return self._get(
            "/stock/insider-transactions",
            {"symbol": ticker.upper(), "from": start_date, "to": end_date},
        )

    def insider_sentiment(
        self, ticker: str, *, start_date: str, end_date: str
    ) -> dict[str, Any]:
        """`/stock/insider-sentiment` — monthly aggregated insider MSPR."""
        return self._get(
            "/stock/insider-sentiment",
            {"symbol": ticker.upper(), "from": start_date, "to": end_date},
        )

    def recommendation_trends(self, ticker: str) -> list[dict[str, Any]]:
        """`/stock/recommendation` — strongBuy/buy/hold/sell/strongSell by month."""
        return self._get("/stock/recommendation", {"symbol": ticker.upper()})

    def earnings_surprises(self, ticker: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        """`/stock/earnings` — historical EPS actual vs estimate (beat/miss)."""
        return self._get("/stock/earnings", {"symbol": ticker.upper(), "limit": limit})

    def earnings_calendar(
        self, *, start_date: str, end_date: str, ticker: str | None = None
    ) -> dict[str, Any]:
        """`/calendar/earnings` — upcoming earnings dates."""
        return self._get(
            "/calendar/earnings",
            {"from": start_date, "to": end_date, "symbol": ticker.upper() if ticker else None},
        )

    def peers(self, ticker: str) -> list[str]:
        """`/stock/peers` — list of peer tickers."""
        return self._get("/stock/peers", {"symbol": ticker.upper()})

    def company_profile(self, ticker: str) -> dict[str, Any]:
        """`/stock/profile2` — company profile / industry / market cap."""
        return self._get("/stock/profile2", {"symbol": ticker.upper()})

    def quote(self, ticker: str) -> dict[str, Any]:
        """`/quote` — 20-min-delayed quote (free tier)."""
        return self._get("/quote", {"symbol": ticker.upper()})

    def symbol_search(self, query: str) -> dict[str, Any]:
        """`/search` — symbol/company typeahead lookup (free tier)."""
        return self._get("/search", {"q": query})

    # ─── News ────────────────────────────────────────────────────────────────

    def company_news(
        self, ticker: str, *, start_date: str, end_date: str
    ) -> list[dict[str, Any]]:
        """`/company-news` — per-ticker news in a date window (YYYY-MM-DD)."""
        return self._get(
            "/company-news",
            {"symbol": ticker.upper(), "from": start_date, "to": end_date},
        )

    def market_news(self, category: str = "general") -> list[dict[str, Any]]:
        """`/news` — macro/general market news feed."""
        return self._get("/news", {"category": category})


def is_finnhub_configured() -> bool:
    """True when a Finnhub key is available for the CURRENT request (per-user key,
    or the shared key if approved). Reads the request-scoped key context, NOT the
    raw env, so a non-approved user correctly sees no Finnhub."""
    return bool(finnhub_api_key().strip())


def get_finnhub_client() -> FinnhubClient | None:
    """Return a Finnhub client for the current request, or None when no key is
    available. Constructed PER CALL (not a process-wide singleton) so each
    request uses its own resolved key — a shared singleton would pin the first
    caller's key and leak it to everyone else. The rate limiter is a module
    global, so throttling is unaffected by per-call construction.

    Callers use the None return to fall back to their existing behavior, so
    Finnhub is strictly additive — the app is fully functional without it.
    """
    if not is_finnhub_configured():
        return None
    try:
        return FinnhubClient()
    except FinnhubError:
        return None
