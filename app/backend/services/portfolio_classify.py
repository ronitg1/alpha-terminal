"""Classify a holding into an allocation bucket: Cash, Market Index, or a detailed
sector (Semiconductors, Software, Internet & Media, …).

Why this exists: the Portfolio allocation card should group holdings the way an
investor thinks about them — cash-equivalents together, broad-market ETFs
together, and individual stocks by (detailed) sector — instead of one meaningless
"Other" bucket. Options are collapsed onto their underlying by the caller, so only
the underlying ticker is classified here.

Resolution order (fastest first, so the common case never touches the network):
1. :data:`_CASH_TICKERS` / money-market name  -> ``Cash``
2. :data:`_INDEX_ETFS`                         -> ``Market Index``
3. :data:`_TICKER_SECTOR` curated map          -> a detailed sector, instantly
4. Finnhub ``finnhubIndustry`` (raw, detailed) -> that industry
5. nothing resolved                            -> ``Funds & ETFs`` / ``Other``

Steps 1-3 are instant (no I/O); :func:`instant_bucket` exposes them so the caller
can classify most of a portfolio synchronously and reserve the slow Finnhub lookup
(step 4) for the unknown tail — this is what stopped the whole allocation from
falling back to "Other" when the per-ticker Finnhub calls timed out.
"""
from __future__ import annotations

import logging

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
    "VT", "VXUS", "VEA", "VWO", "IXUS", "ACWI", "EFA", "IEFA", "IEMG", "AVUV",
    "VIG", "VYM", "SCHD", "NOBL", "DGRO",
    "SPXL", "UPRO", "SSO", "TQQQ", "QLD", "SPUU",  # leveraged broad-index
    "RSP", "VUG", "VTV", "SCHG", "SCHV", "MGK",
})

# Curated ticker -> DETAILED sector. Instant (no network) and consistent, so the
# common large caps always land in a sensible bucket. Extend freely.
_TICKER_SECTOR: dict[str, str] = {
    # Semiconductors
    **{t: "Semiconductors" for t in (
        "NVDA", "AMD", "TSM", "ASML", "AVGO", "QCOM", "MU", "INTC", "TXN", "AMAT",
        "LRCX", "ARM", "SMCI", "MRVL", "ADI", "NXPI", "ON", "MCHP", "KLAC", "TER", "ENTG",
    )},
    # Software & Cloud
    **{t: "Software & Cloud" for t in (
        "MSFT", "ORCL", "CRM", "ADBE", "NOW", "SNOW", "PLTR", "INTU", "SAP", "PANW",
        "CRWD", "DDOG", "ZS", "NET", "TEAM", "WDAY", "ADSK", "MDB", "HUBS", "NBIS",
    )},
    # Internet & Media
    **{t: "Internet & Media" for t in (
        "GOOG", "GOOGL", "META", "NFLX", "SPOT", "PINS", "SNAP", "RDDT", "TTWO",
        "EA", "RBLX", "DIS", "UBER", "ABNB", "DASH", "BKNG",
    )},
    # Consumer Electronics & Hardware
    **{t: "Consumer Electronics & Hardware" for t in (
        "AAPL", "DELL", "HPQ", "ANET", "CSCO", "JNPR", "STX", "WDC",
    )},
    # E-Commerce & Retail
    **{t: "E-Commerce & Retail" for t in (
        "AMZN", "BABA", "MELI", "ETSY", "EBAY", "CHWY", "W", "WMT", "COST", "TGT",
        "HD", "LOW", "NKE", "SBUX", "MCD", "LULU",
    )},
    # Autos & EV
    **{t: "Autos & EV" for t in ("TSLA", "RIVN", "LCID", "F", "GM")},
    # Financials
    **{t: "Financials" for t in (
        "JPM", "BAC", "WFC", "GS", "MS", "C", "V", "MA", "AXP", "PYPL", "SQ", "COIN",
        "HOOD", "SCHW", "BRK.B", "BRK.A", "BLK", "SOFI",
    )},
    # Health Care
    **{t: "Health Care" for t in (
        "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "DHR", "AMGN",
        "GILD", "MRNA", "BMY", "CVS", "ISRG", "VRTX",
    )},
    # Energy
    **{t: "Energy" for t in ("XOM", "CVX", "COP", "SLB", "OXY", "EOG", "MPC", "PSX", "VLO")},
    # Industrials
    **{t: "Industrials" for t in (
        "BA", "CAT", "GE", "HON", "UPS", "RTX", "LMT", "DE", "MMM", "UNP", "SON", "EMR",
    )},
    # Telecom
    **{t: "Telecom" for t in ("T", "VZ", "TMUS", "CMCSA")},
    # Consumer Staples
    **{t: "Consumer Staples" for t in ("PG", "KO", "PEP", "PM", "MO", "CL", "MDLZ")},
    # Utilities
    **{t: "Utilities" for t in ("NEE", "DUK", "SO", "D", "AEP")},
    # Materials
    **{t: "Materials" for t in ("LIN", "FCX", "NEM", "SHW", "APD")},
    # Real Estate
    **{t: "Real Estate" for t in ("AMT", "PLD", "O", "SPG", "EQIX")},
}

# ticker -> resolved bucket, cached for the worker's lifetime (sector is stable).
_bucket_cache: dict[str, str] = {}


def _looks_like_cash(symbol: str, name: str | None) -> bool:
    if symbol in _CASH_TICKERS:
        return True
    text = (name or "").lower()
    return "money market" in text or "cash reserves" in text


def instant_bucket(symbol: str, *, name: str | None = None) -> str | None:
    """The bucket for ``symbol`` if it can be decided WITHOUT a network call
    (cash, index ETF, or the curated sector map), else None. Lets the caller
    classify most of a portfolio synchronously."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return OTHER
    if _looks_like_cash(sym, name):
        return CASH
    if sym in _INDEX_ETFS:
        return MARKET_INDEX
    return _TICKER_SECTOR.get(sym)


def classify(symbol: str, *, name: str | None = None, industry: str | None = None) -> str:
    """Full classification. ``industry`` (Finnhub ``finnhubIndustry``) is used only
    when the instant path can't decide; it's kept raw so tech shows detailed sub-
    sectors ("Semiconductors", "Software") rather than one broad bucket."""
    fast = instant_bucket(symbol, name=name)
    if fast is not None:
        return fast
    return industry.strip() if (industry and industry.strip()) else FUNDS


def industry_for(symbol: str) -> str | None:
    """Finnhub industry for ``symbol`` (best-effort). Returns None on any failure so
    classification falls back gracefully. Only called for the unknown tail."""
    from src.tools.finnhub.client import FinnhubClient, FinnhubError, is_finnhub_configured

    if not is_finnhub_configured():
        return None
    try:
        profile = FinnhubClient().company_profile(symbol)
    except FinnhubError as exc:
        logger.debug("Finnhub profile lookup failed for %s: %s", symbol, exc)
        return None
    except Exception as exc:  # noqa: BLE001 — never break the portfolio view
        logger.warning("Unexpected sector lookup error for %s: %s", symbol, type(exc).__name__)
        return None
    return (profile or {}).get("finnhubIndustry") or None


def bucket_for(symbol: str, *, name: str | None = None) -> str:
    """Resolve and cache the bucket for ``symbol``, hitting Finnhub only for the
    tail the instant path can't classify."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return OTHER
    if sym in _bucket_cache:
        return _bucket_cache[sym]
    fast = instant_bucket(sym, name=name)
    result = fast if fast is not None else classify(sym, name=name, industry=industry_for(sym))
    _bucket_cache[sym] = result
    return result
