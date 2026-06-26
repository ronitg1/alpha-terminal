"""Pattern-scanner options backtest engine.

Answers the question: *when the Pattern Scanner flags a play, what happens if
I buy an option on it?* For every historical bar where a detector fires, we
simulate buying a call (bullish pattern) or put (bearish pattern) at a target
delta + days-to-expiry, then selling it ``hold`` candles later. Aggregating
across all fired signals gives a win rate / average return / expectancy, and
sweeping over (delta, DTE, hold) finds the combination that historically paid
best — i.e. *which option to buy and how long to hold*.

Design split (matches the rest of ``src/backtesting``):
- This module is **pure math**: signal replay, strike selection, premium
  pricing, P&L, aggregation. It does no network IO, so it is unit-testable
  with synthetic candles.
- The route (``app/backend/routes/patterns.py``) does the IO: fetching
  candles, picking the realized-vol input, and (for real-fill pricing)
  fetching the historical option contract series, which it then hands to
  ``price_from_series`` here.

Pricing has two modes, chosen by the caller:
- **real** — premiums from the actual listed contract's historical bars
  (per-bar close), aligned to the fire/exit bars by their ET label. The plan
  exposes intraday option aggregates, so this works at hourly / 15-min too.
- **bsm** — Black-Scholes proxy off the underlying's path + realized vol.
  Fast and free, but diverges from real premiums (empirically ~24% median,
  worse for OTM / high-IV names), so it is a fallback, not the default.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal

from src.backtesting.options_proxy import (
    RISK_FREE_RATE,
    bsm_price,
    strike_for_delta,
)

OptionType = Literal["call", "put"]
Detector = Callable[[list[dict]], list[dict]]

# Seconds in a (365-day) year — TTM decays over calendar time, including
# nights and weekends, because that's how option theta actually works.
_SECONDS_PER_YEAR = 365.0 * 24.0 * 3600.0


@dataclass(frozen=True)
class PatternSignal:
    """One historical bar where a detector fired."""

    ticker: str
    pattern: str
    bullish: bool
    fire_idx: int          # index into the candle list of the breakout bar
    fire_date: str         # that bar's label ("YYYY-MM-DD" or "...THH:MM")
    entry_spot: float      # underlying close at the fire bar
    confidence: float


@dataclass(frozen=True)
class TradeConfig:
    """A single option choice to test: target delta, DTE, and hold length."""

    delta: float           # target option delta magnitude, e.g. 0.40
    dte: int               # days-to-expiry of the contract bought
    hold: int              # candles held before selling (1 = next candle close)


@dataclass
class Trade:
    """Result of one simulated option trade."""

    ticker: str
    pattern: str
    option_type: OptionType
    strike: float
    open_date: str
    close_date: str
    entry_premium: float
    exit_premium: float
    pnl: float             # per share, after slippage
    return_pct: float
    confidence: float
    synthetic: bool        # True when priced by BSM fallback, not real fills
    contract: str | None   # Polygon contract ticker when real-priced


def _label_to_dt(label: str) -> datetime:
    """Parse a candle label into a naive datetime for elapsed-time math.

    Daily/weekly labels are ``YYYY-MM-DD``; intraday labels carry ``THH:MM``.
    """
    return datetime.fromisoformat(label) if "T" in label else datetime.fromisoformat(label + "T00:00")


def replay_signals(
    ticker: str,
    candles: list[dict],
    detectors: dict[str, Detector],
    bullish_set: set[str],
    *,
    patterns: list[str] | None = None,
    min_confidence: float = 0.0,
) -> list[PatternSignal]:
    """Run each selected detector over the full candle history and collect the
    bars where a pattern completed (broke out).

    Detectors already return one dict per completed pattern with ``end_date``
    set to the breakout bar's label; we map that label back to its index so
    the simulator can walk forward ``hold`` candles. Signals whose breakout
    bar is the very last candle (no forward bar to exit into) are kept here —
    the simulator drops them when it can't form the hold.
    """
    label_to_idx = {c["date"]: i for i, c in enumerate(candles)}
    selected = patterns or list(detectors.keys())
    signals: list[PatternSignal] = []
    for name in selected:
        detector = detectors.get(name)
        if detector is None:
            continue
        for det in detector(candles):
            if float(det.get("confidence", 0.0)) < min_confidence:
                continue
            idx = label_to_idx.get(det.get("end_date"))
            if idx is None:
                continue
            close = candles[idx].get("close")
            if not close or close <= 0:
                continue
            signals.append(
                PatternSignal(
                    ticker=ticker,
                    pattern=name,
                    bullish=name in bullish_set,
                    fire_idx=idx,
                    fire_date=str(det["end_date"]),
                    entry_spot=float(close),
                    confidence=float(det.get("confidence", 0.0)),
                )
            )
    signals.sort(key=lambda s: s.fire_idx)
    return signals


def option_type_for(signal: PatternSignal, direction: str) -> OptionType:
    """Resolve which option a signal buys.

    ``direction='auto'`` follows the pattern (bullish -> call, bearish -> put);
    ``'calls'`` / ``'puts'`` force one leg regardless of pattern bias.
    """
    if direction == "calls":
        return "call"
    if direction == "puts":
        return "put"
    return "call" if signal.bullish else "put"


def target_strike(signal: PatternSignal, sigma: float, cfg: TradeConfig, option_type: OptionType) -> float:
    """Strike of the contract this signal would buy, from the target delta."""
    return strike_for_delta(
        spot=signal.entry_spot,
        target_delta=cfg.delta,
        time_to_expiry_years=cfg.dte / 365.0,
        sigma=sigma,
        option_type=option_type,
    )


def _apply_slippage(entry: float, exit_: float, slippage_pct: float | None) -> tuple[float, float]:
    """Cross the spread: buy a half-spread up, sell a half-spread down."""
    if not slippage_pct or slippage_pct <= 0:
        return entry, exit_
    half = slippage_pct / 2.0
    return entry * (1.0 + half), exit_ * (1.0 - half)


def _exit_index(signal: PatternSignal, candles: list[dict], hold: int) -> int | None:
    idx = signal.fire_idx + hold
    if idx >= len(candles):
        return None
    return idx


def price_bsm(
    signal: PatternSignal,
    candles: list[dict],
    sigma: float,
    cfg: TradeConfig,
    option_type: OptionType,
    *,
    slippage_pct: float | None = 0.05,
    risk_free: float = RISK_FREE_RATE,
) -> Trade | None:
    """Black-Scholes-proxy trade: price entry/exit off the underlying's path.

    Returns None when there aren't enough forward candles to hold the trade.
    """
    exit_idx = _exit_index(signal, candles, cfg.hold)
    if exit_idx is None:
        return None
    spot0 = signal.entry_spot
    ttm0 = cfg.dte / 365.0
    strike = target_strike(signal, sigma, cfg, option_type)
    entry_prem = bsm_price(
        spot=spot0, strike=strike, time_to_expiry_years=ttm0,
        sigma=sigma, option_type=option_type, risk_free=risk_free,
    )
    if entry_prem <= 0:
        return None
    elapsed_years = max(
        0.0,
        (_label_to_dt(candles[exit_idx]["date"]) - _label_to_dt(signal.fire_date)).total_seconds()
        / _SECONDS_PER_YEAR,
    )
    spot1 = float(candles[exit_idx]["close"])
    exit_prem = bsm_price(
        spot=spot1, strike=strike, time_to_expiry_years=max(0.0, ttm0 - elapsed_years),
        sigma=sigma, option_type=option_type, risk_free=risk_free,
    )
    entry_eff, exit_eff = _apply_slippage(entry_prem, exit_prem, slippage_pct)
    pnl = exit_eff - entry_eff
    return Trade(
        ticker=signal.ticker, pattern=signal.pattern, option_type=option_type,
        strike=strike, open_date=signal.fire_date, close_date=str(candles[exit_idx]["date"]),
        entry_premium=entry_eff, exit_premium=exit_eff, pnl=pnl,
        return_pct=pnl / entry_eff if entry_eff > 0 else 0.0,
        confidence=signal.confidence, synthetic=True, contract=None,
    )


def _series_lookup(series: dict[str, float], target_label: str, sorted_labels: list[str]) -> float | None:
    """Premium at ``target_label``, or the first available bar at/after it."""
    direct = series.get(target_label)
    if direct and direct > 0:
        return direct
    for lab in sorted_labels:
        if lab >= target_label and series.get(lab, 0.0) > 0:
            return series[lab]
    return None


def price_from_series(
    signal: PatternSignal,
    candles: list[dict],
    cfg: TradeConfig,
    option_type: OptionType,
    *,
    series: dict[str, float],
    strike: float,
    contract: str | None,
    slippage_pct: float | None = 0.05,
) -> Trade | None:
    """Real-fill trade: price entry/exit from a contract's historical bars.

    ``series`` maps candle label -> contract close (built by the route from
    the option aggregates). Exit is the bar ``hold`` candles after the fire
    bar, matched by label so it lines up with the candle the user sees.
    Returns None if there aren't enough forward candles or the contract has no
    usable entry/exit bar — the caller then falls back to ``price_bsm``.
    """
    exit_idx = _exit_index(signal, candles, cfg.hold)
    if exit_idx is None or not series:
        return None
    sorted_labels = sorted(series.keys())
    entry_prem = _series_lookup(series, signal.fire_date, sorted_labels)
    exit_label = str(candles[exit_idx]["date"])
    exit_prem = _series_lookup(series, exit_label, sorted_labels)
    if entry_prem is None or exit_prem is None or entry_prem <= 0:
        return None
    entry_eff, exit_eff = _apply_slippage(entry_prem, exit_prem, slippage_pct)
    pnl = exit_eff - entry_eff
    return Trade(
        ticker=signal.ticker, pattern=signal.pattern, option_type=option_type,
        strike=strike, open_date=signal.fire_date, close_date=exit_label,
        entry_premium=entry_eff, exit_premium=exit_eff, pnl=pnl,
        return_pct=pnl / entry_eff if entry_eff > 0 else 0.0,
        confidence=signal.confidence, synthetic=False, contract=contract,
    )


def aggregate(trades: list[Trade]) -> dict:
    """Summary stats over a set of trades (one TradeConfig's results)."""
    n = len(trades)
    if n == 0:
        return {
            "n_trades": 0, "n_wins": 0, "win_rate": 0.0, "avg_return_pct": 0.0,
            "total_pnl": 0.0, "expectancy": 0.0, "n_synthetic": 0, "by_pattern": {},
        }
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    returns = [t.return_pct for t in trades]
    avg_win = sum(t.return_pct for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.return_pct for t in losses) / len(losses) if losses else 0.0
    win_rate = len(wins) / n
    # Expectancy in return-% terms: prob-weighted average outcome per trade.
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

    by_pattern: dict[str, dict] = {}
    for t in trades:
        b = by_pattern.setdefault(t.pattern, {"n": 0, "wins": 0, "pnl": 0.0, "ret": 0.0})
        b["n"] += 1
        b["wins"] += 1 if t.pnl > 0 else 0
        b["pnl"] += t.pnl
        b["ret"] += t.return_pct
    for b in by_pattern.values():
        b["win_rate"] = b["wins"] / b["n"] if b["n"] else 0.0
        b["avg_return_pct"] = b["ret"] / b["n"] if b["n"] else 0.0
        del b["ret"]

    return {
        "n_trades": n,
        "n_wins": len(wins),
        "win_rate": win_rate,
        "avg_return_pct": sum(returns) / n,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "total_pnl": sum(t.pnl for t in trades),
        "expectancy": expectancy,
        "n_synthetic": sum(1 for t in trades if t.synthetic),
        "by_pattern": by_pattern,
    }


def build_grid(deltas: list[float], dtes: list[int], holds: list[int]) -> list[TradeConfig]:
    """Cartesian product of the sweep axes, as TradeConfigs."""
    return [
        TradeConfig(delta=d, dte=dte, hold=h)
        for d in deltas
        for dte in dtes
        for h in holds
    ]
