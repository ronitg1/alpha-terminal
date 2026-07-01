"""Allocation bucket classification (Cash / Market Index / broad sector)."""
from __future__ import annotations

from app.backend.services import portfolio_classify as pc


def test_cash_tickers_and_name_heuristic():
    assert pc.classify("SPAXX") == pc.CASH
    assert pc.classify("FDRXX") == pc.CASH
    assert pc.classify("XYZ", name="Fidelity Government Money Market") == pc.CASH


def test_index_etfs():
    assert pc.classify("VOO") == pc.MARKET_INDEX
    assert pc.classify("VIG") == pc.MARKET_INDEX
    assert pc.classify("SPXL") == pc.MARKET_INDEX


def test_industry_maps_to_broad_sector():
    assert pc.classify("NVDA", industry="Semiconductors") == "Technology"
    assert pc.classify("GOOG", industry="Internet Media & Services") == "Communication Services"
    assert pc.classify("XOM", industry="Oil & Gas") == "Energy"
    assert pc.classify("JPM", industry="Banking") == "Financials"


def test_unknown_industry_kept_missing_becomes_funds():
    # An unmapped-but-present industry keeps its own label (not dumped into Other).
    assert pc.classify("ABC", industry="Widget Fabrication") == "Widget Fabrication"
    # No industry (typical of an ETF/fund) -> Funds & ETFs.
    assert pc.classify("SOMEETF", industry=None) == pc.FUNDS


def test_bucket_for_short_circuits_without_finnhub(monkeypatch):
    # Cash + index resolve without ever calling Finnhub.
    monkeypatch.setattr(pc, "industry_for", lambda s: (_ for _ in ()).throw(AssertionError("looked up")))
    pc._bucket_cache.clear()
    assert pc.bucket_for("SPAXX") == pc.CASH
    assert pc.bucket_for("VOO") == pc.MARKET_INDEX


def test_bucket_for_caches(monkeypatch):
    calls: list[str] = []

    def _fake_industry(sym: str) -> str:
        calls.append(sym)
        return "Semiconductors"

    monkeypatch.setattr(pc, "industry_for", _fake_industry)
    pc._bucket_cache.clear()
    assert pc.bucket_for("NVDA") == "Technology"
    assert pc.bucket_for("NVDA") == "Technology"
    assert calls == ["NVDA"]  # second call served from cache
