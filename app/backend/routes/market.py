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
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/market", tags=["market"])

# (display label, ETF/crypto proxy ticker). Proxies avoid index-data entitlement.
_INDEX_PROXIES: list[tuple[str, str]] = [
    ("S&P 500", "SPY"),
    ("Nasdaq", "QQQ"),
    ("Dow Jones", "DIA"),
    ("Russell 2000", "IWM"),
    ("Gold", "GLD"),
    ("Crude Oil", "USO"),
    ("Bitcoin", "BITO"),
    ("20Y Treasuries", "TLT"),
]

_MAX_MOVERS = 8


@router.get("/indices")
async def get_indices() -> dict[str, Any]:
    """Major indices + macro proxies with last / change / % / sparkline."""
    from app.backend.routes.sleeves import get_quotes

    symbols = ",".join(sym for _, sym in _INDEX_PROXIES)
    try:
        payload = await get_quotes(tickers=symbols)
        quotes = payload.get("quotes", {}) if isinstance(payload, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Index quotes failed: %s", type(exc).__name__)
        quotes = {}

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
        out.append({
            "ticker": r.get("ticker"),
            "change": r.get("todaysChange"),
            "change_pct": r.get("todaysChangePerc"),
            "price": day.get("c") or day.get("close"),
        })
    return out[:_MAX_MOVERS]


@router.get("/movers")
async def get_movers() -> dict[str, Any]:
    """Top gainers and losers across US stocks (best-effort, time-boxed)."""
    try:
        gainers, losers = await asyncio.gather(
            asyncio.to_thread(_fetch_movers, "gainers"),
            asyncio.to_thread(_fetch_movers, "losers"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Movers failed: %s", type(exc).__name__)
        gainers, losers = [], []
    return {"gainers": gainers, "losers": losers}
