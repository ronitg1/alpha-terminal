"""Offline smoke tests for src/tools/massive/converters.py.

These exercise the Polygon → FDS-shape mapping with hand-crafted JSON so
the conversion logic is verified without hitting the live Massive API.
Run with: ``poetry run pytest tests/test_massive_converters.py -v``
"""
from __future__ import annotations

from src.tools.massive.converters import (
    convert_company_facts,
    convert_company_news,
    convert_financial_metrics,
    convert_line_items,
    convert_prices,
)


def test_convert_prices_basic() -> None:
    aggs = {
        "results": [
            {"t": 1_730_000_000_000, "o": 100.0, "h": 105.0, "l": 99.0, "c": 104.0, "v": 1_000_000},
            {"t": 1_730_086_400_000, "o": 104.0, "h": 110.0, "l": 103.5, "c": 109.0, "v": 1_200_000},
        ]
    }
    prices = convert_prices(aggs)
    assert len(prices) == 2
    assert prices[0].open == 100.0
    assert prices[0].close == 104.0
    assert prices[0].volume == 1_000_000
    # Time should be an ISO date.
    assert len(prices[0].time) == 10 and prices[0].time[4] == "-"


def test_convert_prices_skips_malformed() -> None:
    aggs = {"results": [{"t": 1_730_000_000_000, "o": "broken"}, {"t": 1_730_086_400_000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}]}
    prices = convert_prices(aggs)
    assert len(prices) == 1


def test_convert_financial_metrics_maps_ratios() -> None:
    ratios = {
        "results": [
            {
                "ticker": "AAPL",
                "date": "2025-06-30",
                "price_to_earnings": 34.84,
                "price_to_book": 52.16,
                "price_to_sales": 9.0,
                "return_on_equity": 1.5284,
                "return_on_assets": 0.30,
                "debt_to_equity": 1.52,
                "enterprise_value": 3_500_000_000_000,
                "ev_to_ebitda": 24.0,
                "ev_to_sales": 9.5,
                "current": 0.68,
                "quick": 0.60,
                "cash": 0.20,
                "earnings_per_share": 6.5,
                "market_cap": 3_400_000_000_000,
                "free_cash_flow": 104_339_000_000,
            }
        ]
    }
    latest_income = {
        "revenue": 94_036_000_000,
        "gross_profit": 43_718_000_000,
        "operating_income": 28_202_000_000,
        "net_income_loss_attributable_common_shareholders": 23_500_000_000,
    }
    metrics = convert_financial_metrics(ratios, ticker="AAPL", period="ttm", latest_income=latest_income)
    assert len(metrics) == 1
    m = metrics[0]
    assert m.ticker == "AAPL"
    assert m.price_to_earnings_ratio == 34.84
    assert m.return_on_equity == 1.5284
    assert m.current_ratio == 0.68
    # margins computed from income statement
    assert m.gross_margin is not None and round(m.gross_margin, 3) == 0.465
    assert m.operating_margin is not None and round(m.operating_margin, 3) == 0.300
    # FCF yield = FCF / market_cap
    assert m.free_cash_flow_yield is not None and round(m.free_cash_flow_yield, 4) == round(104_339_000_000 / 3_400_000_000_000, 4)
    # Fields we intentionally leave as None
    assert m.peg_ratio is None
    assert m.revenue_growth is None


def test_convert_line_items_joins_statements() -> None:
    income_response = {
        "results": [
            {
                "period_end": "2025-06-30",
                "revenue": 94_036_000_000,
                "operating_income": 28_202_000_000,
                "consolidated_net_income_loss": 23_500_000_000,
                "diluted_shares_outstanding": 15_300_000_000,
            },
            {
                "period_end": "2025-03-31",
                "revenue": 90_753_000_000,
                "operating_income": 27_900_000_000,
                "consolidated_net_income_loss": 24_780_000_000,
                "diluted_shares_outstanding": 15_400_000_000,
            },
        ]
    }
    balance_response = {
        "results": [
            {
                "period_end": "2025-06-30",
                "total_current_assets": 152_987_000_000,
                "total_current_liabilities": 178_080_000_000,
                "total_equity": 56_950_000_000,
                "long_term_debt_and_capital_lease_obligations": 85_750_000_000,
            },
            {
                "period_end": "2025-03-31",
                "total_current_assets": 145_000_000_000,
                "total_current_liabilities": 170_000_000_000,
                "total_equity": 60_000_000_000,
                "long_term_debt_and_capital_lease_obligations": 87_000_000_000,
            },
        ]
    }
    cashflow_response = {
        "results": [
            {
                "period_end": "2025-06-30",
                "net_cash_from_operating_activities": 27_867_000_000,
                "purchase_of_property_plant_and_equipment": -2_500_000_000,
            },
            {
                "period_end": "2025-03-31",
                "net_cash_from_operating_activities": 26_000_000_000,
                "purchase_of_property_plant_and_equipment": -2_300_000_000,
            },
        ]
    }

    rows = convert_line_items(
        ticker="AAPL",
        period="ttm",
        requested_fields=[
            "revenue",
            "operating_income",
            "net_income",
            "long_term_debt",
            "shareholders_equity",
            "working_capital",
            "free_cash_flow",
            "outstanding_shares",
            "totally_made_up_field",  # should appear as None
        ],
        income_response=income_response,
        balance_response=balance_response,
        cashflow_response=cashflow_response,
        limit=10,
    )
    assert len(rows) == 2
    latest = rows[0]
    assert latest.revenue == 94_036_000_000
    # net_income maps to net_income_loss_attributable_common_shareholders if present,
    # else falls back to consolidated. Our sample only has the consolidated key.
    assert latest.net_income is None  # we requested net_income; income only has consolidated
    assert latest.long_term_debt == 85_750_000_000
    assert latest.shareholders_equity == 56_950_000_000
    # working_capital is computed: current_assets - current_liabilities
    assert latest.working_capital == 152_987_000_000 - 178_080_000_000
    # free_cash_flow is computed: ocf + capex (capex negative)
    assert latest.free_cash_flow == 27_867_000_000 + (-2_500_000_000)
    assert latest.outstanding_shares == 15_300_000_000
    # Unknown field comes back as None.
    assert getattr(latest, "totally_made_up_field") is None


def test_convert_company_news() -> None:
    response = {
        "results": [
            {
                "id": "abc123",
                "publisher": {"name": "Reuters"},
                "title": "AAPL beats on revenue",
                "author": "J. Smith",
                "published_utc": "2025-10-25T14:30:00Z",
                "article_url": "https://example.com/news/abc123",
            }
        ]
    }
    news = convert_company_news(response, ticker="AAPL")
    assert len(news) == 1
    assert news[0].source == "Reuters"
    assert news[0].title == "AAPL beats on revenue"
    assert news[0].url.startswith("https://")


def test_convert_company_facts() -> None:
    response = {
        "results": {
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "cik": "0000320193",
            "primary_exchange": "XNAS",
            "active": True,
            "list_date": "1980-12-12",
            "locale": "us",
            "market_cap": 3_400_000_000_000,
            "total_employees": 164_000,
            "sic_code": "3571",
            "sic_description": "Electronic Computers",
            "homepage_url": "https://apple.com",
            "weighted_shares_outstanding": 15_300_000_000,
            "type": "CS",
        }
    }
    facts = convert_company_facts(response)
    assert facts is not None
    assert facts.ticker == "AAPL"
    assert facts.name == "Apple Inc."
    assert facts.market_cap == 3_400_000_000_000
    assert facts.industry == "Electronic Computers"
    assert facts.number_of_employees == 164_000


def test_convert_company_facts_empty_returns_none() -> None:
    assert convert_company_facts({"results": None}) is None
    assert convert_company_facts({}) is None
