"""Valuation "football field" for the thesis view.

Produces a small set of fair-value BANDS (low / mid / high) per method so the UI can
draw a football field and the thesis can ground its bull/bear call in an estimated
fair value — instead of hand-waving "bullish."

Data reality: this deployment's market-data plan does NOT include company financial
statements (the ``/financials`` endpoints 403), so a full FCFF DCF isn't available.
We build the field from Finnhub's free ``basic_financials`` metrics, which we DO
have — TTM EPS, trailing P/E, multi-year EPS/revenue growth, and the 52-week range.
Methods:
  • Growth-justified P/E — a PEG-style earnings multiple scaled to the name's own
    growth, applied to TTM EPS. This is the "comps" leg: low-growth names get a low
    multiple, high-growth names a higher one, so it adapts across established and
    growth names.
  • 52-week range — the band the market has actually paid over the last year. Honest
    and bounded by construction; a useful reality check on the earnings estimate.

Every band is CLAMPED into a sane window around the current price (``_FLOOR``× to
``_CEIL``×) and any band wider than ``_MAX_REL_WIDTH`` of its midpoint is dropped —
the user's explicit requirement that ranges never come out "ridiculously off or
large." If nothing survives we return ``available: False`` and the UI hides it.
"""
from __future__ import annotations

import datetime
import logging
import statistics
from typing import Any

logger = logging.getLogger(__name__)

_FLOOR = 0.45   # a fair value below 45% of price is almost always a data artefact
_CEIL = 2.2     # …or above 2.2x
_MAX_REL_WIDTH = 1.1  # drop bands wider than 110% of their midpoint (too vague)


def _num(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None  # reject NaN
    except (TypeError, ValueError):
        return None


def _clamp(v: float, price: float) -> float:
    return max(price * _FLOOR, min(price * _CEIL, v))


def _band(method: str, lo: float, mid: float, hi: float, price: float) -> dict[str, Any] | None:
    lo, mid, hi = _clamp(lo, price), _clamp(mid, price), _clamp(hi, price)
    if not (lo <= mid <= hi) or mid <= 0 or (hi - lo) / mid > _MAX_REL_WIDTH:
        return None
    return {"method": method, "low": round(lo, 2), "mid": round(mid, 2), "high": round(hi, 2)}


def compute_valuation(ticker: str) -> dict[str, Any]:
    """Football-field valuation for one ticker from Finnhub metrics. Best-effort;
    returns ``{"available": False}`` when the data won't support a sane estimate."""
    from src.tools.finnhub.client import FinnhubClient, is_finnhub_configured

    symbol = ticker.strip().upper()
    end_date = datetime.date.today().isoformat()
    unavailable = {"available": False, "ticker": symbol}
    if not is_finnhub_configured():
        return unavailable

    try:
        data = FinnhubClient().basic_financials(symbol)
    except Exception as exc:  # noqa: BLE001 — provider hiccup; hide the chart
        logger.warning("Valuation metrics fetch failed for %s: %s", symbol, type(exc).__name__)
        return unavailable

    m = data.get("metric", {}) if isinstance(data, dict) else {}
    eps = _num(m.get("epsTTM")) or _num(m.get("epsBasicExclExtraItemsTTM"))
    pe = _num(m.get("peTTM")) or _num(m.get("peBasicExclExtraTTM"))
    hi52 = _num(m.get("52WeekHigh"))
    lo52 = _num(m.get("52WeekLow"))
    growth = _num(m.get("epsGrowth5Y")) or _num(m.get("epsGrowthTTMYoy")) or _num(m.get("revenueGrowth5Y"))

    # Current price: P/E × EPS is exactly the price Finnhub priced the ratios at.
    price = None
    if pe and eps and eps > 0:
        price = pe * eps
    elif hi52 and lo52:
        price = (hi52 + lo52) / 2  # fallback anchor
    if not price or price <= 0:
        return unavailable

    bands: list[dict[str, Any]] = []

    # Growth-justified earnings multiple (comps leg).
    if eps and eps > 0:
        g = max(0.0, min(35.0, growth if growth is not None else 6.0))
        mult = max(10.0, min(34.0, 11.0 + g))  # PEG-ish, bounded 10–34x
        b = _band("Growth-justified P/E", eps * mult * 0.85, eps * mult, eps * mult * 1.15, price)
        if b:
            bands.append(b)

    # 52-week range the market has actually paid.
    if hi52 and lo52 and hi52 > lo52:
        b = _band("52-week range", lo52, (hi52 + lo52) / 2, hi52, price)
        if b:
            bands.append(b)

    if not bands:
        return unavailable

    fair_value = statistics.median([b["mid"] for b in bands])
    return {
        "available": True,
        "ticker": symbol,
        "current_price": round(price, 2),
        "fair_value": round(fair_value, 2),
        "upside_pct": round((fair_value - price) / price * 100, 1),
        "growth_pct": round(growth, 1) if growth is not None else None,
        "bands": bands,
        "as_of": end_date,
    }
