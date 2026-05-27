"""HTTP client for Massive (Polygon.io rebrand).

Wraps the small subset of REST endpoints we actually use:

* ``/v2/aggs/...``                          — daily price aggregates
* ``/v2/reference/news``                    — company news
* ``/v3/reference/tickers/{ticker}``        — ticker reference / market cap
* ``/stocks/financials/v1/income-statements``
* ``/stocks/financials/v1/balance-sheets``
* ``/stocks/financials/v1/cash-flow-statements``
* ``/stocks/financials/v1/ratios``

Auth uses the ``Authorization: Bearer …`` header (Polygon accepts both the
header and the ``apiKey`` query param; the header keeps URLs clean and
prevents the key from leaking into logs).

Retries: exponential backoff on 429 and 5xx, capped at 5 attempts. We treat
4xx-non-429 as terminal — there is no point retrying a 401 or 404.
"""
from __future__ import annotations

import logging
import os
import random
import time
from typing import Any
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.polygon.io"
DEFAULT_TIMEOUT_SECONDS = 30
MAX_RETRY_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0


class MassiveError(RuntimeError):
    """Raised when the Massive API returns a terminal error."""

    def __init__(self, status_code: int, message: str, url: str) -> None:
        super().__init__(f"{status_code} from {url}: {message}")
        self.status_code = status_code
        self.url = url
        self.message = message


class MassiveClient:
    """Thin REST client for the Massive (Polygon) API.

    The client is intentionally stateless beyond config — there is no
    connection pool here that would surprise callers. Each public method maps
    to one endpoint and returns the raw parsed JSON (a dict). Conversion to
    the agent-facing pydantic models lives in :mod:`converters`.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = api_key or os.environ.get("MASSIVE_API_KEY", "")
        if not self.api_key:
            raise MassiveError(0, "MASSIVE_API_KEY not set", base_url or DEFAULT_BASE_URL)
        self.base_url = (base_url or os.environ.get("MASSIVE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    # ─── HTTP plumbing ──────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        # Drop None values so callers can pass optional params uniformly.
        query = {k: v for k, v in (params or {}).items() if v is not None}
        full_url = f"{url}?{urlencode(query)}" if query else url

        for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
            try:
                response = self._session.get(full_url, headers=self._headers(), timeout=self.timeout)
            except requests.RequestException as exc:
                # Network errors get the same backoff treatment as 5xx.
                if attempt == MAX_RETRY_ATTEMPTS:
                    raise MassiveError(0, f"network error: {exc}", full_url) from exc
                self._sleep_for_retry(attempt, reason=f"network: {exc.__class__.__name__}")
                continue

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise MassiveError(200, f"non-JSON body: {exc}", full_url) from exc

            # Retry on 429 (rate-limited) and 5xx.
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == MAX_RETRY_ATTEMPTS:
                    raise MassiveError(response.status_code, response.text[:500], full_url)
                self._sleep_for_retry(attempt, reason=f"{response.status_code}")
                continue

            # 4xx other than 429 — terminal.
            raise MassiveError(response.status_code, response.text[:500], full_url)

        raise MassiveError(0, "exhausted retries", full_url)

    @staticmethod
    def _sleep_for_retry(attempt: int, *, reason: str) -> None:
        delay = min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)))
        jitter = delay * 0.25
        sleep_for = max(0.0, delay + random.uniform(-jitter, jitter))
        logger.warning("Massive retry %d (%s) — sleeping %.2fs", attempt, reason, sleep_for)
        time.sleep(sleep_for)

    # ─── Endpoint wrappers ──────────────────────────────────────────────────

    def get_daily_aggregates(
        self,
        ticker: str,
        from_date: str,
        to_date: str,
        *,
        adjusted: bool = True,
        limit: int = 5000,
    ) -> dict[str, Any]:
        """Daily OHLCV bars between ``from_date`` and ``to_date`` (inclusive).

        Polygon returns at most 5000 rows per call — that's ~20 years of
        daily bars, so we don't bother paginating here.
        """
        path = f"/v2/aggs/ticker/{ticker.upper()}/range/1/day/{from_date}/{to_date}"
        return self._get(path, {"adjusted": str(adjusted).lower(), "sort": "asc", "limit": limit})

    def get_ticker_details(self, ticker: str) -> dict[str, Any]:
        """Reference data + current market cap for one ticker."""
        return self._get(f"/v3/reference/tickers/{ticker.upper()}")

    def get_company_news(
        self,
        ticker: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """News articles tagged for ``ticker`` over the given date range."""
        params: dict[str, Any] = {"ticker": ticker.upper(), "limit": limit, "order": "desc"}
        if start_date:
            params["published_utc.gte"] = start_date
        if end_date:
            params["published_utc.lte"] = end_date
        return self._get("/v2/reference/news", params)

    def get_income_statements(
        self,
        ticker: str,
        *,
        period_end_lte: str | None = None,
        timeframe: str = "trailing_twelve_months",
        limit: int = 10,
    ) -> dict[str, Any]:
        """Income statements, newest first."""
        params: dict[str, Any] = {
            "tickers": ticker.upper(),
            "timeframe": timeframe,
            "limit": limit,
            "sort": "period_end.desc",
        }
        if period_end_lte:
            params["period_end.lte"] = period_end_lte
        return self._get("/stocks/financials/v1/income-statements", params)

    def get_balance_sheets(
        self,
        ticker: str,
        *,
        period_end_lte: str | None = None,
        timeframe: str = "quarterly",
        limit: int = 10,
    ) -> dict[str, Any]:
        """Balance sheets, newest first. Balance sheets don't have a TTM
        notion, so the default timeframe is quarterly."""
        params: dict[str, Any] = {
            "tickers": ticker.upper(),
            "timeframe": timeframe,
            "limit": limit,
            "sort": "period_end.desc",
        }
        if period_end_lte:
            params["period_end.lte"] = period_end_lte
        return self._get("/stocks/financials/v1/balance-sheets", params)

    def get_cash_flow_statements(
        self,
        ticker: str,
        *,
        period_end_lte: str | None = None,
        timeframe: str = "trailing_twelve_months",
        limit: int = 10,
    ) -> dict[str, Any]:
        """Cash-flow statements, newest first."""
        params: dict[str, Any] = {
            "tickers": ticker.upper(),
            "timeframe": timeframe,
            "limit": limit,
            "sort": "period_end.desc",
        }
        if period_end_lte:
            params["period_end.lte"] = period_end_lte
        return self._get("/stocks/financials/v1/cash-flow-statements", params)

    def get_ratios(
        self,
        ticker: str,
        *,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Pre-computed financial ratios — PE, PB, ROE, etc."""
        return self._get(
            "/stocks/financials/v1/ratios",
            {"ticker": ticker.upper(), "limit": limit},
        )
