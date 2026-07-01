"""Allocation bucket classification (Cash / Market Index / detailed sector)."""
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


def test_curated_map_gives_detailed_sectors():
    # Curated tickers resolve instantly to a detailed sector — the industry arg is
    # not even consulted.
    assert pc.classify("NVDA", industry="ignored") == "Semiconductors"
    assert pc.classify("GOOG") == "Internet & Media"
    assert pc.classify("MSFT") == "Software & Cloud"
    assert pc.classify("XOM") == "Energy"
    assert pc.classify("JPM") == "Financials"
    # SpaceX (recently IPO'd, may lack price data) still classifies as a stock sector.
    assert pc.classify("SPCX") == "Aerospace & Defense"


def test_unknown_uses_finnhub_industry_then_funds():
    # Not cash/index/curated: keep the raw (detailed) Finnhub industry...
    assert pc.classify("ZZZZ", industry="Widget Fabrication") == "Widget Fabrication"
    # ...or Funds & ETFs when there's no industry (typical of an ETF/fund).
    assert pc.classify("SOMEETF", industry=None) == pc.FUNDS


def test_instant_bucket_no_network():
    assert pc.instant_bucket("SPAXX") == pc.CASH
    assert pc.instant_bucket("VOO") == pc.MARKET_INDEX
    assert pc.instant_bucket("NVDA") == "Semiconductors"
    assert pc.instant_bucket("ZZZZ") is None  # needs a Finnhub lookup


def test_bucket_for_short_circuits_without_finnhub(monkeypatch):
    monkeypatch.setattr(pc, "industry_for", lambda s: (_ for _ in ()).throw(AssertionError("looked up")))
    pc._bucket_cache.clear()
    assert pc.bucket_for("SPAXX") == pc.CASH
    assert pc.bucket_for("VOO") == pc.MARKET_INDEX
    assert pc.bucket_for("NVDA") == "Semiconductors"  # curated -> no lookup


def test_bucket_for_hits_finnhub_only_for_tail_and_caches(monkeypatch):
    calls: list[str] = []

    def _fake_industry(sym: str) -> str:
        calls.append(sym)
        return "Specialty Chemicals"

    monkeypatch.setattr(pc, "industry_for", _fake_industry)
    pc._bucket_cache.clear()
    assert pc.bucket_for("ZZZZ") == "Specialty Chemicals"
    assert pc.bucket_for("ZZZZ") == "Specialty Chemicals"
    assert calls == ["ZZZZ"]  # second call served from cache
