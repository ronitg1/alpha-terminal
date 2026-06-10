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
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

_EASTERN = ZoneInfo("America/New_York")

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


def _cache_key(
    ticker: str, from_date: str, to_date: str, timespan: str, multiplier: int
) -> str:
    return f"{ticker}:{from_date}:{to_date}:{multiplier}{timespan}"


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
    """Fetch OHLCV bars for *ticker* between *from_date* and *to_date*.

    ``timespan``/``multiplier`` follow the Polygon aggregates convention
    (e.g. 1/"day", 1/"hour", 15/"minute"). Daily bars carry a ``date`` of
    ``YYYY-MM-DD``; intraday bars carry a full ISO timestamp so bars within
    one session stay distinct.
    """
    key = _cache_key(ticker, from_date, to_date, timespan, multiplier)
    cached = _get_cached(key)
    if cached is not None:
        logger.debug("pattern_data cache hit: %s", ticker)
        return cached

    async with _get_semaphore():
        data = await _fetch_from_api(ticker, from_date, to_date, timespan, multiplier)

    _set_cached(key, data)
    return data


def _is_regular_hours(ts_utc: datetime) -> bool:
    """True if the bar opens within US regular trading hours (09:30–16:00 ET).

    Intraday aggregates include pre/post-market bars; their thin volume makes
    pattern detection noisy, so intraday fetches keep RTH bars only. Uses the
    America/New_York zone so DST is handled correctly.
    """
    et = ts_utc.astimezone(_EASTERN)
    minutes = et.hour * 60 + et.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


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
    # The key travels in a header, never the URL: query-param auth leaks the
    # secret into httpx INFO logs and HTTPStatusError messages.
    headers = {"Authorization": f"Bearer {_MASSIVE_API_KEY}"}
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 50000,
    }
    intraday = timespan != "day"

    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(2):
            try:
                resp = await client.get(url, params=params, headers=headers)

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
                    ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                    if intraday:
                        if not _is_regular_hours(ts):
                            continue
                        # ET wall-clock label ("09:30" = the open) — this is a
                        # US-equity tool, so charts and pattern dates read in
                        # exchange time, not UTC.
                        date_str = ts.astimezone(_EASTERN).strftime("%Y-%m-%dT%H:%M")
                    else:
                        date_str = ts.strftime("%Y-%m-%d")
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

            except httpx.HTTPStatusError as exc:
                # Re-raise with the URL stripped so the secret-free guarantee
                # holds even if a future change reintroduces URL credentials.
                raise RuntimeError(
                    f"Aggregates request for {ticker} failed with HTTP "
                    f"{exc.response.status_code}"
                ) from None
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # Polygon flakes with ReadTimeouts under load — one retry
                # covers the common transient; persistent failure raises a
                # sanitized error (no URL → no credential exposure path).
                if attempt == 0:
                    logger.warning("Network error on %s (%s) — retrying", ticker, type(exc).__name__)
                    await asyncio.sleep(1.0)
                    continue
                raise RuntimeError(
                    f"Aggregates request for {ticker} failed: {type(exc).__name__}"
                ) from None
            except Exception as exc:
                logger.exception("Unexpected error fetching %s: %s", ticker, exc)
                raise

    return []
