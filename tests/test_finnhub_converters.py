"""Tests for Finnhub payload conversion (no network — hand-crafted fixtures)."""

from __future__ import annotations

from src.tools.finnhub.converters import (
    _earnings,
    _insider_flow,
    _recommendation,
    _select_metrics,
    finnhub_insider_trades,
)


def test_insider_trades_mapping() -> None:
    payload = {
        "data": [
            {
                "name": "DOE JANE",
                "change": -1000,
                "share": 4000,
                "transactionPrice": 50.0,
                "transactionDate": "2026-05-01",
                "filingDate": "2026-05-03",
            },
            {  # no filing OR transaction date → skipped (model requires a date)
                "name": "NO DATES",
                "change": 100,
            },
        ]
    }
    trades = finnhub_insider_trades("aapl", payload)
    assert len(trades) == 1
    t = trades[0]
    assert t.ticker == "AAPL"
    assert t.name == "DOE JANE"
    assert t.transaction_shares == -1000
    assert t.transaction_price_per_share == 50.0
    assert t.transaction_value == -50000.0           # change * price
    assert t.shares_owned_after_transaction == 4000
    assert t.shares_owned_before_transaction == 5000  # share - change
    assert t.filing_date == "2026-05-03"


def test_select_metrics_filters_to_numbers() -> None:
    metric = {
        "revenueGrowthTTMYoy": 12.76,
        "inventoryTurnoverTTM": 36.17,
        "epsGrowthTTMYoy": None,       # null → dropped
        "irrelevantKey": "ignored",    # not in the allowlist
    }
    out = _select_metrics(metric)
    assert out["revenue_growth_ttm"] == 12.76
    assert out["inventory_turnover_ttm"] == 36.17
    assert "eps_growth_ttm" not in out
    assert "irrelevantKey" not in out


def test_earnings_beat_miss_flags() -> None:
    rows = [
        {"period": "2026-03-31", "actual": 2.01, "estimate": 1.99, "surprisePercent": 1.0},
        {"period": "2025-12-31", "actual": 1.50, "estimate": 1.70, "surprisePercent": -11.8},
    ]
    out = _earnings(rows)
    assert out[0]["beat"] is True
    assert out[1]["beat"] is False


def test_recommendation_latest() -> None:
    rows = [
        {"period": "2026-06-01", "strongBuy": 14, "buy": 24, "hold": 15, "sell": 2, "strongSell": 0},
        {"period": "2026-05-01", "strongBuy": 13, "buy": 22, "hold": 16, "sell": 3, "strongSell": 0},
    ]
    rec = _recommendation(rows)
    assert rec is not None
    assert rec["period"] == "2026-06-01"
    assert rec["strong_buy"] == 14
    assert _recommendation([]) is None


def test_insider_flow_summary() -> None:
    payload = {"data": [{"change": 100}, {"change": -50}, {"change": -200}]}
    flow = _insider_flow(payload)
    assert flow["buys"] == 1
    assert flow["sells"] == 2
    assert flow["net_shares"] == -150
    assert flow["n"] == 3
