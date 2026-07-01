"""Classify a holding into an allocation bucket: Cash, Market Index, or a broad
sector (Technology, Health Care, …).

Why this exists: the Portfolio allocation card should group holdings the way an
investor thinks about them — cash-equivalents together, broad-market ETFs
together, and individual stocks by sector — instead of dumping everything into a
meaningless "Other" bucket. Options are collapsed onto their underlying by the
caller, so only the underlying ticker is classified here.

Data source: Finnhub's ``company_profile`` ``finnhubIndustry`` field, which is
*granular* (e.g. "Semiconductors", "Internet Media"), not a clean GICS sector. We
map it to a broad sector with keyword matching (:data:`_SECTOR_KEYWORDS`). Lookups
are cached in-process for the lifetime of the worker (a ticker's sector doesn't
change intraday) and every failure degrades to a safe bucket rather than raising —
this must never break the portfolio view.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

CASH = "Cash"
MARKET_INDEX = "Market Index"
FUNDS = "Funds & ETFs"
OTHER = "Other"

# Money-market / sweep funds that are effectively cash but show up as positions.
_CASH_TICKERS = frozenset({
    "SPAXX", "FDRXX", "SPRXX", "FZFXX", "FDLXX", "FZCXX", "FGXXX",
    "VMFXX", "VMRXX", "VUSXX", "SWVXX", "SNVXX", "SNSXX", "TTTXX", "CASH", "USD",
})

# Broad-market / index ETFs — grouped as "Market Index" rather than a sector.
_INDEX_ETFS = frozenset({
    "SPY", "VOO", "IVV", "SPLG", "VTI", "ITOT", "SCHB",
    "QQQ", "QQQM", "DIA", "IWM", "IWB", "IJR", "IJH", "VO", "VB", "MDY",
    "VT", "VXUS", "VEA", "VWO", "IXUS", "ACWI", "EFA", "IEFA", "IEMG",
    "VIG", "VYM", "SCHD", "NOBL", "DGRO",
    "SPXL", "UPRO", "SSO", "TQQQ", "QLD", "SPUU",  # leveraged broad-index
    "RSP", "VUG", "VTV", "SCHG", "SCHV", "MGK",
})

# finnhubIndustry keyword -> broad sector. First match wins; order matters
# (more specific keywords first). Case-insensitive substring match.
_SECTOR_KEYWORDS: list[tuple[str, str]] = [
    ("semiconductor", "Technology"),
    ("software", "Technology"),
    ("technology", "Technology"),
    ("hardware", "Technology"),
    ("electronic", "Technology"),
    ("it services", "Technology"),
    ("internet", "Communication Services"),
    ("media", "Communication Services"),
    ("telecommunication", "Communication Services"),
    ("communication", "Communication Services"),
    ("entertainment", "Communication Services"),
    ("pharmaceutical", "Health Care"),
    ("biotechnology", "Health Care"),
    ("health", "Health Care"),
    ("medical", "Health Care"),
    ("life sciences", "Health Care"),
    ("bank", "Financials"),
    ("insurance", "Financials"),
    ("financial", "Financials"),
    ("capital markets", "Financials"),
    ("oil", "Energy"),
    ("gas", "Energy"),
    ("energy", "Energy"),
    ("utilit", "Utilities"),
    ("aerospace", "Industrials"),
    ("defense", "Industrials"),
    ("machinery", "Industrials"),
    ("industrial", "Industrials"),
    ("transportation", "Industrials"),
    ("airlines", "Industrials"),
    ("construction", "Industrials"),
    ("real estate", "Real Estate"),
    ("reit", "Real Estate"),
    ("chemical", "Materials"),
    ("metals", "Materials"),
    ("mining", "Materials"),
    ("materials", "Materials"),
    ("retail", "Consumer Discretionary"),
    ("automobile", "Consumer Discretionary"),
    ("auto ", "Consumer Discretionary"),
    ("apparel", "Consumer Discretionary"),
    ("hotel", "Consumer Discretionary"),
    ("leisure", "Consumer Discretionary"),
    ("beverage", "Consumer Staples"),
    ("food", "Consumer Staples"),
    ("tobacco", "Consumer Staples"),
    ("household", "Consumer Staples"),
    ("consumer staples", "Consumer Staples"),
    ("consumer", "Consumer Discretionary"),
]

# ticker -> resolved bucket, cached for the worker's lifetime.
_bucket_cache: dict[str, str] = {}


def _sector_from_industry(industry: str | None) -> str:
    if not industry:
        return FUNDS  # no industry usually means an ETF/fund
    low = industry.lower()
    for keyword, sector in _SECTOR_KEYWORDS:
        if keyword in low:
            return sector
    return industry  # keep the raw industry rather than dumping into "Other"


def _looks_like_cash(symbol: str, name: str | None) -> bool:
    if symbol in _CASH_TICKERS:
        return True
    text = (name or "").lower()
    return "money market" in text or "cash reserves" in text


def classify(symbol: str, *, name: str | None = None, industry: str | None = None) -> str:
    """The allocation bucket for one underlying ticker. ``industry`` (Finnhub
    finnhubIndustry) is optional — when absent the caller has already decided not
    to spend a lookup (e.g. cash/index short-circuit)."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return OTHER
    if _looks_like_cash(sym, name):
        return CASH
    if sym in _INDEX_ETFS:
        return MARKET_INDEX
    return _sector_from_industry(industry)


def industry_for(symbol: str) -> str | None:
    """Finnhub industry for ``symbol`` (cached, best-effort). Returns None on any
    failure so classification falls back gracefully."""
    from src.tools.finnhub.client import FinnhubClient, FinnhubError, is_finnhub_configured

    if not is_finnhub_configured():
        return None
    try:
        profile: dict[str, Any] = FinnhubClient().company_profile(symbol)
    except FinnhubError as exc:
        logger.debug("Finnhub profile lookup failed for %s: %s", symbol, exc)
        return None
    except Exception as exc:  # noqa: BLE001 — never break the portfolio view
        logger.warning("Unexpected sector lookup error for %s: %s", symbol, type(exc).__name__)
        return None
    return (profile or {}).get("finnhubIndustry") or None


def bucket_for(symbol: str, *, name: str | None = None) -> str:
    """Resolve and cache the allocation bucket for ``symbol``. Only hits Finnhub
    for real stocks (cash and index ETFs short-circuit without a lookup)."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return OTHER
    if sym in _bucket_cache:
        return _bucket_cache[sym]
    if _looks_like_cash(sym, name):
        result = CASH
    elif sym in _INDEX_ETFS:
        result = MARKET_INDEX
    else:
        result = classify(sym, name=name, industry=industry_for(sym))
    _bucket_cache[sym] = result
    return result
