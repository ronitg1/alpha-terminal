"""
Massive.com OHLCV data client for the pattern scanner.

Thin async wrapper around the Polygon-compatible aggregates endpoint with
in-memory TTL caching and a concurrency semaphore.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_MASSIVE_API_KEY = os.getenv("MASSIVE_API_KEY", "")
_BASE_URL = os.getenv("MASSIVE_BASE_URL", "https://api.polygon.io")
_CACHE_TTL = 900  # 15 minutes

_cache: dict[str, tuple[float, list]] = {}
_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(5)
    return _semaphore


def _cache_key(ticker: str, from_date: str, to_date: str) -> str:
    return f"{ticker}:{from_date}:{to_date}"


def _get_cached(key: str) -> list | None:
    entry = _cache.get(key)
    if entry and time.time() - entry[0] < _CACHE_TTL:
        return entry[1]
    if entry:
        del _cache[key]
    return None


def _set_cached(key: str, data: list) -> None:
    _cache[key] = (time.time(), data)


async def fetch_candles(
    ticker: str,
    from_date: str,
    to_date: str,
    timespan: str = "day",
    multiplier: int = 1,
) -> list[dict]:
    """Fetch daily OHLCV bars for *ticker* between *from_date* and *to_date*."""
    key = _cache_key(ticker, from_date, to_date)
    cached = _get_cached(key)
    if cached is not None:
        logger.debug("pattern_data cache hit: %s", ticker)
        return cached

    async with _get_semaphore():
        data = await _fetch_from_api(ticker, from_date, to_date, timespan, multiplier)

    _set_cached(key, data)
    return data


async def _fetch_from_api(
    ticker: str,
    from_date: str,
    to_date: str,
    timespan: str,
    multiplier: int,
) -> list[dict]:
    url = (
        f"{_BASE_URL}/v2/aggs/ticker/{ticker}/range"
        f"/{multiplier}/{timespan}/{from_date}/{to_date}"
    )
    params = {
        "apiKey": _MASSIVE_API_KEY,
        "adjusted": "true",
        "sort": "asc",
        "limit": 50000,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(2):
            try:
                resp = await client.get(url, params=params)

                if resp.status_code in (429, 503):
                    if attempt == 0:
                        logger.warning("Rate limited on %s — retrying", ticker)
                        await asyncio.sleep(1.5)
                        continue
                    resp.raise_for_status()

                if resp.status_code >= 400:
                    logger.error(
                        "HTTP %s for %s: %s",
                        resp.status_code,
                        ticker,
                        resp.text[:200],
                    )
                    resp.raise_for_status()

                raw = resp.json().get("results") or []
                candles: list[dict] = []
                for r in raw:
                    ts_ms = r.get("t", 0)
                    date_str = datetime.fromtimestamp(
                        ts_ms / 1000, tz=timezone.utc
                    ).strftime("%Y-%m-%d")
                    candles.append(
                        {
                            "date": date_str,
                            "open": r.get("o"),
                            "high": r.get("h"),
                            "low": r.get("l"),
                            "close": r.get("c"),
                            "volume": r.get("v"),
                            "vwap": r.get("vw"),
                        }
                    )
                return candles

            except httpx.HTTPStatusError:
                raise
            except Exception as exc:
                logger.exception("Unexpected error fetching %s: %s", ticker, exc)
                raise

    return []
