"""Notable earnings THIS WEEK — a broader view than the watchlist calendar.

Merges a curated list of market-moving names with the user's watchlist, finds who
reports in the current Mon–Sun window, and splits them into:
  • upcoming — with the consensus EPS estimate
  • reported — with the actual vs estimate (beat/miss) and the post-print price
    reaction (the move on the first session after the print)

Everything is per-symbol Finnhub/Polygon (slow, rate-limited), so the whole payload
is cached for the week. Best-effort — a name we can't price just omits its reaction.
"""
from __future__ import annotations

import datetime
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# Curated market-moving names across sectors — the "notable" set, independent of the
# user's watchlist. Kept short because each name costs a rate-limited lookup.
_NOTABLE = [
    "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "JPM", "LLY",
    "XOM", "WMT", "V", "MA", "COST", "NFLX", "AMD", "ORCL", "CRM", "KO",
    "JNJ", "BAC", "DIS", "PEP",
]

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()
_TTL = 3 * 3600.0


def _num(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _reaction(client_massive: Any, sym: str, report_date: str) -> float | None:
    """First-session % move after the print: close after the report vs the prior
    close. Uses daily bars around the date; None if we can't line them up."""
    try:
        d = datetime.date.fromisoformat(report_date)
        data = client_massive.get_daily_aggregates(
            sym, (d - datetime.timedelta(days=8)).isoformat(), (d + datetime.timedelta(days=6)).isoformat()
        )
    except Exception:  # noqa: BLE001
        return None
    bars = data.get("results") if isinstance(data, dict) else None
    closes = [(b.get("t"), b.get("c")) for b in bars or [] if isinstance(b, dict) and b.get("c")]
    if len(closes) < 2:
        return None
    # Bars are ms timestamps; find the first bar strictly AFTER the report date.
    d_ms = int(datetime.datetime(d.year, d.month, d.day).timestamp() * 1000)
    idx = next((i for i, (t, _) in enumerate(closes) if t and t > d_ms), None)
    if idx is None or idx == 0:
        return None
    prev, post = closes[idx - 1][1], closes[idx][1]
    if not prev:
        return None
    return round((post - prev) / prev * 100, 2)


def build_week(watchlist: list[str]) -> dict[str, Any]:
    """This week's notable + watchlist earnings, split upcoming vs reported."""
    from src.tools.finnhub.client import FinnhubClient, is_finnhub_configured
    from src.tools.massive.client import MassiveClient

    if not is_finnhub_configured():
        return {"week_of": None, "upcoming": [], "reported": []}

    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    sunday = monday + datetime.timedelta(days=6)
    key = f"{monday.isoformat()}|{','.join(sorted(set(watchlist)))[:200]}"
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and (time.monotonic() - hit[0]) < _TTL:
            return hit[1]

    seen: set[str] = set()
    syms: list[str] = []
    for s in _NOTABLE + [t.strip().upper() for t in watchlist if t.strip()]:
        if s and s not in seen:
            seen.add(s)
            syms.append(s)
        if len(syms) >= 28:
            break

    fh = FinnhubClient()
    mv = None
    try:
        mv = MassiveClient(timeout=8)
    except Exception:  # noqa: BLE001
        mv = None

    upcoming: list[dict[str, Any]] = []
    reported: list[dict[str, Any]] = []
    for sym in syms:
        try:
            data = fh.earnings_calendar(start_date=monday.isoformat(), end_date=sunday.isoformat(), ticker=sym)
        except Exception:  # noqa: BLE001
            continue
        rows = data.get("earningsCalendar") if isinstance(data, dict) else None
        if not rows:
            continue
        r = min(rows, key=lambda x: x.get("date") or "9999")
        date = r.get("date")
        if not date:
            continue
        if date >= today.isoformat():
            upcoming.append({
                "ticker": sym, "date": date, "hour": r.get("hour"),
                "eps_estimate": _num(r.get("epsEstimate")),
            })
        else:
            actual = _num(r.get("epsActual"))
            est = _num(r.get("epsEstimate"))
            if actual is None:  # calendar sometimes lags; pull from the surprises series
                try:
                    surp = fh.earnings_surprises(sym, limit=1) or []
                    if surp:
                        actual = _num(surp[0].get("actual"))
                        est = est if est is not None else _num(surp[0].get("estimate"))
                except Exception:  # noqa: BLE001
                    pass
            surprise = None
            if actual is not None and est not in (None, 0):
                surprise = round((actual - est) / abs(est) * 100, 1)
            reported.append({
                "ticker": sym, "date": date, "hour": r.get("hour"),
                "eps_actual": actual, "eps_estimate": est, "surprise_pct": surprise,
                "reaction_pct": _reaction(mv, sym, date) if mv else None,
            })

    upcoming.sort(key=lambda e: (e.get("date") or "9999", e.get("ticker")))
    reported.sort(key=lambda e: (e.get("date") or "0000", e.get("ticker")), reverse=True)
    out = {"week_of": monday.isoformat(), "upcoming": upcoming, "reported": reported}
    with _cache_lock:
        _cache[key] = (time.monotonic(), out)
    return out
