"""Tests for the unified cross-brokerage portfolio overview.

Everything external (SnapTrade fetch, Robinhood fetch, quote enrichment) is
monkeypatched, so these pin the merge + metric math, not the network.
"""
from __future__ import annotations

import anyio
import pytest

from app.backend.services import portfolio_classify
from app.backend.services import portfolio_overview as ov


@pytest.fixture(autouse=True)
def _clear_caches():
    """Module-level caches (option snapshots, sector buckets) must not leak between
    tests — a cached None from one test would suppress another's override."""
    ov._option_change_cache.clear()
    portfolio_classify._bucket_cache.clear()
    yield


def _snaptrade_payload():
    return {
        "status": "ok",
        "accounts": [
            {
                "id": "acc1",
                "label": "Individual (X1)",
                "institution": "Fidelity",
                "total_balance": 3425.0,  # positions (2050 + 375) + 1000 cash
                "positions": [
                    {
                        "kind": "stock", "symbol": "NVDA", "underlying": "NVDA",
                        "units": 10, "price": 200.0, "avg_cost": 100.0,
                        "cost_basis": 1000.0, "market_value": 2000.0, "open_pnl": 1000.0,
                    },
                ],
                "options": [
                    {
                        "kind": "option", "symbol": "NVDA 260724C", "underlying": "NVDA",
                        "option_type": "CALL", "strike": 210.0, "expiration": "2026-07-24",
                        "units": 1, "price": 3.75, "avg_cost": 5.26,
                        "cost_basis": 526.0, "market_value": 375.0, "open_pnl": -151.0,
                    },
                ],
            }
        ],
    }


@pytest.fixture()
def only_snaptrade(monkeypatch):
    monkeypatch.setattr(ov, "snaptrade_configured", lambda: True)
    monkeypatch.setattr(ov.snaptrade_connection_service, "get_status", lambda: {"connected": True})
    monkeypatch.setattr(ov.snaptrade_service, "fetch_portfolio", _snaptrade_payload)
    monkeypatch.setattr(ov, "resolve_key", lambda provider: None)  # no robinhood

    async def _quotes(symbols):
        return {"NVDA": {"last": 205.0, "prev_close": 200.0, "pct_change": 2.5, "name": "NVIDIA"}}

    monkeypatch.setattr(ov, "_fetch_quotes", _quotes)
    # Keep tests hermetic: no Finnhub sector calls, no Polygon option-bar calls.
    monkeypatch.setattr(ov, "bucket_for", lambda sym, name=None: "Technology")
    monkeypatch.setattr(ov, "_fetch_option_snapshot", lambda underlying, occ: None)
    yield


def test_not_connected_returns_empty(monkeypatch):
    monkeypatch.setattr(ov, "snaptrade_configured", lambda: False)
    monkeypatch.setattr(ov, "resolve_key", lambda provider: None)
    result = anyio.run(ov.build_overview)
    assert result == {"connected": False, "sources": [], "accounts": [], "combined": None}


def test_single_snaptrade_account_metrics(only_snaptrade):
    result = anyio.run(ov.build_overview)
    assert result["connected"] is True
    assert result["sources"] == ["snaptrade"]
    assert result["combined"] is None  # single account => no combined view
    acct = result["accounts"][0]
    assert acct["institution"] == "Fidelity"

    by_symbol = {(p["symbol"], p["kind"]): p for p in acct["positions"]}
    nvda = by_symbol[("NVDA", "stock")]
    # stock value uses the fresh quote (10 * 205), day change (205-200)*10
    assert nvda["last_price"] == 205.0
    assert nvda["current_value"] == 2050.0
    assert nvda["day_change"] == 50.0
    assert nvda["day_change_pct"] == 2.5
    # total gain = value (2050) − cost basis (10 * 100) = 1050, computed (not open_pnl)
    assert nvda["total_gain"] == 1050.0
    assert nvda["total_gain_pct"] == 105.0
    assert nvda["name"] == "NVIDIA"

    opt = by_symbol[("NVDA 260724C", "option")]
    # option keeps its own premium — the underlying quote must NOT override it
    assert opt["last_price"] == 3.75
    assert opt["current_value"] == 375.0
    assert opt["day_change"] is None
    assert opt["option_type"] == "CALL"
    # option total gain = value (375) − cost basis (526), computed from avg cost
    assert opt["total_gain"] == -151.0

    # account totals: broker total 3425; cash = 3425 − invested (2425) = 1000
    assert acct["total_value"] == 3425.0
    assert acct["cash"] == 1000.0
    # pct_of_account sums to <100 because of cash
    assert nvda["pct_of_account"] == pytest.approx(2050 / 3425 * 100, abs=0.01)


def test_option_snapshot_price_overrides_stale_broker_mark(monkeypatch):
    monkeypatch.setattr(ov, "snaptrade_configured", lambda: True)
    monkeypatch.setattr(ov.snaptrade_connection_service, "get_status", lambda: {"connected": True})
    monkeypatch.setattr(ov.snaptrade_service, "fetch_portfolio", _snaptrade_payload)
    monkeypatch.setattr(ov, "resolve_key", lambda provider: None)

    async def _quotes(symbols):
        return {"NVDA": {"last": 205.0, "prev_close": 200.0, "pct_change": 2.5, "name": "NVIDIA"}}

    monkeypatch.setattr(ov, "_fetch_quotes", _quotes)
    monkeypatch.setattr(ov, "bucket_for", lambda sym, name=None: "Technology")
    # Live option price 6.00 (vs the broker's stale 3.75 mark), +0.50 / +8% today.
    monkeypatch.setattr(ov, "_fetch_option_snapshot", lambda underlying, occ: (6.0, 0.5, 8.0))

    result = anyio.run(ov.build_overview)
    opt = next(p for p in result["accounts"][0]["positions"] if p["kind"] == "option")
    assert opt["last_price"] == 6.0
    assert opt["current_value"] == 600.0  # 6.00 * 1 * 100 (not the broker's 375)
    assert opt["total_gain"] == 74.0      # 600 - 526 cost basis
    assert opt["day_change"] == 50.0      # 0.50 * 1 * 100
    assert opt["day_change_pct"] == 8.0


def test_combined_merges_symbols_across_accounts(monkeypatch):
    def _two_accounts():
        payload = _snaptrade_payload()
        second = {
            "id": "acc2", "label": "Roth (X2)", "institution": "Fidelity", "total_balance": 1025.0,
            "positions": [
                {
                    "kind": "stock", "symbol": "NVDA", "underlying": "NVDA",
                    "units": 5, "price": 200.0, "avg_cost": 150.0,
                    "cost_basis": 750.0, "market_value": 1000.0, "open_pnl": 250.0,
                },
            ],
            "options": [],
        }
        payload["accounts"].append(second)
        return payload

    monkeypatch.setattr(ov, "snaptrade_configured", lambda: True)
    monkeypatch.setattr(ov.snaptrade_connection_service, "get_status", lambda: {"connected": True})
    monkeypatch.setattr(ov.snaptrade_service, "fetch_portfolio", _two_accounts)
    monkeypatch.setattr(ov, "resolve_key", lambda provider: None)

    async def _quotes(symbols):
        return {"NVDA": {"last": 205.0, "prev_close": 200.0, "pct_change": 2.5, "name": "NVIDIA"}}

    monkeypatch.setattr(ov, "_fetch_quotes", _quotes)
    monkeypatch.setattr(ov, "bucket_for", lambda sym, name=None: "Technology")
    monkeypatch.setattr(ov, "_fetch_option_snapshot", lambda underlying, occ: None)

    result = anyio.run(ov.build_overview)
    assert len(result["accounts"]) == 2
    combined = result["combined"]
    assert combined is not None
    nvda = next(p for p in combined["positions"] if p["symbol"] == "NVDA" and p["kind"] == "stock")
    # 10 + 5 units, values 2050 + 1025 = 3075
    assert nvda["quantity"] == 15.0
    assert nvda["current_value"] == 3075.0


def test_robinhood_positions_parsed(monkeypatch):
    monkeypatch.setattr(ov, "snaptrade_configured", lambda: False)
    monkeypatch.setattr(ov, "resolve_key", lambda provider: "rh-token")

    async def _rh_fetch():
        return {"tools": [{"tool": "get_portfolio", "data": {"positions": [
            {"symbol": "AAPL", "quantity": "3", "price": "100", "average_buy_price": "80"},
        ]}}]}

    import app.backend.services.robinhood_mcp as rh
    monkeypatch.setattr(rh, "fetch_portfolio", _rh_fetch)

    async def _quotes(symbols):
        return {"AAPL": {"last": 110.0, "prev_close": 100.0, "pct_change": 10.0, "name": "Apple"}}

    monkeypatch.setattr(ov, "_fetch_quotes", _quotes)
    monkeypatch.setattr(ov, "bucket_for", lambda sym, name=None: "Technology")
    monkeypatch.setattr(ov, "_fetch_option_snapshot", lambda underlying, occ: None)

    result = anyio.run(ov.build_overview)
    assert result["connected"] is True
    assert result["sources"] == ["robinhood"]
    acct = result["accounts"][0]
    aapl = acct["positions"][0]
    assert aapl["symbol"] == "AAPL"
    assert aapl["current_value"] == 330.0  # 3 * 110
    assert aapl["cost_basis_total"] == 240.0  # 3 * 80
    assert aapl["total_gain"] == 90.0  # 330 - 240
