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

from src.tools.key_context import massive_api_key

logger = logging.getLogger(__name__)

_EASTERN = ZoneInfo("America/New_York")

_BASE_URL = os.getenv("MASSIVE_BASE_URL", "https://api.polygon.io")
_CACHE_TTL = 900  # 15 minutes

_cache: dict[str, tuple[float, list]] = {}
_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    # Concurrency cap for outbound aggregate fetches. 5 was far too low for
    # large universes — a 318-ticker "all watchlists" scan serialized into
    # ~4 min and blew past the client timeout. Polygon's paid tiers allow
    # heavy concurrency, so 16 keeps the wall-clock bounded without tripping
    # rate limits (each fetch already retries on 429).
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(16)
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

    # Don't pin responses that look truncated: a degraded provider can return
    # a partial window (bars stopping days before to_date), and caching it
    # froze charts mid-history for the full TTL. Serve it (better than
    # nothing) but refetch on the next request.
    if _looks_complete(data, to_date):
        _set_cached(key, data)
    elif data:
        logger.warning(
            "Not caching %s aggregates — last bar %s is stale vs %s (provider degraded?)",
            ticker, data[-1].get("date"), to_date,
        )
    return data


def _looks_complete(candles: list[dict], to_date: str) -> bool:
    """True when the series plausibly reaches the requested end of window.

    Allows a 5-calendar-day grace (weekends + a holiday + the end date being
    a non-trading day). Halted/delisted names will re-fetch each request —
    acceptable; pinning truncated data for live names is not.
    """
    if not candles:
        return False
    try:
        last = datetime.strptime(str(candles[-1]["date"])[:10], "%Y-%m-%d").date()
        end = datetime.strptime(to_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return True  # unparseable — don't block caching on a format quirk
    return (end - last).days <= 5


def _is_regular_hours(ts_utc: datetime, bar_minutes: int) -> bool:
    """True if the bar's interval OVERLAPS US regular trading hours
    (09:30–16:00 ET).

    Intraday aggregates include pre/post-market bars; their thin volume makes
    pattern detection noisy, so intraday fetches keep RTH-overlapping bars
    only. Overlap (not bar-open containment) matters for hourly bars: the
    09:00 bar holds the 09:30–10:00 open — dropping it loses the most
    important hour of the session. Uses America/New_York so DST is handled.
    """
    et = ts_utc.astimezone(_EASTERN)
    start = et.hour * 60 + et.minute
    end = start + bar_minutes
    return start < 16 * 60 and end > 9 * 60 + 30


async def _get_with_retry(
    client: httpx.AsyncClient, ticker: str, url: str, params: dict | None, headers: dict
) -> dict:
    """One Polygon GET with a single retry on rate-limit / transient network
    errors. Errors re-raise sanitized (no URL → no credential exposure)."""
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
                logger.error("HTTP %s for %s: %s", resp.status_code, ticker, resp.text[:200])
                resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Aggregates request for {ticker} failed with HTTP {exc.response.status_code}"
            ) from None
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt == 0:
                logger.warning("Network error on %s (%s) — retrying", ticker, type(exc).__name__)
                await asyncio.sleep(1.0)
                continue
            raise RuntimeError(
                f"Aggregates request for {ticker} failed: {type(exc).__name__}"
            ) from None
    return {}


# Polygon paginates aggregates when the underlying scan exceeds its internal
# budget — heavily-traded names (NVDA, AMD) truncate mid-window on long
# intraday ranges and hand back a `next_url` cursor. 10 pages comfortably
# covers a 90-day hourly window on the busiest tickers.
_MAX_AGG_PAGES = 10


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
    # secret into httpx INFO logs and HTTPStatusError messages. Resolved per
    # request (per-user / approved-shared), not the raw env key.
    headers = {"Authorization": f"Bearer {massive_api_key()}"}
    params: dict | None = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 50000,
    }
    # Intraday = sub-daily bars (need RTH filtering + HH:MM timestamps).
    # Daily AND weekly bars are date-labeled and span full sessions, so they
    # skip both. Don't key this off "!= day" — that wrongly catches "week".
    intraday = timespan in ("minute", "hour")
    bar_minutes = multiplier * (60 if timespan == "hour" else 1) if intraday else 0

    raw: list[dict] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        next_url: str | None = url
        next_params = params
        for page in range(_MAX_AGG_PAGES):
            body = await _get_with_retry(client, ticker, next_url, next_params, headers)
            raw.extend(body.get("results") or [])
            next_url = body.get("next_url")
            next_params = None  # next_url carries the full query cursor
            if not next_url:
                break
        else:
            logger.warning(
                "Aggregates for %s still paginated after %d pages — series may be incomplete",
                ticker, _MAX_AGG_PAGES,
            )

    candles: list[dict] = []
    for r in raw:
        ts_ms = r.get("t", 0)
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        if intraday:
            if not _is_regular_hours(ts, bar_minutes):
                continue
            # ET wall-clock label ("09:30" = the open) — this is a US-equity
            # tool, so charts and pattern dates read in exchange time, not UTC.
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
