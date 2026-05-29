"""Real-fill historical option pricing for the strategy backtest.

Sibling to ``options_proxy`` (Black-Scholes). When the user has a Polygon
options plan (Starter / Developer / Advanced), we can fetch actual
end-of-day premiums for the specific contract that existed on the entry
date instead of synthesizing them.

The flow per simulated trade:

1. ``pick_contract`` — call ``/v3/reference/options/contracts`` with
   ``as_of=entry_date, expired=true`` and a strike/expiry window. Pick
   the listed contract closest to the strategy's target.

2. ``get_close_range`` — call ``/v2/aggs/ticker/{O:...}/range/...`` once,
   spanning entry → exit. Pull entry and exit daily closes from the result.

3. Caller computes P&L = exit_close - entry_close (per share, per leg).

Why daily close rather than NBBO mid: Polygon **Developer** plan provides
daily OHLC aggregates per contract but not historical bid/ask quotes —
those are an **Advanced**-tier add-on. Closes are honest for ranking but
slightly optimistic for fills (they don't model spread drag); add an
explicit spread haircut in the caller if you want to be conservative.

If real data is missing (contract not listed, no aggregate bar on entry
or exit), the caller catches ``NoSuchContract`` or ``NoAggregateData``
and falls back to BSM — those trades are flagged ``synthetic=true`` so
the trade table can distinguish them.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Literal

from src.tools.massive.client import MassiveClient

logger = logging.getLogger(__name__)

OptionType = Literal["call", "put"]


class NoSuchContract(Exception):
    """No listed contract matched the target strike/expiry window."""


class NoAggregateData(Exception):
    """Contract exists but entry or exit daily bar is missing."""


# Strike window: ±10% around target. Wider than necessary at-the-money
# but tolerant of OTM/ITM strategies. Polygon caps results at limit=1000;
# this filter keeps the response small.
_STRIKE_WINDOW_PCT = 0.10
# Expiry window: ±14 calendar days around the target. Weekly chains
# don't always exist on every Friday; this gives the picker fallback.
_EXPIRY_WINDOW_DAYS = 14


def pick_contract(
    client: MassiveClient,
    *,
    underlying: str,
    as_of: _dt.date,
    target_strike: float,
    target_expiry_days: int,
    option_type: OptionType,
) -> dict:
    """Find the listed contract closest to (target_strike, as_of+target_expiry_days).

    Picks by combined distance — expiry-day delta plus strike-pct delta scaled
    so strike is weighted ~equally. Raises ``NoSuchContract`` if nothing in
    the window comes back from Polygon.
    """
    target_expiry = as_of + _dt.timedelta(days=target_expiry_days)
    # Polygon quirk: when ``as_of`` is set, ``expired`` is evaluated *relative
    # to that date*, not today. For a backtest we want contracts that hadn't
    # expired by the entry date — so expired=False is the right flag, even
    # though by "now" the contract has of course expired. expired=True + as_of
    # returns zero rows.
    response = client.list_options_contracts(
        underlying=underlying,
        as_of=as_of.isoformat(),
        expiration_date_gte=(target_expiry - _dt.timedelta(days=_EXPIRY_WINDOW_DAYS)).isoformat(),
        expiration_date_lte=(target_expiry + _dt.timedelta(days=_EXPIRY_WINDOW_DAYS)).isoformat(),
        strike_price_gte=target_strike * (1.0 - _STRIKE_WINDOW_PCT),
        strike_price_lte=target_strike * (1.0 + _STRIKE_WINDOW_PCT),
        contract_type=option_type,
        expired=False,
    )
    rows = response.get("results") or []
    if not rows:
        raise NoSuchContract(
            f"No {option_type}s for {underlying} near strike {target_strike:.2f} "
            f"× expiry {target_expiry} (as_of {as_of})"
        )

    def _distance(c: dict) -> float:
        exp = _dt.date.fromisoformat(c["expiration_date"])
        days_off = abs((exp - target_expiry).days)
        strike_off_pct = (
            abs(float(c["strike_price"]) - target_strike) / target_strike
            if target_strike > 0
            else 0.0
        )
        # Weight: 1 day expiry slip ≈ 1pp strike slip. Tuned so a perfect-strike
        # weekly beats a perfect-expiry monthly at e.g. +5% off strike.
        return days_off + strike_off_pct * 100.0

    rows.sort(key=_distance)
    return rows[0]


def get_close_range(
    client: MassiveClient,
    *,
    option_ticker: str,
    from_date: _dt.date,
    to_date: _dt.date,
) -> dict[_dt.date, float]:
    """Return ``{date: close}`` for a contract over [from, to] inclusive."""
    response = client.get_option_aggregates(
        option_ticker, from_date.isoformat(), to_date.isoformat()
    )
    rows = response.get("results") or []
    if not rows:
        raise NoAggregateData(
            f"No aggregates for {option_ticker} in [{from_date}, {to_date}]"
        )
    bars: dict[_dt.date, float] = {}
    for r in rows:
        ts_ms = int(r["t"])
        d = _dt.datetime.utcfromtimestamp(ts_ms / 1000.0).date()
        bars[d] = float(r["c"])
    return bars


def get_premium_series(
    client: MassiveClient,
    *,
    underlying: str,
    entry_date: _dt.date,
    exit_date: _dt.date,
    target_strike: float,
    target_expiry_days: int,
    option_type: OptionType,
) -> tuple[dict, dict[_dt.date, float]]:
    """Pick the best contract and return (meta, {date: close}) over [entry, exit].

    The caller scans the dict for entry/exit closes (and optionally stop-loss
    triggers). ``meta`` carries ticker / strike / expiry for trade-row metadata.

    Raises ``NoSuchContract`` if no listed contract matches the strike/expiry
    window. Raises ``NoAggregateData`` if the aggregates endpoint returns
    nothing for the chosen contract in the [entry, exit] window.
    """
    contract = pick_contract(
        client,
        underlying=underlying,
        as_of=entry_date,
        target_strike=target_strike,
        target_expiry_days=target_expiry_days,
        option_type=option_type,
    )
    option_ticker = str(contract["ticker"])
    bars = get_close_range(
        client,
        option_ticker=option_ticker,
        from_date=entry_date,
        to_date=exit_date,
    )
    meta = {
        "ticker": option_ticker,
        "strike": float(contract["strike_price"]),
        "expiration_date": str(contract["expiration_date"]),
    }
    return meta, bars


def get_historical_premiums(
    client: MassiveClient,
    *,
    underlying: str,
    entry_date: _dt.date,
    exit_date: _dt.date,
    target_strike: float,
    target_expiry_days: int,
    option_type: OptionType,
) -> tuple[float, float, dict]:
    """Pick the best contract and return (entry_close, exit_close, meta).

    Thin wrapper around ``get_premium_series`` for callers that don't need
    intra-window data (e.g., no stop-loss).
    """
    meta, bars = get_premium_series(
        client,
        underlying=underlying,
        entry_date=entry_date,
        exit_date=exit_date,
        target_strike=target_strike,
        target_expiry_days=target_expiry_days,
        option_type=option_type,
    )
    entry_close = pick_close(bars, entry_date, max_back=2)
    exit_close = pick_close(bars, exit_date, max_back=2)
    if entry_close is None or exit_close is None:
        raise NoAggregateData(
            f"Missing entry/exit bar for {meta['ticker']}: "
            f"have {sorted(bars.keys())[:5]}…"
        )
    return entry_close, exit_close, meta


def pick_close(
    bars: dict[_dt.date, float], target: _dt.date, *, max_back: int = 2
) -> float | None:
    """Return the close on ``target`` or up to ``max_back`` calendar days
    before it. Returns None if no bar in that window. Public so the
    endpoint's straddle-combining logic can reuse it."""
    for offset in range(max_back + 1):
        d = target - _dt.timedelta(days=offset)
        if d in bars:
            return bars[d]
    return None


def scan_stop_loss(
    series: dict[_dt.date, float] | list[tuple[_dt.date, float]],
    *,
    entry_premium: float,
    entry_date: _dt.date,
    exit_date: _dt.date,
    stop_loss_pct: float,
) -> tuple[float, _dt.date] | None:
    """Walk the series chronologically; return (premium, date) on the first
    day whose close is at or below ``entry_premium * (1 - stop_loss_pct)``.

    Returns None if no stop trigger inside [entry_date+1, exit_date]. Days
    outside that range are ignored. ``entry_premium`` of 0 or negative is
    treated as no-stop (can't compute a percentage).
    """
    if entry_premium <= 0 or stop_loss_pct <= 0:
        return None
    threshold = entry_premium * (1.0 - stop_loss_pct)
    items = (
        sorted(series.items()) if isinstance(series, dict) else sorted(series)
    )
    for d, close in items:
        if d <= entry_date or d > exit_date:
            continue
        if close <= threshold:
            return float(close), d
    return None


def bsm_premium_series(
    *,
    spot_series: list[float],
    strike: float,
    hold_days: int,
    sigma: float,
    option_type: OptionType,
    risk_free: float,
) -> list[float]:
    """Walk-forward BSM premium series matching the underlying's ``spot_series``.

    Day 0 is entry (full hold_days/252 time-to-expiry); each subsequent day
    decays TTM by 1/252. The final entry assumes option expires at the
    last sample (intrinsic value).
    """
    from src.backtesting.options_proxy import bsm_price as _bsm

    premiums: list[float] = []
    for i, s in enumerate(spot_series):
        ttm = max(0.0, (hold_days - i) / 252.0)
        premiums.append(
            _bsm(
                spot=s,
                strike=strike,
                time_to_expiry_years=ttm,
                sigma=sigma,
                option_type=option_type,
                risk_free=risk_free,
            )
        )
    return premiums


def bsm_straddle_series(
    *,
    spot_series: list[float],
    strike: float,
    hold_days: int,
    sigma: float,
    risk_free: float,
) -> list[float]:
    calls = bsm_premium_series(
        spot_series=spot_series, strike=strike, hold_days=hold_days,
        sigma=sigma, option_type="call", risk_free=risk_free,
    )
    puts = bsm_premium_series(
        spot_series=spot_series, strike=strike, hold_days=hold_days,
        sigma=sigma, option_type="put", risk_free=risk_free,
    )
    return [c + p for c, p in zip(calls, puts)]
