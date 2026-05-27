"""Financial data API surface used by every agent.

The public functions (``get_prices``, ``get_financial_metrics``,
``search_line_items``, ``get_insider_trades``, ``get_company_news``,
``get_market_cap``, ``prices_to_df``) preserve the financialdatasets.ai
schema that the agents have been built around. Internally each function
dispatches to either:

* **Massive** (Polygon.io rebrand) — selected by ``DATA_PROVIDER=massive`` or
  by setting ``MASSIVE_API_KEY``. This is the default for rg-alpha-engine.
* **financialdatasets.ai** — the legacy/fallback provider, selected by
  ``DATA_PROVIDER=fds``.

Caching is provider-agnostic and unchanged.

Note on insider trades: Massive/Polygon does not publish bulk insider trade
data. When the active provider is Massive, ``get_insider_trades`` returns
an empty list and logs a one-time warning. Agents that rely on insider
trades (e.g. Burry) already null-check this list.
"""
from __future__ import annotations

import datetime
import logging
import os
import time
from typing import Iterable

import pandas as pd
import requests

logger = logging.getLogger(__name__)

from src.data.cache import get_cache
from src.data.models import (
    CompanyFactsResponse,
    CompanyNews,
    CompanyNewsResponse,
    FinancialMetrics,
    FinancialMetricsResponse,
    InsiderTrade,
    InsiderTradeResponse,
    LineItem,
    LineItemResponse,
    Price,
    PriceResponse,
)
from src.tools.massive import (
    MassiveClient,
    MassiveError,
    convert_company_facts,
    convert_company_news,
    convert_financial_metrics,
    convert_line_items,
    convert_prices,
)

_cache = get_cache()
_insider_warning_emitted = False


# ─── Provider selection ───────────────────────────────────────────────────────


def _provider() -> str:
    """Return the active data provider name (``"massive"`` or ``"fds"``)."""
    value = (os.environ.get("DATA_PROVIDER") or "").strip().lower()
    if value in {"massive", "polygon"}:
        return "massive"
    if value in {"fds", "financialdatasets"}:
        return "fds"
    # No explicit choice — fall back to whichever key the user set.
    if os.environ.get("MASSIVE_API_KEY"):
        return "massive"
    return "fds"


def _massive_client() -> MassiveClient:
    return MassiveClient()


# ─── HTTP plumbing (FDS only) ────────────────────────────────────────────────


def _make_fds_request(
    url: str,
    headers: dict,
    method: str = "GET",
    json_data: dict | None = None,
    max_retries: int = 3,
) -> requests.Response:
    """Issue a request to financialdatasets.ai with linear backoff on 429s."""
    for attempt in range(max_retries + 1):
        if method.upper() == "POST":
            response = requests.post(url, headers=headers, json=json_data)
        else:
            response = requests.get(url, headers=headers)

        if response.status_code == 429 and attempt < max_retries:
            delay = 60 + (30 * attempt)
            print(f"Rate limited (429). Attempt {attempt + 1}/{max_retries + 1}. Waiting {delay}s before retrying...")
            time.sleep(delay)
            continue
        return response

    # Unreachable — the loop always returns or continues, but mypy needs it.
    raise RuntimeError("_make_fds_request exited loop without returning")


def _fds_headers(api_key: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    key = api_key or os.environ.get("FINANCIAL_DATASETS_API_KEY")
    if key:
        headers["X-API-KEY"] = key
    return headers


# ─── Prices ──────────────────────────────────────────────────────────────────


def get_prices(ticker: str, start_date: str, end_date: str, api_key: str | None = None) -> list[Price]:
    """Daily OHLCV bars for ``ticker`` in ``[start_date, end_date]``."""
    cache_key = f"{ticker}_{start_date}_{end_date}"
    if cached_data := _cache.get_prices(cache_key):
        return [Price(**price) for price in cached_data]

    if _provider() == "massive":
        prices = _massive_prices(ticker, start_date, end_date)
    else:
        prices = _fds_prices(ticker, start_date, end_date, api_key)

    if not prices:
        return []
    _cache.set_prices(cache_key, [p.model_dump() for p in prices])
    return prices


def _massive_prices(ticker: str, start_date: str, end_date: str) -> list[Price]:
    try:
        response = _massive_client().get_daily_aggregates(ticker, start_date, end_date)
    except MassiveError as exc:
        logger.warning("Massive get_prices failed for %s: %s", ticker, exc)
        return []
    return convert_prices(response)


def _fds_prices(ticker: str, start_date: str, end_date: str, api_key: str | None) -> list[Price]:
    url = (
        f"https://api.financialdatasets.ai/prices/"
        f"?ticker={ticker}&interval=day&interval_multiplier=1"
        f"&start_date={start_date}&end_date={end_date}"
    )
    response = _make_fds_request(url, _fds_headers(api_key))
    if response.status_code != 200:
        return []
    try:
        return PriceResponse(**response.json()).prices
    except Exception as exc:
        logger.warning("Failed to parse FDS price response for %s: %s", ticker, exc)
        return []


# ─── Financial metrics ───────────────────────────────────────────────────────


def get_financial_metrics(
    ticker: str,
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
    api_key: str | None = None,
) -> list[FinancialMetrics]:
    """Pre-computed ratios + margins for ``ticker`` as of ``end_date``."""
    cache_key = f"{ticker}_{period}_{end_date}_{limit}"
    if cached_data := _cache.get_financial_metrics(cache_key):
        return [FinancialMetrics(**metric) for metric in cached_data]

    if _provider() == "massive":
        metrics = _massive_financial_metrics(ticker, end_date, period, limit)
    else:
        metrics = _fds_financial_metrics(ticker, end_date, period, limit, api_key)

    if not metrics:
        return []
    _cache.set_financial_metrics(cache_key, [m.model_dump() for m in metrics])
    return metrics


def _massive_financial_metrics(ticker: str, end_date: str, period: str, limit: int) -> list[FinancialMetrics]:
    client = _massive_client()
    try:
        ratios = client.get_ratios(ticker, limit=limit)
        # Pull the latest income statement to fill in margin fields the ratios
        # endpoint does not provide.
        income = client.get_income_statements(ticker, period_end_lte=end_date, limit=1)
    except MassiveError as exc:
        logger.warning("Massive get_financial_metrics failed for %s: %s", ticker, exc)
        return []

    latest_income: dict | None = None
    income_results = income.get("results") if income else None
    if income_results:
        latest_income = income_results[0]

    return convert_financial_metrics(
        ratios,
        ticker=ticker,
        period=period,
        latest_income=latest_income,
    )


def _fds_financial_metrics(
    ticker: str,
    end_date: str,
    period: str,
    limit: int,
    api_key: str | None,
) -> list[FinancialMetrics]:
    url = (
        f"https://api.financialdatasets.ai/financial-metrics/"
        f"?ticker={ticker}&report_period_lte={end_date}&limit={limit}&period={period}"
    )
    response = _make_fds_request(url, _fds_headers(api_key))
    if response.status_code != 200:
        return []
    try:
        return FinancialMetricsResponse(**response.json()).financial_metrics
    except Exception as exc:
        logger.warning("Failed to parse FDS financial metrics for %s: %s", ticker, exc)
        return []


# ─── Line items ──────────────────────────────────────────────────────────────


def search_line_items(
    ticker: str,
    line_items: list[str],
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
    api_key: str | None = None,
) -> list[LineItem]:
    """Fetch specific income/balance/cash-flow fields, joined by period."""
    if _provider() == "massive":
        return _massive_line_items(ticker, line_items, end_date, period, limit)
    return _fds_line_items(ticker, line_items, end_date, period, limit, api_key)


def _massive_line_items(
    ticker: str,
    fields: list[str],
    end_date: str,
    period: str,
    limit: int,
) -> list[LineItem]:
    client = _massive_client()
    timeframe = "trailing_twelve_months" if period == "ttm" else period
    try:
        income = client.get_income_statements(ticker, period_end_lte=end_date, timeframe=timeframe, limit=limit)
        balance = client.get_balance_sheets(ticker, period_end_lte=end_date, timeframe="quarterly", limit=limit)
        cashflow = client.get_cash_flow_statements(ticker, period_end_lte=end_date, timeframe=timeframe, limit=limit)
    except MassiveError as exc:
        logger.warning("Massive search_line_items failed for %s: %s", ticker, exc)
        return []

    return convert_line_items(
        ticker=ticker,
        period=period,
        requested_fields=fields,
        income_response=income,
        balance_response=balance,
        cashflow_response=cashflow,
        limit=limit,
    )


def _fds_line_items(
    ticker: str,
    line_items: list[str],
    end_date: str,
    period: str,
    limit: int,
    api_key: str | None,
) -> list[LineItem]:
    body = {
        "tickers": [ticker],
        "line_items": line_items,
        "end_date": end_date,
        "period": period,
        "limit": limit,
    }
    response = _make_fds_request(
        "https://api.financialdatasets.ai/financials/search/line-items",
        _fds_headers(api_key),
        method="POST",
        json_data=body,
    )
    if response.status_code != 200:
        return []
    try:
        return LineItemResponse(**response.json()).search_results[:limit]
    except Exception as exc:
        logger.warning("Failed to parse FDS line items for %s: %s", ticker, exc)
        return []


# ─── Insider trades ──────────────────────────────────────────────────────────


def get_insider_trades(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
    api_key: str | None = None,
) -> list[InsiderTrade]:
    """Form-4-style insider trades. Empty under the Massive provider."""
    if _provider() == "massive":
        global _insider_warning_emitted
        if not _insider_warning_emitted:
            logger.warning(
                "Massive/Polygon does not publish insider trades — returning []. "
                "Set DATA_PROVIDER=fds to use financialdatasets.ai for insider data."
            )
            _insider_warning_emitted = True
        return []

    cache_key = f"{ticker}_{start_date or 'none'}_{end_date}_{limit}"
    if cached_data := _cache.get_insider_trades(cache_key):
        return [InsiderTrade(**trade) for trade in cached_data]

    all_trades: list[InsiderTrade] = []
    current_end_date = end_date
    headers = _fds_headers(api_key)

    while True:
        url = f"https://api.financialdatasets.ai/insider-trades/?ticker={ticker}&filing_date_lte={current_end_date}"
        if start_date:
            url += f"&filing_date_gte={start_date}"
        url += f"&limit={limit}"
        response = _make_fds_request(url, headers)
        if response.status_code != 200:
            break
        try:
            insider_trades = InsiderTradeResponse(**response.json()).insider_trades
        except Exception as exc:
            logger.warning("Failed to parse FDS insider trades for %s: %s", ticker, exc)
            break
        if not insider_trades:
            break
        all_trades.extend(insider_trades)
        if not start_date or len(insider_trades) < limit:
            break
        current_end_date = min(trade.filing_date for trade in insider_trades).split("T")[0]
        if current_end_date <= start_date:
            break

    if not all_trades:
        return []
    _cache.set_insider_trades(cache_key, [t.model_dump() for t in all_trades])
    return all_trades


# ─── Company news ────────────────────────────────────────────────────────────


def get_company_news(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
    api_key: str | None = None,
) -> list[CompanyNews]:
    """Company news articles in ``[start_date, end_date]``."""
    cache_key = f"{ticker}_{start_date or 'none'}_{end_date}_{limit}"
    if cached_data := _cache.get_company_news(cache_key):
        return [CompanyNews(**news) for news in cached_data]

    if _provider() == "massive":
        news = _massive_news(ticker, start_date, end_date, limit)
    else:
        news = _fds_news(ticker, start_date, end_date, limit, api_key)

    if not news:
        return []
    _cache.set_company_news(cache_key, [n.model_dump() for n in news])
    return news


def _massive_news(
    ticker: str,
    start_date: str | None,
    end_date: str | None,
    limit: int,
) -> list[CompanyNews]:
    try:
        response = _massive_client().get_company_news(
            ticker, start_date=start_date, end_date=end_date, limit=min(limit, 1000)
        )
    except MassiveError as exc:
        logger.warning("Massive get_company_news failed for %s: %s", ticker, exc)
        return []
    return convert_company_news(response, ticker=ticker)


def _fds_news(
    ticker: str,
    start_date: str | None,
    end_date: str | None,
    limit: int,
    api_key: str | None,
) -> list[CompanyNews]:
    all_news: list[CompanyNews] = []
    current_end_date = end_date
    headers = _fds_headers(api_key)

    while True:
        url = f"https://api.financialdatasets.ai/news/?ticker={ticker}&end_date={current_end_date}"
        if start_date:
            url += f"&start_date={start_date}"
        url += f"&limit={limit}"
        response = _make_fds_request(url, headers)
        if response.status_code != 200:
            break
        try:
            company_news = CompanyNewsResponse(**response.json()).news
        except Exception as exc:
            logger.warning("Failed to parse FDS company news for %s: %s", ticker, exc)
            break
        if not company_news:
            break
        all_news.extend(company_news)
        if not start_date or len(company_news) < limit:
            break
        current_end_date = min(n.date for n in company_news).split("T")[0]
        if current_end_date <= start_date:
            break
    return all_news


# ─── Market cap ──────────────────────────────────────────────────────────────


def get_market_cap(ticker: str, end_date: str, api_key: str | None = None) -> float | None:
    """Market cap as of ``end_date`` (today → live; historical → from financials)."""
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    if end_date == today:
        if _provider() == "massive":
            try:
                details = _massive_client().get_ticker_details(ticker)
            except MassiveError as exc:
                logger.warning("Massive get_market_cap failed for %s: %s", ticker, exc)
                return None
            facts = convert_company_facts(details)
            return facts.market_cap if facts else None

        # FDS path
        headers = _fds_headers(api_key)
        url = f"https://api.financialdatasets.ai/company/facts/?ticker={ticker}"
        response = _make_fds_request(url, headers)
        if response.status_code != 200:
            print(f"Error fetching company facts: {ticker} - {response.status_code}")
            return None
        try:
            return CompanyFactsResponse(**response.json()).company_facts.market_cap
        except Exception as exc:
            logger.warning("Failed to parse FDS company facts for %s: %s", ticker, exc)
            return None

    metrics = get_financial_metrics(ticker, end_date, api_key=api_key)
    return metrics[0].market_cap if metrics else None


# ─── DataFrame helpers ───────────────────────────────────────────────────────


def prices_to_df(prices: Iterable[Price]) -> pd.DataFrame:
    """Convert a list of ``Price`` rows to a date-indexed DataFrame."""
    df = pd.DataFrame([p.model_dump() for p in prices])
    if df.empty:
        return df
    df["Date"] = pd.to_datetime(df["time"])
    df.set_index("Date", inplace=True)
    for col in ("open", "close", "high", "low", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.sort_index(inplace=True)
    return df


def get_price_data(ticker: str, start_date: str, end_date: str, api_key: str | None = None) -> pd.DataFrame:
    """Convenience wrapper returning prices as a DataFrame."""
    prices = get_prices(ticker, start_date, end_date, api_key=api_key)
    return prices_to_df(prices)
