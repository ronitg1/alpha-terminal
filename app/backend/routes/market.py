"""Market data for the Portfolio Summary tab: index levels + market movers.

  GET /market/indices — major indices (via liquid ETF proxies) + a few macro
                        instruments, each with last / change / % / sparkline.
  GET /market/movers  — top gainers and losers across US stocks.

Indices reuse the cached ``/sleeves/quotes`` machinery (ETF proxies price nearly
identically to their index and need no extra entitlement). Movers come from
Polygon's snapshot. Both are best-effort and degrade to empty rather than error.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/market", tags=["market"])

# Market-wide data is identical for every user, and building it is slow (crypto/
# forex spot aggregates + Finnhub name warming for movers → ~20s cold). Cache each
# payload process-wide for a short window so only the first caller after expiry
# pays the cost; everyone else gets an instant hit.
_MARKET_TTL = 90.0
_market_cache: dict[str, tuple[float, dict[str, Any]]] = {}


async def _cached(key: str, builder: Any) -> dict[str, Any]:
    hit = _market_cache.get(key)
    if hit is not None and (time.monotonic() - hit[0]) < _MARKET_TTL:
        return hit[1]
    payload = await builder()
    _market_cache[key] = (time.monotonic(), payload)
    return payload

# (display label, ETF proxy ticker). Equity/bond indices priced off liquid ETF
# proxies — they track the index and need no index-data entitlement, and their
# share price sits in a sane range (SPY≈index/10, USO≈WTI), so the number reads
# right. Ordered as they should appear in the card.
_INDEX_PROXIES: list[tuple[str, str]] = [
    ("S&P 500", "SPY"),
    ("Nasdaq", "QQQ"),
    ("Dow Jones", "DIA"),
    ("Russell 2000", "IWM"),
    ("Crude Oil", "USO"),
    ("20Y Treasuries", "TLT"),
]

# (display label, Polygon spot ticker). Crypto (``X:``) and forex/metals (``C:``)
# quote in their real units, so an ETF proxy would misprice them badly
# (BITO≈$60 vs BTC≈$100k, GLD≈$240 vs gold≈$2,600/oz). We pull real spot bars.
_SPOT_INSTRUMENTS: list[tuple[str, str]] = [
    ("Bitcoin", "X:BTCUSD"),
    ("Ethereum", "X:ETHUSD"),
    ("Gold", "C:XAUUSD"),
    ("Silver", "C:XAGUSD"),
]

_MAX_MOVERS = 8


def _fetch_spot(label: str, ticker: str) -> dict[str, Any]:
    """Real spot last/prev/change/spark for a crypto or forex/metal ticker.

    Uses daily aggregates (last ~45 calendar days) so crypto weekends and forex
    gaps still yield a couple of closes. Best-effort: returns nulls on failure so
    the row degrades to a dash rather than dropping the instrument."""
    import datetime

    from src.tools.massive.client import MassiveClient

    empty = {"label": label, "symbol": ticker, "last": None, "prev_close": None,
             "change": None, "change_pct": None, "spark": []}
    try:
        today = datetime.date.today()
        start = today - datetime.timedelta(days=45)
        data = MassiveClient(timeout=8).get_daily_aggregates(ticker, start.isoformat(), today.isoformat())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Spot fetch (%s) failed: %s", ticker, type(exc).__name__)
        return empty
    bars = data.get("results") if isinstance(data, dict) else None
    closes = [b.get("c") for b in bars or [] if isinstance(b, dict) and b.get("c") is not None]
    if not closes:
        return empty
    last = closes[-1]
    prev = closes[-2] if len(closes) >= 2 else None
    change = round(last - prev, 2) if prev is not None else None
    change_pct = round((last - prev) / prev * 100, 2) if prev else None
    return {
        "label": label,
        "symbol": ticker,
        "last": round(last, 2),
        "prev_close": round(prev, 2) if prev is not None else None,
        "change": change,
        "change_pct": change_pct,
        "spark": [round(c, 2) for c in closes[-30:]],
    }


@router.get("/indices")
async def get_indices() -> dict[str, Any]:
    """Major indices (ETF proxies) + real crypto/metal spot, each with last /
    change / % / sparkline. Best-effort — degrades to dashes, never errors.
    Cached ~90s process-wide (same for every user)."""
    return await _cached("indices", _build_indices)


async def _build_indices() -> dict[str, Any]:
    from app.backend.routes.sleeves import get_quotes

    symbols = ",".join(sym for _, sym in _INDEX_PROXIES)

    async def _proxies() -> dict[str, Any]:
        try:
            payload = await get_quotes(tickers=symbols)
            return payload.get("quotes", {}) if isinstance(payload, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Index quotes failed: %s", type(exc).__name__)
            return {}

    quotes, *spots = await asyncio.gather(
        _proxies(),
        *(asyncio.to_thread(_fetch_spot, label, tkr) for label, tkr in _SPOT_INSTRUMENTS),
    )

    out = []
    for label, sym in _INDEX_PROXIES:
        q = quotes.get(sym) or {}
        out.append({
            "label": label,
            "symbol": sym,
            "last": q.get("last"),
            "prev_close": q.get("prev_close"),
            "change": round(q["last"] - q["prev_close"], 2) if (q.get("last") is not None and q.get("prev_close")) else None,
            "change_pct": q.get("pct_change"),
            "spark": q.get("spark") or [],
        })
    out.extend(spots)
    return {"indices": out}


def _fetch_movers(direction: str) -> list[dict[str, Any]]:
    from src.tools.massive.client import MassiveClient

    try:
        data = MassiveClient(timeout=8).get_market_movers(direction)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Movers fetch (%s) failed: %s", direction, type(exc).__name__)
        return []
    rows = data.get("tickers") if isinstance(data, dict) else None
    out: list[dict[str, Any]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        day = r.get("day") if isinstance(r.get("day"), dict) else {}
        prev = r.get("prevDay") if isinstance(r.get("prevDay"), dict) else {}
        last_trade = r.get("lastTrade") if isinstance(r.get("lastTrade"), dict) else {}
        # Prefer today's close; fall back to the last trade (pre/post-market) or prev close.
        last = day.get("c") or day.get("close") or last_trade.get("p") or prev.get("c")
        prev_c = prev.get("c")
        change = r.get("todaysChange")
        change_pct = r.get("todaysChangePerc")
        # Polygon reports todaysChange(Perc) as 0/None when the market is closed or
        # pre-market (no session yet). Derive the move from the previous close so the
        # card never shows a flat 0% for every name.
        if (not change_pct) and last and prev_c:
            change = round(last - prev_c, 2)
            change_pct = round((last - prev_c) / prev_c * 100, 2)
        out.append({
            "ticker": r.get("ticker"),
            "name": "",
            "change": change,
            "change_pct": change_pct,
            "price": last,
        })
    return out[:_MAX_MOVERS]


@router.get("/movers")
async def get_movers() -> dict[str, Any]:
    """Top gainers and losers across US stocks (best-effort, time-boxed).
    Cached ~90s process-wide (same for every user)."""
    return await _cached("movers", _build_movers)


@router.get("/search")
async def search_symbols(q: str = "") -> dict[str, Any]:
    """Symbol/company typeahead for the sidebar search. Returns US-listed common
    stocks + ETFs matching the query, most-relevant first. Best-effort."""
    query = q.strip()
    if len(query) < 1:
        return {"results": []}

    def _fetch() -> list[dict[str, Any]]:
        from src.tools.finnhub.client import FinnhubClient, is_finnhub_configured

        if not is_finnhub_configured():
            return []
        try:
            data = FinnhubClient().symbol_search(query)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Symbol search failed: %s", type(exc).__name__)
            return []
        rows = data.get("result") if isinstance(data, dict) else None
        out: list[dict[str, Any]] = []
        for r in rows or []:
            sym = str(r.get("symbol") or "")
            typ = str(r.get("type") or "")
            # US-listed only (skip foreign suffixes like .SS/.T) and real equities/ETFs.
            if not sym or "." in sym or ":" in sym:
                continue
            if typ and typ not in ("Common Stock", "ETP", "ETF", "ADR", "REIT"):
                continue
            out.append({"ticker": sym.upper(), "name": r.get("description") or "", "type": typ})
            if len(out) >= 12:
                break
        return out

    results = await asyncio.to_thread(_fetch)
    return {"results": results}


async def _build_movers() -> dict[str, Any]:
    try:
        gainers, losers = await asyncio.gather(
            asyncio.to_thread(_fetch_movers, "gainers"),
            asyncio.to_thread(_fetch_movers, "losers"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Movers failed: %s", type(exc).__name__)
        gainers, losers = [], []

    # Enrich with company names via the cached quote machinery (same source the
    # left nav uses). Best-effort — a missing name just leaves the ticker alone.
    tickers = [m["ticker"] for m in (*gainers, *losers) if m.get("ticker")]
    if tickers:
        from app.backend.routes.sleeves import get_quotes

        try:
            payload = await get_quotes(tickers=",".join(sorted(set(tickers))))
            quotes = payload.get("quotes", {}) if isinstance(payload, dict) else {}
            for m in (*gainers, *losers):
                nm = (quotes.get(m["ticker"]) or {}).get("name")
                if nm:
                    m["name"] = nm
        except Exception as exc:  # noqa: BLE001
            logger.warning("Mover name enrichment failed: %s", type(exc).__name__)

    return {"gainers": gainers, "losers": losers}
