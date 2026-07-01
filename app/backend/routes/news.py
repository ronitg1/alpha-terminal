"""Market News routes — book/sector headlines, ticker search, macro feed.

Backed by the Finnhub-primary news service (Polygon fallback for per-ticker).
The macro feed requires Finnhub; ``configured: false`` is returned when no
FINNHUB_API_KEY is set so the UI hides the macro column gracefully.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.backend.services import finnhub_news
from app.backend.services import sleeve_config_service
from src.tools.finnhub import is_finnhub_configured

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/news")


def _sleeve_for_ticker(ticker: str) -> str | None:
    """Which sleeve (if any) holds this ticker — used for summary relevance.

    Reads through the backend-aware sleeve config so it honors STORAGE_BACKEND
    (per-user sleeves from Postgres under the db backend, the module global
    under the file backend)."""
    t = ticker.upper()
    for name, sleeve in sleeve_config_service.read_sleeves().items():
        if t in {x.upper() for x in sleeve.get("tickers", [])}:
            return name
    return None


@router.get("/feed")
async def get_news_feed(tickers: str = "", hours: int = 168) -> dict[str, Any]:
    """Book headlines (fanned across the given tickers) + categorized macro feed.

    ``tickers`` is a comma-separated list (typically the user's sleeve +
    watchlist names). When Finnhub isn't configured, book headlines still work
    via the Polygon fallback but the macro feed is empty.
    """
    if not is_finnhub_configured():
        # Polygon fallback still serves per-ticker book headlines.
        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
        if not ticker_list:
            return {"configured": False, "book_headlines": [], "macro": [], "macro_category_counts": {}}
        feed = await asyncio.to_thread(finnhub_news.build_feed, ticker_list, hours_back=hours)
        feed["configured"] = False
        return feed

    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    return await asyncio.to_thread(finnhub_news.build_feed, ticker_list, hours_back=hours)


@router.get("/ticker/{ticker}")
async def get_ticker_news(ticker: str, hours: int = 168) -> dict[str, Any]:
    """News for a single searched ticker (Finnhub primary, Polygon fallback)."""
    symbol = (ticker or "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Ticker is required.")
    articles = await asyncio.to_thread(finnhub_news.ticker_feed, symbol, hours_back=hours)
    return {"ticker": symbol, "articles": articles}


@router.get("/thesis-impact")
async def get_thesis_impact(tickers: str = "", limit: int = 12) -> dict[str, Any]:
    """Recent headlines for the given tickers, each with a one-line Claude read of
    what changed and whether it supports / threatens / is neutral to the thesis.
    One batched LLM call, cached per (ticker-set, day). Best-effort."""
    from app.backend.services import news_impact

    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        return {"items": []}
    items = await asyncio.to_thread(news_impact.build_impact, ticker_list, limit=limit)
    return {"items": items}


class _SummarizeRequest(BaseModel):
    title: str
    description: str = ""
    related: str | None = None


@router.post("/summarize")
async def summarize_article(req: _SummarizeRequest) -> dict[str, Any]:
    """3-bullet AI summary + 'why it matters to my book' for one article."""
    if not req.title:
        raise HTTPException(status_code=400, detail="Article title is required.")
    sleeve = _sleeve_for_ticker(req.related) if req.related else None
    return await asyncio.to_thread(
        finnhub_news.summarize_article,
        title=req.title,
        description=req.description,
        related=req.related,
        sleeve=sleeve,
    )
