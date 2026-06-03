"""Convert Finnhub payloads into this project's models and compact summaries.

Two consumers:
  * ``finnhub_insider_trades`` — maps `/stock/insider-transactions` into the
    existing ``InsiderTrade`` model so the Burry/Sentiment agents see real Form
    4 data where Massive returns nothing.
  * ``fundamentals_summary`` — a compact, LLM- and UI-friendly dict combining
    growth/turnover metrics, the earnings beat/miss track record, analyst
    recommendation consensus, peers, and recent insider flow. Reused by the
    Market-tab enrichment endpoint and the Portfolio Pulse thesis/agent context.
"""

from __future__ import annotations

import logging
from typing import Any

from src.data.models import FinancialMetrics, InsiderTrade
from src.tools.finnhub.client import FinnhubClient, FinnhubError

logger = logging.getLogger(__name__)

# Finnhub metric keys we surface, mapped to friendly labels. These are exactly
# the growth/turnover/DSO fields Massive's /ratios endpoint omits, plus the
# headline valuation + quality ratios.
_METRIC_FIELDS: dict[str, str] = {
    "revenueGrowthTTMYoy": "revenue_growth_ttm",
    "revenueGrowth5Y": "revenue_growth_5y",
    "epsGrowthTTMYoy": "eps_growth_ttm",
    "epsGrowth5Y": "eps_growth_5y",
    "focfCagr5Y": "fcf_cagr_5y",
    "netProfitMarginTTM": "net_margin_ttm",
    "grossMarginTTM": "gross_margin_ttm",
    "operatingMarginTTM": "operating_margin_ttm",
    "roeTTM": "roe_ttm",
    "roaTTM": "roa_ttm",
    "assetTurnoverTTM": "asset_turnover_ttm",
    "inventoryTurnoverTTM": "inventory_turnover_ttm",
    "receivablesTurnoverTTM": "receivables_turnover_ttm",
    "currentRatioQuarterly": "current_ratio",
    "totalDebt/totalEquityQuarterly": "debt_to_equity",
    "peTTM": "pe_ttm",
    "pbQuarterly": "pb",
    "psTTM": "ps_ttm",
    "dividendYieldIndicatedAnnual": "dividend_yield",
    "52WeekHigh": "week_52_high",
    "52WeekLow": "week_52_low",
    "beta": "beta",
}


def finnhub_insider_trades(ticker: str, payload: dict[str, Any]) -> list[InsiderTrade]:
    """Map a `/stock/insider-transactions` payload into InsiderTrade models."""
    rows = payload.get("data") or []
    trades: list[InsiderTrade] = []
    for r in rows:
        change = r.get("change")  # signed share delta
        price = r.get("transactionPrice")
        shares_after = r.get("share")
        value = (change * price) if (change is not None and price) else None
        shares_before = (
            (shares_after - change)
            if (shares_after is not None and change is not None)
            else None
        )
        filing_date = r.get("filingDate") or r.get("transactionDate") or ""
        if not filing_date:
            continue  # filing_date is required by the model
        trades.append(
            InsiderTrade(
                ticker=ticker.upper(),
                issuer=None,
                name=r.get("name"),
                title=None,  # not provided on Finnhub's free tier
                is_board_director=None,
                transaction_date=r.get("transactionDate"),
                transaction_shares=change,
                transaction_price_per_share=price,
                transaction_value=value,
                shares_owned_before_transaction=shares_before,
                shares_owned_after_transaction=shares_after,
                security_title=None,
                filing_date=filing_date,
            )
        )
    return trades


# Every FinancialMetrics field (all are float|None with no default → must be
# supplied). We start from None and fill what Finnhub provides.
_FM_FIELDS = (
    "market_cap enterprise_value price_to_earnings_ratio price_to_book_ratio "
    "price_to_sales_ratio enterprise_value_to_ebitda_ratio enterprise_value_to_revenue_ratio "
    "free_cash_flow_yield peg_ratio gross_margin operating_margin net_margin "
    "return_on_equity return_on_assets return_on_invested_capital asset_turnover "
    "inventory_turnover receivables_turnover days_sales_outstanding operating_cycle "
    "working_capital_turnover current_ratio quick_ratio cash_ratio operating_cash_flow_ratio "
    "debt_to_equity debt_to_assets interest_coverage revenue_growth earnings_growth "
    "book_value_growth earnings_per_share_growth free_cash_flow_growth operating_income_growth "
    "ebitda_growth payout_ratio earnings_per_share book_value_per_share free_cash_flow_per_share"
).split()

_PCT = 0.01  # Finnhub reports margins/growth/returns as percent; models use fractions


def finnhub_financial_metrics(
    client: FinnhubClient, ticker: str, *, end_date: str, period: str = "ttm"
) -> list[FinancialMetrics]:
    """Map Finnhub `metric/all` into a single FinancialMetrics.

    The fallback that lets agents see fundamentals when Massive's plan omits
    the ratios add-on. Percent-style fields (margins, growth, ROE/ROA, payout)
    are scaled to fractions to match the FDS/Massive convention the agents
    reason against; multiples (P/E, turnover, current ratio, D/E) pass through.
    """
    try:
        data = client.basic_financials(ticker)
    except (FinnhubError, KeyError, TypeError, ValueError):
        return []
    m = data.get("metric") or {}
    if not m:
        return []

    def g(key: str, scale: float = 1.0) -> float | None:
        v = m.get(key)
        return float(v) * scale if isinstance(v, (int, float)) else None

    def first(keys: list[str], scale: float = 1.0) -> float | None:
        for k in keys:
            val = g(k, scale)
            if val is not None:
                return val
        return None

    fields: dict[str, Any] = {f: None for f in _FM_FIELDS}
    fields.update(
        price_to_earnings_ratio=g("peTTM"),
        price_to_book_ratio=first(["pbQuarterly", "pbAnnual"]),
        price_to_sales_ratio=first(["psTTM", "psAnnual"]),
        enterprise_value_to_ebitda_ratio=g("enterpriseValueToEbitdaTTM"),
        peg_ratio=g("pegTTM"),
        gross_margin=g("grossMarginTTM", _PCT),
        operating_margin=g("operatingMarginTTM", _PCT),
        net_margin=g("netProfitMarginTTM", _PCT),
        return_on_equity=g("roeTTM", _PCT),
        return_on_assets=g("roaTTM", _PCT),
        return_on_invested_capital=g("roiTTM", _PCT),
        asset_turnover=g("assetTurnoverTTM"),
        inventory_turnover=g("inventoryTurnoverTTM"),
        receivables_turnover=g("receivablesTurnoverTTM"),
        current_ratio=first(["currentRatioQuarterly", "currentRatioAnnual"]),
        quick_ratio=first(["quickRatioQuarterly", "quickRatioAnnual"]),
        debt_to_equity=first(["totalDebt/totalEquityQuarterly", "totalDebt/totalEquityAnnual"]),
        debt_to_assets=first(["totalDebt/totalAssetsQuarterly", "totalDebt/totalAssetsAnnual"]),
        interest_coverage=g("netInterestCoverageTTM"),
        revenue_growth=g("revenueGrowthTTMYoy", _PCT),
        earnings_growth=g("epsGrowthTTMYoy", _PCT),
        earnings_per_share_growth=g("epsGrowthTTMYoy", _PCT),
        book_value_growth=g("bookValueShareGrowth5Y", _PCT),
        free_cash_flow_growth=g("focfCagr5Y", _PCT),
        payout_ratio=g("payoutRatioTTM", _PCT),
        earnings_per_share=first(["epsTTM", "epsAnnual"]),
        book_value_per_share=first(["bookValuePerShareQuarterly", "bookValuePerShareAnnual"]),
        free_cash_flow_per_share=g("freeCashFlowPerShareTTM"),
    )
    return [
        FinancialMetrics(
            ticker=ticker.upper(),
            report_period=end_date,
            period=period,
            currency="USD",
            **fields,
        )
    ]


def _select_metrics(metric: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for src_key, friendly in _METRIC_FIELDS.items():
        v = metric.get(src_key)
        if isinstance(v, (int, float)):
            out[friendly] = float(v)
    return out


def _insider_flow(payload: dict[str, Any]) -> dict[str, Any]:
    """Summarize recent insider transactions into net shares + buy/sell counts."""
    rows = payload.get("data") or []
    buys = sum(1 for r in rows if (r.get("change") or 0) > 0)
    sells = sum(1 for r in rows if (r.get("change") or 0) < 0)
    net_shares = sum((r.get("change") or 0) for r in rows)
    return {"net_shares": net_shares, "buys": buys, "sells": sells, "n": len(rows)}


def fundamentals_summary(client: FinnhubClient, ticker: str) -> dict[str, Any]:
    """Compact fundamentals bundle for the Market tab + Portfolio Pulse analysis.

    Every sub-call is wrapped so a single failing/premium endpoint degrades to a
    missing key rather than killing the whole summary. Forward analyst estimates
    are intentionally excluded (premium on the free tier).
    """
    ticker = ticker.upper()
    out: dict[str, Any] = {"ticker": ticker}

    def _try(label: str, fn: Any) -> None:
        try:
            out[label] = fn()
        except (FinnhubError, KeyError, TypeError, ValueError) as exc:
            logger.info("Finnhub %s unavailable for %s: %s", label, ticker, exc)

    _try("profile", lambda: _profile(client.company_profile(ticker)))
    _try("metrics", lambda: _select_metrics(client.basic_financials(ticker).get("metric", {})))
    _try("earnings", lambda: _earnings(client.earnings_surprises(ticker, limit=8)))
    _try("recommendation", lambda: _recommendation(client.recommendation_trends(ticker)))
    _try("peers", lambda: [p for p in client.peers(ticker) if p != ticker][:8])
    _try("insider_flow", lambda: _insider_flow(client.insider_transactions(ticker)))
    return out


def _profile(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": p.get("name"),
        "industry": p.get("finnhubIndustry"),
        "market_cap": p.get("marketCapitalization"),
        "exchange": p.get("exchange"),
        "ipo": p.get("ipo"),
    }


def _earnings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Most-recent-first list of {period, actual, estimate, surprise_pct, beat}."""
    out = []
    for r in rows or []:
        actual = r.get("actual")
        estimate = r.get("estimate")
        out.append(
            {
                "period": r.get("period"),
                "actual": actual,
                "estimate": estimate,
                "surprise_pct": r.get("surprisePercent"),
                "beat": (actual is not None and estimate is not None and actual >= estimate),
            }
        )
    return out


def _recommendation(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Latest analyst recommendation distribution (most recent period)."""
    if not rows:
        return None
    latest = rows[0]
    return {
        "period": latest.get("period"),
        "strong_buy": latest.get("strongBuy", 0),
        "buy": latest.get("buy", 0),
        "hold": latest.get("hold", 0),
        "sell": latest.get("sell", 0),
        "strong_sell": latest.get("strongSell", 0),
    }
