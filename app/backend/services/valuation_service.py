"""Valuation "football field" for the thesis view.

Produces fair-value BANDS (low / mid / high) per method so the UI can draw a
football field and the thesis can ground its bull/bear call in an estimated fair
value — instead of hand-waving "bullish."

Methods (all from Finnhub's free ``basic_financials`` — this deployment's market-data
plan does NOT include company statements, which 403):
  • Mini-DCF — FCF/share grown at a fading rate and discounted at a CAPM cost of
    equity (risk-free + beta·ERP), plus a Gordon-growth terminal value. The band
    comes from flexing the discount rate ±1.5pp (the value's most sensitive input).
  • Exit multiple (comps) — project TTM EPS forward 5y at the name's growth, apply a
    normalized terminal P/E, and discount back. The comps leg.
  • 52-week range — the band the market has actually paid; a reality check.

Every band is CLAMPED into a sane window around the current price and dropped if too
wide, so ranges never come out "ridiculously off or large" (the user's explicit ask).
FCF/share and the growth inputs are best-effort proxies from free metrics, so treat
the output as a triangulation, not a precise target.
"""
from __future__ import annotations

import datetime
import logging
import statistics
from typing import Any

logger = logging.getLogger(__name__)

_FLOOR = 0.45   # a fair value below 45% of price is almost always a data artefact
_CEIL = 2.2     # …or above 2.2x
_MAX_REL_WIDTH = 1.15  # drop bands wider than 115% of their midpoint (too vague)

# CAPM inputs for the discount rate.
_RISK_FREE = 0.045
_EQUITY_RISK_PREMIUM = 0.05
_TERMINAL_GROWTH = 0.025


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
    # A band that collapsed to (near) zero width sits entirely at a clamp bound — its
    # estimate is outside the sane window (far from the price), so we drop it rather
    # than show a misleading bar pinned at the edge.
    if (hi - lo) < price * 0.02:
        return None
    return {"method": method, "low": round(lo, 2), "mid": round(mid, 2), "high": round(hi, 2)}


def _cost_of_equity(beta: float | None) -> float:
    """CAPM, bounded to a sane 7–14%."""
    return max(0.07, min(0.14, _RISK_FREE + (beta if beta and beta > 0 else 1.0) * _EQUITY_RISK_PREMIUM))


def _dcf_intrinsic(fcf_ps: float, g0: float, discount: float, years: int = 10) -> float:
    """PV of FCF/share faded from g0 to terminal growth over ``years`` + terminal value."""
    g = g0
    g_step = (_TERMINAL_GROWTH - g0) / (years - 1)
    fcf = fcf_ps
    pv_sum = 0.0
    for yr in range(1, years + 1):
        fcf = fcf * (1 + g)
        pv_sum += fcf / (1 + discount) ** yr
        g += g_step
    # fcf is now the year-N cash flow; Gordon-growth terminal value, discounted back.
    tv = fcf * (1 + _TERMINAL_GROWTH) / (discount - _TERMINAL_GROWTH) / (1 + discount) ** years
    return pv_sum + tv


def _mini_dcf(price: float, fcf_ps: float | None, growth_pct: float | None, beta: float | None) -> dict[str, Any] | None:
    if not fcf_ps or fcf_ps <= 0:
        return None
    g0 = max(0.0, min(0.18, (growth_pct or 6.0) / 100.0))  # cap stage-1 growth at 18%
    disc = _cost_of_equity(beta)
    mid = _dcf_intrinsic(fcf_ps, g0, disc)
    lo = _dcf_intrinsic(fcf_ps, g0, disc + 0.015)   # higher discount → lower value (bear)
    hi = _dcf_intrinsic(fcf_ps, g0, max(disc - 0.015, _TERMINAL_GROWTH + 0.02))
    return _band("Mini-DCF", lo, mid, hi, price)


def _exit_multiple(price: float, eps: float | None, growth_pct: float | None, beta: float | None) -> dict[str, Any] | None:
    """Project EPS 5y forward, apply a growth-normalized terminal P/E, discount back."""
    if not eps or eps <= 0:
        return None
    g = max(0.0, min(0.20, (growth_pct or 6.0) / 100.0))
    years = 5
    fwd_eps = eps * (1 + g) ** years
    disc = _cost_of_equity(beta)
    # Normalized terminal multiple scaled to growth, bounded 10–28x.
    base_mult = max(10.0, min(28.0, 11.0 + (growth_pct or 6.0)))
    pv = lambda mult: fwd_eps * mult / (1 + disc) ** years  # noqa: E731
    return _band("Exit multiple (comps)", pv(base_mult * 0.82), pv(base_mult), pv(base_mult * 1.18), price)


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
    growth = _num(m.get("epsGrowth5Y")) or _num(m.get("ebitdaCagr5Y")) or _num(m.get("revenueGrowth5Y"))
    beta = _num(m.get("beta"))
    cfps = _num(m.get("cashFlowPerShareTTM"))  # operating CF/share (pre-capex)
    ev_fcf = _num(m.get("currentEv/freeCashFlowTTM"))

    price = None
    if pe and eps and eps > 0:
        price = pe * eps
    elif hi52 and lo52:
        price = (hi52 + lo52) / 2
    if not price or price <= 0:
        return unavailable

    # FCF/share proxy: prefer the EV/FCF-derived figure — that's REAL post-capex free
    # cash flow (EV≈price approximation), which avoids overstating cash generation for
    # capex-heavy names (energy, industrials) the way operating-CF/share would. Fall
    # back to operating CF/share only when EV/FCF is unavailable.
    fcf_ps = (price / ev_fcf) if (ev_fcf and ev_fcf > 0) else (cfps if cfps and cfps > 0 else None)

    bands: list[dict[str, Any]] = []
    for b in (
        _mini_dcf(price, fcf_ps, growth, beta),
        _exit_multiple(price, eps, growth, beta),
    ):
        if b:
            bands.append(b)
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
