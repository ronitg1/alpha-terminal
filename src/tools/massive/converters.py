"""Convert Massive (Polygon) responses into the financialdatasets.ai-shape
Pydantic models the agents already consume.

Two design choices worth calling out:

1. **Precomputed ratios are preferred.** Massive's ``/ratios`` endpoint
   returns a curated set (PE, PB, ROE, debt-to-equity, current ratio, etc).
   We map those 1:1 and *only* compute extra fields (margins) that the
   agents commonly read. Growth rates, turnover ratios, and other less-used
   metrics are intentionally left as ``None``; every agent in this codebase
   already null-checks before using these fields.

2. **Line-item requests are served from raw statements.** Polygon does not
   have an "ask for these fields" endpoint. We fetch the three statements
   (income, balance, cash-flow) and pluck the requested fields per period.
   The mapping from FDS field name to Polygon field name lives in
   ``LINE_ITEM_MAP`` so adding a new field requires a one-line change.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any

from src.data.models import (
    CompanyFacts,
    CompanyNews,
    FinancialMetrics,
    LineItem,
    Price,
)

logger = logging.getLogger(__name__)

# ─── Price aggregates ─────────────────────────────────────────────────────────


def convert_prices(aggs_response: dict[str, Any]) -> list[Price]:
    """Map Polygon ``/v2/aggs`` rows to FDS-shape Price models."""
    rows = aggs_response.get("results") or []
    prices: list[Price] = []
    for row in rows:
        try:
            # Polygon returns timestamps in ms since epoch.
            ts_ms = row["t"]
            iso_date = datetime.datetime.fromtimestamp(ts_ms / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
            prices.append(
                Price(
                    open=float(row["o"]),
                    close=float(row["c"]),
                    high=float(row["h"]),
                    low=float(row["l"]),
                    volume=int(row["v"]),
                    time=iso_date,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Skipping malformed price row: %s (%s)", row, exc)
    return prices


# ─── Financial metrics (mostly precomputed ratios) ───────────────────────────


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def convert_financial_metrics(
    ratios_response: dict[str, Any],
    *,
    ticker: str,
    period: str,
    latest_income: dict[str, Any] | None = None,
) -> list[FinancialMetrics]:
    """Convert a ratios response to ``FinancialMetrics`` rows.

    Margins are filled in from the latest income statement when available,
    since Massive's ratios endpoint omits them. Less-common fields stay
    ``None`` and the agents already handle that.
    """
    rows = ratios_response.get("results") or []
    if not rows:
        return []

    # Margins come from the most recent income statement, applied to every
    # row in the response. This is a deliberate simplification — true
    # point-in-time margins would require pairing each ratios row with its
    # matching income statement, which Polygon doesn't make trivial.
    gross_margin = operating_margin = net_margin = None
    if latest_income:
        revenue = latest_income.get("revenue")
        gross_margin = _safe_div(latest_income.get("gross_profit"), revenue)
        operating_margin = _safe_div(latest_income.get("operating_income"), revenue)
        # Prefer the common-shareholder net income; fall back to consolidated.
        net_income = latest_income.get("net_income_loss_attributable_common_shareholders") \
            or latest_income.get("consolidated_net_income_loss")
        net_margin = _safe_div(net_income, revenue)

    out: list[FinancialMetrics] = []
    for row in rows:
        market_cap = row.get("market_cap")
        free_cash_flow = row.get("free_cash_flow")
        # FDS reports yield, Massive reports the raw FCF dollar amount.
        fcf_yield = _safe_div(free_cash_flow, market_cap)

        # Polygon's ratios rows don't always carry a `date` field; if missing,
        # fall back to today so the model validates.
        report_period = row.get("date") or datetime.date.today().isoformat()

        out.append(
            FinancialMetrics(
                ticker=ticker.upper(),
                report_period=report_period,
                period=period,
                currency="USD",  # Polygon's coverage is US-listed; safe default.
                market_cap=market_cap,
                enterprise_value=row.get("enterprise_value"),
                price_to_earnings_ratio=row.get("price_to_earnings"),
                price_to_book_ratio=row.get("price_to_book"),
                price_to_sales_ratio=row.get("price_to_sales"),
                enterprise_value_to_ebitda_ratio=row.get("ev_to_ebitda"),
                enterprise_value_to_revenue_ratio=row.get("ev_to_sales"),
                free_cash_flow_yield=fcf_yield,
                # peg_ratio requires forward growth which Polygon doesn't publish.
                peg_ratio=None,
                gross_margin=gross_margin,
                operating_margin=operating_margin,
                net_margin=net_margin,
                return_on_equity=row.get("return_on_equity"),
                return_on_assets=row.get("return_on_assets"),
                return_on_invested_capital=None,
                asset_turnover=None,
                inventory_turnover=None,
                receivables_turnover=None,
                days_sales_outstanding=None,
                operating_cycle=None,
                working_capital_turnover=None,
                current_ratio=row.get("current"),
                quick_ratio=row.get("quick"),
                cash_ratio=row.get("cash"),
                operating_cash_flow_ratio=None,
                debt_to_equity=row.get("debt_to_equity"),
                debt_to_assets=None,
                interest_coverage=None,
                # Growth fields require multi-period comparison; left blank in
                # the foundation pass. Agents that need growth call
                # search_line_items() and compute it themselves.
                revenue_growth=None,
                earnings_growth=None,
                book_value_growth=None,
                earnings_per_share_growth=None,
                free_cash_flow_growth=None,
                operating_income_growth=None,
                ebitda_growth=None,
                payout_ratio=None,
                earnings_per_share=row.get("earnings_per_share"),
                book_value_per_share=None,
                free_cash_flow_per_share=None,
            )
        )
    return out


# ─── Line items ──────────────────────────────────────────────────────────────


# Map from the field names agents request → (statement_kind, polygon_field).
# Statement kinds: "income", "balance", "cashflow", "computed".
#
# "computed" entries are evaluated lazily in ``_extract_line_item``; they
# need access to multiple statements at once.
LINE_ITEM_MAP: dict[str, tuple[str, str]] = {
    # ── income statement ──
    "revenue": ("income", "revenue"),
    "cost_of_revenue": ("income", "cost_of_revenue"),
    "gross_profit": ("income", "gross_profit"),
    "operating_income": ("income", "operating_income"),
    "operating_expense": ("income", "selling_general_administrative"),
    "net_income": ("income", "net_income_loss_attributable_common_shareholders"),
    "consolidated_net_income": ("income", "consolidated_net_income_loss"),
    "ebitda": ("income", "ebitda"),
    "earnings_per_share": ("income", "diluted_earnings_per_share"),
    "diluted_earnings_per_share": ("income", "diluted_earnings_per_share"),
    "basic_earnings_per_share": ("income", "basic_earnings_per_share"),
    "outstanding_shares": ("income", "diluted_shares_outstanding"),
    "shares_outstanding": ("income", "diluted_shares_outstanding"),
    "research_and_development": ("income", "research_development"),
    "selling_general_and_administrative_expenses": ("income", "selling_general_administrative"),
    "interest_expense": ("income", "interest_expense"),
    "income_tax_expense": ("income", "income_taxes"),
    # ── balance sheet ──
    "total_assets": ("balance", "total_assets"),
    "current_assets": ("balance", "total_current_assets"),
    "total_current_assets": ("balance", "total_current_assets"),
    "cash_and_equivalents": ("balance", "cash_and_equivalents"),
    "inventory": ("balance", "inventories"),
    "receivables": ("balance", "receivables"),
    "property_plant_and_equipment": ("balance", "property_plant_equipment_net"),
    "goodwill": ("balance", "goodwill"),
    "intangible_assets": ("balance", "intangible_assets_net"),
    "total_liabilities": ("balance", "total_liabilities"),
    "current_liabilities": ("balance", "total_current_liabilities"),
    "total_current_liabilities": ("balance", "total_current_liabilities"),
    "accounts_payable": ("balance", "accounts_payable"),
    "current_debt": ("balance", "debt_current"),
    "long_term_debt": ("balance", "long_term_debt_and_capital_lease_obligations"),
    "total_equity": ("balance", "total_equity"),
    "shareholders_equity": ("balance", "total_equity"),
    "retained_earnings": ("balance", "retained_earnings_deficit"),
    # ── cash flow ──
    "operating_cash_flow": ("cashflow", "net_cash_from_operating_activities"),
    "net_cash_flow_from_operations": ("cashflow", "net_cash_from_operating_activities"),
    "investing_cash_flow": ("cashflow", "net_cash_from_investing_activities"),
    "financing_cash_flow": ("cashflow", "net_cash_from_financing_activities"),
    "change_in_cash": ("cashflow", "change_in_cash_and_equivalents"),
    "depreciation_and_amortization": ("cashflow", "depreciation_depletion_and_amortization"),
    "capital_expenditure": ("cashflow", "purchase_of_property_plant_and_equipment"),
    "dividends_and_other_cash_distributions": ("cashflow", "dividends"),
    # ── computed ──
    "free_cash_flow": ("computed", "free_cash_flow"),
    "working_capital": ("computed", "working_capital"),
}


def _statement_index_by_period(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a ``{period_end: row}`` lookup for fast joining across statements."""
    return {row.get("period_end"): row for row in rows if row.get("period_end")}


def _compute_field(field: str, income: dict[str, Any], balance: dict[str, Any], cashflow: dict[str, Any]) -> float | None:
    if field == "free_cash_flow":
        # CapEx is reported as a negative number on Polygon; adding it to
        # operating cash flow yields free cash flow.
        ocf = cashflow.get("net_cash_from_operating_activities")
        capex = cashflow.get("purchase_of_property_plant_and_equipment")
        if ocf is None or capex is None:
            return None
        return ocf + capex
    if field == "working_capital":
        ca = balance.get("total_current_assets")
        cl = balance.get("total_current_liabilities")
        if ca is None or cl is None:
            return None
        return ca - cl
    return None


def convert_line_items(
    *,
    ticker: str,
    period: str,
    requested_fields: list[str],
    income_response: dict[str, Any],
    balance_response: dict[str, Any],
    cashflow_response: dict[str, Any],
    limit: int = 10,
) -> list[LineItem]:
    """Build LineItem rows by joining the three statements on ``period_end``.

    Returns at most ``limit`` rows, ordered newest first. Unknown field names
    are skipped (and logged once) rather than raising — agents are allowed
    to ask for fields the provider doesn't carry.
    """
    income_rows = income_response.get("results") or []
    balance_by_period = _statement_index_by_period(balance_response.get("results") or [])
    cashflow_by_period = _statement_index_by_period(cashflow_response.get("results") or [])

    unknown_fields = [f for f in requested_fields if f not in LINE_ITEM_MAP]
    if unknown_fields:
        logger.info("Massive adapter: no mapping for fields %s (returning None)", unknown_fields)

    out: list[LineItem] = []
    for income in income_rows[:limit]:
        period_end = income.get("period_end")
        balance = balance_by_period.get(period_end, {})
        cashflow = cashflow_by_period.get(period_end, {})

        extras: dict[str, Any] = {}
        for field in requested_fields:
            mapping = LINE_ITEM_MAP.get(field)
            if mapping is None:
                extras[field] = None
                continue
            kind, source_field = mapping
            if kind == "income":
                extras[field] = income.get(source_field)
            elif kind == "balance":
                extras[field] = balance.get(source_field)
            elif kind == "cashflow":
                extras[field] = cashflow.get(source_field)
            elif kind == "computed":
                extras[field] = _compute_field(source_field, income, balance, cashflow)

        out.append(
            LineItem(
                ticker=ticker.upper(),
                report_period=period_end or "",
                period=period,
                currency="USD",
                **extras,
            )
        )
    return out


# ─── News ────────────────────────────────────────────────────────────────────


def convert_company_news(news_response: dict[str, Any], *, ticker: str) -> list[CompanyNews]:
    """Map Polygon news articles to ``CompanyNews``."""
    rows = news_response.get("results") or []
    out: list[CompanyNews] = []
    for row in rows:
        publisher = row.get("publisher") or {}
        try:
            out.append(
                CompanyNews(
                    ticker=ticker.upper(),
                    title=row.get("title") or "",
                    author=row.get("author"),
                    source=publisher.get("name") or "unknown",
                    # Polygon timestamps are ISO 8601 with timezone; the FDS
                    # schema expects a string and the agents only use date
                    # ordering, so a verbatim copy is fine.
                    date=row.get("published_utc") or "",
                    url=row.get("article_url") or "",
                    sentiment=None,
                )
            )
        except Exception as exc:
            logger.warning("Skipping malformed news row for %s: %s", ticker, exc)
    return out


# ─── Company facts ───────────────────────────────────────────────────────────


def convert_company_facts(ticker_details: dict[str, Any]) -> CompanyFacts | None:
    """Map ``/v3/reference/tickers/{ticker}`` to ``CompanyFacts``."""
    result = ticker_details.get("results")
    if not result:
        return None

    return CompanyFacts(
        ticker=result.get("ticker") or "",
        name=result.get("name") or "",
        cik=result.get("cik"),
        # Polygon doesn't return GICS industry/sector directly; use SIC fields.
        industry=result.get("sic_description"),
        sector=None,
        category=result.get("type"),
        exchange=result.get("primary_exchange"),
        is_active=result.get("active"),
        listing_date=result.get("list_date"),
        location=result.get("locale"),
        market_cap=result.get("market_cap"),
        number_of_employees=result.get("total_employees"),
        sec_filings_url=None,
        sic_code=result.get("sic_code"),
        sic_industry=result.get("sic_description"),
        sic_sector=None,
        website_url=result.get("homepage_url"),
        weighted_average_shares=result.get("weighted_shares_outstanding"),
    )
