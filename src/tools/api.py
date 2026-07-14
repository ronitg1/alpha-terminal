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
from src.tools.key_context import financial_datasets_api_key, massive_api_key

_cache = get_cache()
_insider_warning_emitted = False


# ─── Provider selection ───────────────────────────────────────────────────────


def _provider() -> str:
    """Return the active data provider name (``"massive"`` or ``"fds"``).

    Legacy single-choice selector. New code should use ``_provider_for(...)``
    so price + news data routes through Massive (broad coverage) while
    fundamentals stay on FDS (Massive plan doesn't include ratios).
    """
    value = (os.environ.get("DATA_PROVIDER") or "").strip().lower()
    if value in {"massive", "polygon"}:
        return "massive"
    if value in {"fds", "financialdatasets"}:
        return "fds"
    # No explicit choice — fall back to whichever key the user set.
    if massive_api_key():
        return "massive"
    return "fds"


def _provider_for(data_type: str) -> str:
    """Per-data-type provider routing.

    The user's plan combos historically broke when ``DATA_PROVIDER=fds`` was
    blanket-applied: FDS doesn't cover newer / smaller tickers (ASTS, NBIS,
    etc.) so agents saw "no momentum, no fundamentals, no news" and abstained
    everywhere. Massive (Polygon) has prices + news + reference for the full
    US universe but doesn't include the Financials & Ratios expansion on
    the user's plan tier.

    Routing rules:
      * prices, news, market_cap, reference  → Massive (broad coverage)
      * fundamentals, line_items, insider    → FDS (Massive lacks them on plan)

    If ``MASSIVE_API_KEY`` isn't set, every type falls back to FDS. If
    ``FINANCIAL_DATASETS_API_KEY`` isn't set, fundamentals fall back to
    Massive (and likely return ``None``, but better than crashing).
    """
    explicit = (os.environ.get("DATA_PROVIDER") or "").strip().lower()
    has_massive = bool(massive_api_key())
    has_fds = bool(financial_datasets_api_key())

    massive_first = {"prices", "news", "market_cap"}
    fds_first = {"fundamentals", "line_items", "insider_trades"}

    if data_type in massive_first:
        if has_massive:
            return "massive"
        return "fds"
    if data_type in fds_first:
        if has_fds:
            return "fds"
        return "massive"
    # Fall back to the legacy global picker for anything else.
    if explicit:
        return _provider()
    return _provider()


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
    key = api_key or financial_datasets_api_key()
    if key:
        headers["X-API-KEY"] = key
    return headers


# ─── Prices ──────────────────────────────────────────────────────────────────


def get_prices(ticker: str, start_date: str, end_date: str, api_key: str | None = None) -> list[Price]:
    """Daily OHLCV bars for ``ticker`` in ``[start_date, end_date]``."""
    cache_key = f"{ticker}_{start_date}_{end_date}"
    if cached_data := _cache.get_prices(cache_key):
        return [Price(**price) for price in cached_data]

    # Per-data-type routing: prices always try Massive first (broad coverage
    # for tickers FDS misses — ASTS, NBIS, smaller caps). If Massive returns
    # empty (or isn't configured), fall back to FDS so legacy plans still work.
    primary = _provider_for("prices")
    if primary == "massive":
        prices = _massive_prices(ticker, start_date, end_date)
        if not prices and financial_datasets_api_key():
            prices = _fds_prices(ticker, start_date, end_date, api_key)
    else:
        prices = _fds_prices(ticker, start_date, end_date, api_key)
        if not prices and massive_api_key():
            prices = _massive_prices(ticker, start_date, end_date)

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

    # Fundamentals route via FDS first (Massive plan tier doesn't include
    # the Financials & Ratios expansion). Fall back to Massive if FDS misses
    # — better than nothing for the agents' downstream reasoning.
    primary = _provider_for("fundamentals")
    if primary == "fds":
        metrics = _fds_financial_metrics(ticker, end_date, period, limit, api_key)
        if not metrics and massive_api_key():
            metrics = _massive_financial_metrics(ticker, end_date, period, limit)
    else:
        metrics = _massive_financial_metrics(ticker, end_date, period, limit)
        if not metrics and financial_datasets_api_key():
            metrics = _fds_financial_metrics(ticker, end_date, period, limit, api_key)

    # Last resort: Finnhub's free-tier metric/all. This is what gives the agents
    # real fundamentals (margins, growth, turnover, ROE/ROA, valuation) when the
    # Massive plan lacks the ratios add-on and FDS isn't configured — otherwise
    # they reason over all-null data and report "no edge".
    if not metrics:
        metrics = _get_financial_metrics_finnhub(ticker, end_date, period)

    if not metrics:
        return []
    _cache.set_financial_metrics(cache_key, [m.model_dump() for m in metrics])
    return metrics


def _get_financial_metrics_finnhub(
    ticker: str, end_date: str, period: str
) -> list[FinancialMetrics]:
    """Finnhub free-tier fundamentals fallback. Empty list when not configured."""
    from src.tools.finnhub import get_finnhub_client
    from src.tools.finnhub.converters import finnhub_financial_metrics

    client = get_finnhub_client()
    if client is None:
        return []
    try:
        return finnhub_financial_metrics(client, ticker, end_date=end_date, period=period)
    except Exception as exc:  # noqa: BLE001
        logger.info("Finnhub financial-metrics fallback failed for %s: %s", ticker, exc)
        return []


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
    primary = _provider_for("line_items")
    if primary == "fds":
        items = _fds_line_items(ticker, line_items, end_date, period, limit, api_key)
        if not items and massive_api_key():
            items = _massive_line_items(ticker, line_items, end_date, period, limit)
        return items
    items = _massive_line_items(ticker, line_items, end_date, period, limit)
    if not items and financial_datasets_api_key():
        items = _fds_line_items(ticker, line_items, end_date, period, limit, api_key)
    return items


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


def _get_insider_trades_finnhub(
    ticker: str, end_date: str, start_date: str | None
) -> list[InsiderTrade] | None:
    """Finnhub free-tier insider fallback. Returns None when not configured or
    on error, so the caller can continue to its own empty-list handling."""
    from src.tools.finnhub import get_finnhub_client
    from src.tools.finnhub.client import FinnhubError
    from src.tools.finnhub.converters import finnhub_insider_trades

    client = get_finnhub_client()
    if client is None:
        return None
    try:
        payload = client.insider_transactions(
            ticker, start_date=start_date, end_date=end_date
        )
        return finnhub_insider_trades(ticker, payload)
    except (FinnhubError, ValueError, KeyError) as exc:
        logger.info("Finnhub insider fallback failed for %s: %s", ticker, exc)
        return None


def get_insider_trades(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
    api_key: str | None = None,
) -> list[InsiderTrade]:
    """Form-4-style insider trades. Massive doesn't publish them; route to
    FDS when available, fall back to Finnhub's free tier, else return empty."""
    if not financial_datasets_api_key():
        # Finnhub free tier publishes Form 4 data — use it as the fallback so
        # the Burry/Sentiment agents see real insider activity.
        finnhub_trades = _get_insider_trades_finnhub(ticker, end_date, start_date)
        if finnhub_trades is not None:
            return finnhub_trades

        global _insider_warning_emitted
        if not _insider_warning_emitted:
            logger.warning(
                "Insider trades unavailable: Massive/Polygon doesn't publish them, "
                "FINANCIAL_DATASETS_API_KEY is not set, and no FINNHUB_API_KEY "
                "fallback is configured. Returning [] for all future calls this session."
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

    primary = _provider_for("news")
    if primary == "massive":
        news = _massive_news(ticker, start_date, end_date, limit)
        if not news and financial_datasets_api_key():
            news = _fds_news(ticker, start_date, end_date, limit, api_key)
    else:
        news = _fds_news(ticker, start_date, end_date, limit, api_key)
        if not news and massive_api_key():
            news = _massive_news(ticker, start_date, end_date, limit)

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
        primary = _provider_for("market_cap")
        # Try the primary provider first, fall back to the other if it returns
        # None. Massive's ticker-reference endpoint covers the full US
        # universe; FDS company facts have spotty coverage for smaller names.
        if primary == "massive" and massive_api_key():
            try:
                details = _massive_client().get_ticker_details(ticker)
                facts = convert_company_facts(details)
                mcap = facts.market_cap if facts else None
                if mcap is not None:
                    return mcap
            except MassiveError as exc:
                logger.warning("Massive get_market_cap failed for %s: %s", ticker, exc)

        if financial_datasets_api_key():
            headers = _fds_headers(api_key)
            url = f"https://api.financialdatasets.ai/company/facts/?ticker={ticker}"
            response = _make_fds_request(url, headers)
            if response.status_code == 200:
                try:
                    mcap = CompanyFactsResponse(**response.json()).company_facts.market_cap
                    if mcap is not None:
                        return mcap
                except Exception as exc:
                    logger.warning("Failed to parse FDS company facts for %s: %s", ticker, exc)

        # Final fallback: try Massive even if primary was FDS, in case it
        # wasn't tried above.
        if primary != "massive" and massive_api_key():
            try:
                details = _massive_client().get_ticker_details(ticker)
                facts = convert_company_facts(details)
                return facts.market_cap if facts else None
            except MassiveError as exc:
                logger.warning("Massive get_market_cap fallback failed for %s: %s", ticker, exc)
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
