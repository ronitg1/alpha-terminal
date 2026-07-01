"""
Pattern Scanner API routes — mounted at /patterns/*.

Provides OHLCV-backed chart-pattern detection (12 pattern types) with
per-signal historical win-rate analysis and options strategy recommendations.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.backend.routes.pattern_data import fetch_candles
from src.backtesting import options_historical
from src.backtesting.options_proxy import realized_vol
from src.backtesting.pattern_options import (
    Trade,
    TradeConfig,
    aggregate,
    build_grid,
    option_type_for,
    price_bsm,
    price_from_series,
    replay_signals,
    target_strike,
)
from src.patterns.patterns import BULLISH_PATTERNS, PATTERN_DETECTORS
from src.patterns.trade_plan import (
    annualized_vol,
    build_option_plan,
    build_trade_plan,
    classify_signal,
    compute_atr,
)
from src.tools.massive import MassiveClient, MassiveError

_ET = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/patterns", tags=["patterns"])

# Detector thread pool. Pattern detection is the scan's real cost (12 numpy
# detectors per ticker); the heavy array work releases the GIL, so a wider
# pool meaningfully cuts wall-clock on large universes. Sized to the box.
_executor = ThreadPoolExecutor(max_workers=max(8, (os.cpu_count() or 4) * 2))

# Timeframe registry. Each entry sets the Polygon aggregate params plus the
# guardrails that keep intraday sane: a lookback clamp (dense bars + the
# O(n²)-ish detectors), the signal-analysis history window, and the win
# threshold for the historical backtest (3% in 20 daily bars is a different
# beast from 3% in 20 fifteen-minute bars — thresholds scale down with the
# bar size so "win" stays meaningful).
_TIMEFRAMES: dict[str, dict] = {
    "week": {
        "multiplier": 1,
        "timespan": "week",
        # Weekly bars are sparse: 5y ≈ 260 bars (well under the cap) and gives
        # the longer-base patterns (cup&handle, H&S) room to form. History runs
        # 7y so the 20-bar (~5-month) outcome window still leaves a real sample.
        "max_lookback_days": 1825,
        "history_days": 2555,
        # Win = a favourable move within 20 *weekly* bars (~5 months). Weekly
        # swings dwarf daily ones, so the bar is higher than daily's 3%.
        "win_threshold": 0.06,
    },
    "day": {
        "multiplier": 1,
        "timespan": "day",
        "max_lookback_days": 730,
        "history_days": 730,
        "win_threshold": 0.03,
    },
    "1h": {
        "multiplier": 1,
        "timespan": "hour",
        "max_lookback_days": 90,
        "history_days": 180,
        "win_threshold": 0.015,
    },
    "15m": {
        "multiplier": 15,
        "timespan": "minute",
        "max_lookback_days": 30,
        "history_days": 60,
        "win_threshold": 0.0075,
    },
}

# Hard cap on bars fed to the detectors — bounds scan latency on dense
# intraday series (a 90-day hourly window is ~580 RTH bars; 30 days of 15m
# is ~780; the cap only bites if someone widens the clamps).
_MAX_BARS = 1500


def _timeframe_cfg(timeframe: str) -> dict:
    cfg = _TIMEFRAMES.get(timeframe)
    if cfg is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown timeframe '{timeframe}'. Valid: {sorted(_TIMEFRAMES)}",
        )
    return cfg

DEFAULT_WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "MU", "INTC", "AVGO", "QCOM", "META",
    "GOOGL", "AMZN", "JPM", "BAC", "GS", "MS", "WFC", "C", "V", "MA",
    "DIS", "NFLX", "UBER", "SNOW", "CRM", "ADBE", "ORCL", "IBM", "CSCO", "TXN",
    "AMAT", "LRCX", "KLAC", "MRVL", "PANW", "ZS", "CRWD", "XOM", "CVX", "WMT",
    "HD", "MCD", "KO", "PEP", "JNJ", "PFE", "MRNA", "LLY", "UNH", "COST",
]


# ─── Pydantic models ───────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    tickers: list[str]
    patterns: list[str] = []
    lookback_days: int = 365
    timeframe: str = "day"


class ScanResult(BaseModel):
    ticker: str
    pattern: str
    start_date: str
    end_date: str
    confidence: float
    description: str
    key_levels: dict
    bullish: bool


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _date_range(lookback_days: int) -> tuple[str, str]:
    to_dt = date.today()
    from_dt = to_dt - timedelta(days=lookback_days)
    return from_dt.strftime("%Y-%m-%d"), to_dt.strftime("%Y-%m-%d")


def _run_one_detector(args: tuple) -> list[dict]:
    """Run a single detector in the thread pool (CPU-bound numpy work)."""
    name, detector, candles = args
    try:
        return detector(candles)
    except Exception as exc:
        logger.warning("Detector %s failed: %s", name, exc)
        return []


async def _scan_ticker(
    ticker: str,
    from_date: str,
    to_date: str,
    pattern_names: list[str],
    timespan: str = "day",
    multiplier: int = 1,
) -> list[dict]:
    """Fetch candles then run all selected detectors concurrently in the thread pool."""
    try:
        candles = await fetch_candles(
            ticker, from_date, to_date, timespan=timespan, multiplier=multiplier
        )
        if not candles:
            return []
        candles = candles[-_MAX_BARS:]
        loop = asyncio.get_running_loop()
        tasks = [
            loop.run_in_executor(
                _executor,
                _run_one_detector,
                (name, PATTERN_DETECTORS[name], candles),
            )
            for name in pattern_names
            if name in PATTERN_DETECTORS
        ]
        nested = await asyncio.gather(*tasks)
        detections = [item for sublist in nested for item in sublist]
        for d in detections:
            d["ticker"] = ticker
            d["bullish"] = d["pattern"] in BULLISH_PATTERNS
        return detections
    except Exception as exc:
        logger.error("Error scanning %s: %s", ticker, exc)
        return []


# ─── Endpoints ─────────────────────────────────────────────────────────────────

async def run_pattern_scan(
    tickers: list[str],
    pattern_names: list[str],
    timeframe: str,
    lookback_days: int,
) -> list[dict]:
    """Core scan used by the /scan route AND the scheduled background pre-scan.

    Fans out one fetch+detect per ticker (bounded by the data-client semaphore),
    flattens, and sorts by confidence. ``pattern_names`` must already be resolved
    (non-empty, validated by the caller)."""
    cfg = _timeframe_cfg(timeframe)
    from_date, to_date = _date_range(min(lookback_days, cfg["max_lookback_days"]))
    tasks = [
        _scan_ticker(
            ticker.upper().strip(),
            from_date,
            to_date,
            pattern_names,
            timespan=cfg["timespan"],
            multiplier=cfg["multiplier"],
        )
        for ticker in tickers
        if ticker.strip()
    ]
    nested = await asyncio.gather(*tasks)
    results = [item for sublist in nested for item in sublist]
    results.sort(key=lambda x: -x["confidence"])
    return results


@router.post("/scan", response_model=list[ScanResult])
async def scan(req: ScanRequest) -> list[dict]:
    """Scan a custom list of tickers for chart patterns."""
    if not req.tickers:
        raise HTTPException(status_code=400, detail="At least one ticker required.")

    pattern_names = req.patterns if req.patterns else list(PATTERN_DETECTORS.keys())
    unknown = [p for p in pattern_names if p not in PATTERN_DETECTORS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown patterns: {unknown}")

    return await run_pattern_scan(req.tickers, pattern_names, req.timeframe, req.lookback_days)


@router.get("/watchlist/scan", response_model=list[ScanResult])
async def watchlist_scan(
    patterns: Optional[str] = Query(None, description="Comma-separated pattern names"),
    lookback_days: int = Query(180, ge=30, le=365),
    timeframe: str = Query("day", description="Bar size: week | day | 1h | 15m"),
) -> list[dict]:
    """Scan the built-in 50-ticker large-cap watchlist."""
    pattern_names = (
        [p.strip() for p in patterns.split(",") if p.strip()]
        if patterns
        else list(PATTERN_DETECTORS.keys())
    )
    cfg = _timeframe_cfg(timeframe)
    from_date, to_date = _date_range(min(lookback_days, cfg["max_lookback_days"]))

    tasks = [
        _scan_ticker(
            ticker,
            from_date,
            to_date,
            pattern_names,
            timespan=cfg["timespan"],
            multiplier=cfg["multiplier"],
        )
        for ticker in DEFAULT_WATCHLIST
    ]
    nested = await asyncio.gather(*tasks)
    results = [item for sublist in nested for item in sublist]
    results.sort(key=lambda x: -x["confidence"])
    return results


@router.get("/chart/{ticker}")
async def chart(
    ticker: str,
    lookback_days: int = Query(365, ge=1, le=1825),  # 5y ceiling for weekly
    timeframe: str = Query("day", description="Bar size: week | day | 1h | 15m"),
) -> dict:
    """Return all OHLCV bars + all detected patterns (with trendlines) for one ticker."""
    cfg = _timeframe_cfg(timeframe)
    from_date, to_date = _date_range(min(lookback_days, cfg["max_lookback_days"]))
    ticker = ticker.upper()

    candles = await fetch_candles(
        ticker, from_date, to_date, timespan=cfg["timespan"], multiplier=cfg["multiplier"]
    )
    if not candles:
        raise HTTPException(status_code=404, detail=f"No data found for {ticker}")
    candles = candles[-_MAX_BARS:]

    loop = asyncio.get_running_loop()
    pattern_names = list(PATTERN_DETECTORS.keys())
    tasks = [
        loop.run_in_executor(
            _executor,
            _run_one_detector,
            (name, PATTERN_DETECTORS[name], candles),
        )
        for name in pattern_names
    ]
    nested = await asyncio.gather(*tasks)
    detections = [item for sublist in nested for item in sublist]
    for d in detections:
        d["ticker"] = ticker
        d["bullish"] = d["pattern"] in BULLISH_PATTERNS

    detections.sort(key=lambda x: -x["confidence"])
    return {"ticker": ticker, "candles": candles, "patterns": detections}


@router.get("/signal-analysis/{ticker}/{pattern_name}")
async def signal_analysis(
    ticker: str,
    pattern_name: str,
    timeframe: str = Query("day", description="Bar size: week | day | 1h | 15m"),
) -> dict:
    """
    Run a historical backtest for one ticker+pattern pair on the given timeframe.

    Win = max favourable excursion >= the timeframe's threshold (3% daily,
    1.5% hourly, 0.75% on 15m) over the next 20 bars. Recent signals where
    20 future bars don't yet exist are excluded from the denominator so
    win-rate is not artificially deflated. History window also scales with
    the timeframe (730d / 180d / 60d).
    """
    ticker = ticker.upper()
    pattern_name_decoded = pattern_name.replace("-", " ")

    if pattern_name_decoded not in PATTERN_DETECTORS:
        raise HTTPException(
            status_code=400, detail=f"Unknown pattern: {pattern_name_decoded}"
        )

    bullish = pattern_name_decoded in BULLISH_PATTERNS
    cfg = _timeframe_cfg(timeframe)
    from_date, to_date = _date_range(cfg["history_days"])

    candles = await fetch_candles(
        ticker, from_date, to_date, timespan=cfg["timespan"], multiplier=cfg["multiplier"]
    )
    if not candles:
        raise HTTPException(status_code=404, detail=f"No data found for {ticker}")

    loop = asyncio.get_running_loop()
    detections: list[dict] = await loop.run_in_executor(
        _executor,
        _run_one_detector,
        (pattern_name_decoded, PATTERN_DETECTORS[pattern_name_decoded], candles),
    )

    current_price = candles[-1]["close"]
    WIN_THRESHOLD = cfg["win_threshold"]
    OUTCOME_BARS = 20

    wins, losses = 0, 0
    win_pcts: list[float] = []
    loss_pcts: list[float] = []
    date_to_idx = {c["date"]: i for i, c in enumerate(candles)}

    for det in detections:
        end_idx = date_to_idx.get(det["end_date"])
        if end_idx is None:
            continue
        future_end = end_idx + OUTCOME_BARS
        if future_end >= len(candles):
            continue  # not enough future data — exclude from rate calculation

        entry_price = candles[end_idx]["close"]
        future_slice = candles[end_idx + 1 : future_end + 1]

        if bullish:
            mfe = max((c["high"] - entry_price) / entry_price for c in future_slice)
        else:
            mfe = max((entry_price - c["low"]) / entry_price for c in future_slice)

        if mfe >= WIN_THRESHOLD:
            wins += 1
            win_pcts.append(mfe * 100)
        else:
            losses += 1
            loss_pcts.append(mfe * 100)

    total = wins + losses
    win_rate = round(wins / total * 100, 1) if total > 0 else None
    avg_win = round(sum(win_pcts) / len(win_pcts), 1) if win_pcts else None
    avg_loss = round(sum(loss_pcts) / len(loss_pcts), 1) if loss_pcts else None

    return {
        "ticker": ticker,
        "pattern": pattern_name_decoded,
        "bullish": bullish,
        "current_price": current_price,
        "historical": {
            "total_signals": total,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
            "outcome_bars": OUTCOME_BARS,
            "win_threshold_pct": WIN_THRESHOLD * 100,
        },
        "options": _options_recommendations(pattern_name_decoded, bullish, current_price),
    }


# Preferred contract DTE per scan timeframe: day-trade patterns resolve in
# hours-to-days, swing patterns in weeks, weekly patterns in months — the
# option should outlive the move.
_PLAN_DTE: dict[str, int] = {"week": 90, "day": 30, "1h": 14, "15m": 7}

# Expected hold (calendar days) for the theta haircut on the target premium.
# Derived from the 20-bar outcome window each timeframe's win-rate uses:
# 20 weekly bars ≈ months; 20 daily ≈ weeks; 20 hourly ≈ 3 trading days; 20×15m ≈ a day.
_PLAN_HOLD_DAYS: dict[str, float] = {"week": 45.0, "day": 10.0, "1h": 3.0, "15m": 1.0}


# Contract-recommendation band: among 0.40–0.50 delta (magnitude) options
# expiring in 25–30 days, recommend the one with the best payoff-per-dollar if
# the pattern reaches its measured-move target. The delta floor avoids cheap
# far-OTM lottery tickets; the cap avoids paying up for deep-ITM stock proxies;
# the DTE window keeps theta manageable while giving the move room.
_REC_DELTA_LO, _REC_DELTA_HI = 0.40, 0.50
_REC_DTE_LO, _REC_DTE_HI = 25, 30


def _collect_chain_candidates(ticker: str, bullish: bool) -> list[dict]:
    """Snapshot the chain and return priced candidate contracts (the fields
    build_option_plan needs, plus dte/delta for filtering). Empty on failure —
    the caller then omits the premium plan."""
    import datetime as _dt

    today = _dt.date.today()
    try:
        client = MassiveClient()
        raw = client.get_options_chain(
            ticker,
            contract_type="call" if bullish else "put",
            # Pull a slightly wider expiry window than 25–30 so we have a
            # graceful fallback when no listed expiry lands exactly in-band.
            expiration_date_gte=(today + _dt.timedelta(days=_REC_DTE_LO - 7)).isoformat(),
            expiration_date_lte=(today + _dt.timedelta(days=_REC_DTE_HI + 10)).isoformat(),
            limit=250,
        )
    except MassiveError as exc:
        logger.warning("Chain fetch failed for %s trade plan: %s", ticker, exc)
        return []

    candidates: list[dict] = []
    for row in raw.get("results") or []:
        details = row.get("details") or {}
        strike = details.get("strike_price")
        exp = details.get("expiration_date")
        if not strike or not exp:
            continue
        try:
            dte = (_dt.date.fromisoformat(exp) - today).days
        except ValueError:
            continue
        if dte <= 0:
            continue
        quote = row.get("last_quote") or {}
        bid, ask = quote.get("bid"), quote.get("ask")
        trade = row.get("last_trade") or {}
        day = row.get("day") or {}
        mid = (
            (bid + ask) / 2.0 if (bid and ask and ask > 0)
            else trade.get("price") or day.get("close")
        )
        if not mid or mid <= 0:
            continue
        greeks = row.get("greeks") or {}
        candidates.append({
            "ticker": details.get("ticker"),
            "type": details.get("contract_type"),
            "strike": float(strike),
            "expiration": exp,
            "dte": dte,
            "mid": float(mid),
            "iv": row.get("implied_volatility") or greeks.get("iv"),
            "delta": greeks.get("delta"),
        })
    return candidates


def _choose_best_contract(
    candidates: list[dict],
    *,
    underlying_plan: dict,
    spot: float,
    hold_days: float,
) -> dict | None:
    """Pick the recommended contract from chain candidates (pure, testable).

    Preference order:
      1. In-band: |delta| in [0.40, 0.50] AND DTE in [25, 30]. Among these,
         pick the highest payoff-per-dollar if the pattern reaches target —
         i.e. max (target_premium − entry_premium) / entry_premium from the
         repriced option plan. Ties / non-viable contracts still rank by that
         ratio (least-bad wins), so we always recommend the best available.
      2. Fallback (nothing in-band, e.g. a name with sparse strikes/expiries):
         the candidate closest to 0.45 delta at the DTE nearest 27, priced.

    Returns the option-plan dict (from build_option_plan, which carries the
    contract's strike/expiry/delta + premium-space entry/stop/target), or None
    when no candidate can be priced.
    """
    mid_delta = (_REC_DELTA_LO + _REC_DELTA_HI) / 2.0
    mid_dte = (_REC_DTE_LO + _REC_DTE_HI) / 2.0

    def in_band(c: dict) -> bool:
        d = c.get("delta")
        return (
            d is not None
            and _REC_DELTA_LO <= abs(d) <= _REC_DELTA_HI
            and _REC_DTE_LO <= c["dte"] <= _REC_DTE_HI
        )

    def payoff_ratio(plan: dict | None) -> float:
        if not plan:
            return float("-inf")
        entry = plan.get("entry_premium") or 0.0
        if entry <= 0:
            return float("-inf")
        return (plan.get("target_premium", 0.0) - entry) / entry

    in_band_cands = [c for c in candidates if in_band(c)]
    if in_band_cands:
        best_plan, best_score = None, float("-inf")
        for c in in_band_cands:
            plan = build_option_plan(
                underlying_plan=underlying_plan, spot=spot, contract=c, hold_days=hold_days
            )
            score = payoff_ratio(plan)
            if score > best_score:
                best_score, best_plan = score, plan
        if best_plan is not None:
            best_plan["recommendation_basis"] = (
                f"best payoff-per-dollar in the {_REC_DELTA_LO:.2f}-{_REC_DELTA_HI:.2f}Δ, "
                f"{_REC_DTE_LO}-{_REC_DTE_HI} DTE band if the pattern reaches its target"
            )
            return best_plan

    # Fallback: no contract in-band — get as close to the band as the chain
    # allows (nearest 0.45 delta at the DTE nearest 27), then price it.
    priceable = [c for c in candidates if c.get("delta") is not None]
    if not priceable:
        return None
    priceable.sort(key=lambda c: (abs(c["dte"] - mid_dte), abs(abs(c["delta"]) - mid_delta)))
    for c in priceable:
        plan = build_option_plan(
            underlying_plan=underlying_plan, spot=spot, contract=c, hold_days=hold_days
        )
        if plan is not None:
            plan["recommendation_basis"] = (
                f"closest available to the {_REC_DELTA_LO:.2f}-{_REC_DELTA_HI:.2f}Δ / "
                f"{_REC_DTE_LO}-{_REC_DTE_HI} DTE band (none listed exactly in-band)"
            )
            return plan
    return None


@router.get("/trade-plan/{ticker}/{pattern_name}")
async def trade_plan(
    ticker: str,
    pattern_name: str,
    risk: str = Query("moderate", description="conservative | moderate | aggressive"),
    timeframe: str = Query("day", description="Bar size: week | day | 1h | 15m"),
) -> dict:
    """Entry / stop-loss / target for the most recent occurrence of a pattern.

    Underlying levels: stop sized to ``risk`` tolerance × ATR, target = the
    pattern's measured move. Those levels are then translated into premium
    space for the play's contract (ATM call/put at the breakout, expiry
    suited to the timeframe) — see the ``option`` block in the response.
    """
    ticker = ticker.upper()
    pattern = pattern_name.replace("-", " ")
    if pattern not in PATTERN_DETECTORS:
        raise HTTPException(status_code=400, detail=f"Unknown pattern: {pattern}")

    bullish = pattern in BULLISH_PATTERNS
    cfg = _timeframe_cfg(timeframe)
    # ~180 calendar days of daily bars gives a stable ATR plus a recent signal.
    from_date, to_date = _date_range(min(180, cfg["max_lookback_days"]))
    candles = await fetch_candles(
        ticker, from_date, to_date, timespan=cfg["timespan"], multiplier=cfg["multiplier"]
    )
    if not candles:
        raise HTTPException(status_code=404, detail=f"No data found for {ticker}")
    candles = candles[-_MAX_BARS:]

    loop = asyncio.get_running_loop()
    detections: list[dict] = await loop.run_in_executor(
        _executor, _run_one_detector, (pattern, PATTERN_DETECTORS[pattern], candles)
    )

    current_price = candles[-1]["close"]
    atr = compute_atr(candles)
    vol = annualized_vol(candles)
    base = {
        "ticker": ticker,
        "pattern": pattern,
        "bullish": bullish,
        "current_price": round(current_price, 2),
        "atr": round(atr, 2) if atr else None,
        "atr_pct": round(atr / current_price * 100, 2) if atr and current_price else None,
        "hist_vol_annual_pct": round(vol, 1) if vol else None,
        "timeframe": timeframe,
    }
    if not detections:
        return {**base, "signal_date": None, "plan": None, "option": None}

    latest = max(detections, key=lambda d: d["end_date"])
    plan = build_trade_plan(
        pattern=pattern,
        key_levels=latest.get("key_levels") or {},
        current_price=current_price,
        atr=atr,
        bullish=bullish,
        risk=risk,
    )

    # Actionability: is this latest detection still a trade, or history?
    # Signal age in BARS (approx): calendar days × the timeframe's bar density
    # (5/7 converts calendar → trading days).
    import datetime as _dt

    bars_per_trading_day = {"week": 0.2, "day": 1.0, "1h": 6.5, "15m": 26.0}.get(timeframe, 1.0)
    try:
        signal_day = _dt.date.fromisoformat(latest["end_date"][:10])
        age_bars = max(0.0, (_dt.date.today() - signal_day).days * 5 / 7) * bars_per_trading_day
    except ValueError:
        age_bars = 0.0
    status, status_reason = classify_signal(
        bullish=bullish,
        entry=plan["entry"], stop=plan["stop"], target=plan["target"],
        spot=current_price, atr=atr, age_bars=age_bars,
    )
    plan["status"] = status
    plan["status_reason"] = status_reason

    # The contract recommendation is ALWAYS shown. When the setup is stale (the
    # historical breakout already hit its target or stop), we don't price the
    # play off the dead breakout level — instead we re-anchor to a fresh entry
    # at the CURRENT price and project the pattern's target from there, so the
    # user still gets an actionable contract with sensible TP/SL. The status
    # badge + note make clear the original signal already played out.
    option_plan = plan
    if status == "stale":
        sign = 1.0 if bullish else -1.0
        atr_used = atr if atr and atr > 0 else max(current_price * 0.02, 0.01)
        stop_now = round(current_price - sign * plan["atr_multiple"] * atr_used, 2)
        target_now = plan["target"]
        # If the measured-move target is already behind price, project a fresh
        # 2R continuation target from the current entry.
        if (bullish and target_now <= current_price) or (not bullish and target_now >= current_price):
            target_now = round(current_price + sign * 2.0 * abs(current_price - stop_now), 2)

        def _pct(level: float) -> float:
            return round((level - current_price) / current_price * 100, 2) if current_price else 0.0

        option_plan = {
            **plan,
            "entry": round(current_price, 2),
            "entry_basis": "current price — the original breakout already played out",
            "stop": stop_now,
            "stop_pct": _pct(stop_now),
            "target": target_now,
            "target_pct": _pct(target_now),
            "reanchored": True,
        }

    # Premium-space plan for the play's contract: recommend the best
    # payoff-per-dollar option in the 0.40–0.50 delta, 25–30 DTE band (see
    # _choose_best_contract). Chain snapshot is a sync provider call — keep the
    # event loop free.
    hold_days = _PLAN_HOLD_DAYS.get(timeframe, 10.0)
    candidates = await asyncio.to_thread(_collect_chain_candidates, ticker, bullish)
    option = _choose_best_contract(
        candidates, underlying_plan=option_plan, spot=current_price, hold_days=hold_days
    )

    return {
        **base,
        "signal_date": latest["end_date"],
        "confidence": latest.get("confidence"),
        "plan": option_plan,
        "option": option,
    }


@router.get("/patterns")
async def list_patterns() -> dict:
    """List all supported pattern names and the bullish subset."""
    return {
        "patterns": list(PATTERN_DETECTORS.keys()),
        "bullish": list(BULLISH_PATTERNS),
    }


# ─── Options helpers ───────────────────────────────────────────────────────────

def _strike_round(price: float, bias: float) -> float:
    """Round price * (1 + bias) to nearest $1 strike (or $5 for stocks >= $100)."""
    target = price * (1 + bias)
    increment = 5.0 if price >= 100 else 1.0
    return round(target / increment) * increment


def _options_recommendations(
    pattern: str, bullish: bool, price: float
) -> list[dict]:
    """Return 3 options strategy cards for the given pattern signal."""
    if bullish:
        lc = _strike_round(price, 0.02)
        sb = _strike_round(price, 0.02)
        ss = _strike_round(price, 0.08)
        csp = _strike_round(price, -0.05)
        return [
            {
                "name": "Long Call",
                "grade": "A",
                "structure": f"Buy {lc:.0f}C (30-45 DTE)",
                "rationale": "Captures upside breakout with defined risk. Best when IV rank < 30.",
                "risk_reward": "1 : 3–5",
                "ideal_iv_rank": "< 30",
            },
            {
                "name": "Bull Call Spread",
                "grade": "B+",
                "structure": f"Buy {sb:.0f}C / Sell {ss:.0f}C (30-45 DTE)",
                "rationale": "Reduces premium cost vs. naked call. Caps gains above upper strike.",
                "risk_reward": "1 : 1.5–2.5",
                "ideal_iv_rank": "< 50",
            },
            {
                "name": "Cash-Secured Put",
                "grade": "B",
                "structure": f"Sell {csp:.0f}P (30 DTE)",
                "rationale": "Collect premium if stock stays above support. Profitable in sideways/up moves.",
                "risk_reward": "Premium : Strike",
                "ideal_iv_rank": "> 40",
            },
        ]
    else:
        lp = _strike_round(price, -0.02)
        sb = _strike_round(price, -0.02)
        ss = _strike_round(price, -0.08)
        cc = _strike_round(price, 0.05)
        return [
            {
                "name": "Long Put",
                "grade": "A",
                "structure": f"Buy {lp:.0f}P (30-45 DTE)",
                "rationale": "Captures downside breakdown with defined risk. Best when IV rank < 30.",
                "risk_reward": "1 : 3–5",
                "ideal_iv_rank": "< 30",
            },
            {
                "name": "Bear Put Spread",
                "grade": "B+",
                "structure": f"Buy {sb:.0f}P / Sell {ss:.0f}P (30-45 DTE)",
                "rationale": "Lower cost than naked put. Profits if stock drops to lower strike.",
                "risk_reward": "1 : 1.5–2.5",
                "ideal_iv_rank": "< 50",
            },
            {
                "name": "Covered Call",
                "grade": "B",
                "structure": f"Sell {cc:.0f}C against long shares (30 DTE)",
                "rationale": "Collect premium in a stalling/declining market if you hold shares.",
                "risk_reward": "Premium : Unlimited upside cap",
                "ideal_iv_rank": "> 40",
            },
        ]


# ─── Pattern-scanner options backtest ───────────────────────────────────────
#
# Replays the detectors over history; for every fired signal, simulates buying
# an option (target delta + DTE) and selling it `hold` candles later. Real
# fills come from the listed contract's historical bars (the plan exposes
# intraday option aggregates); BSM is the fast fallback. The optimizer sweeps
# delta x DTE x hold to surface the historically best option + hold.

# Cap total signals priced in one run so a 12-pattern x big-universe sweep
# can't spin for minutes. Most recent signals are kept when truncating.
_MAX_BT_SIGNALS = 600
# Cap the optimizer grid so an accidental huge sweep can't fan out unbounded.
_MAX_BT_CONFIGS = 80


class PatternBacktestRequest(BaseModel):
    """Body for POST /patterns/backtest."""

    tickers: list[str]
    timeframe: str = "1h"
    patterns: list[str] = []          # empty = all detectors
    lookback_days: int | None = None  # None = the timeframe's history window
    mode: str = "single"              # "single" | "optimize"
    # single-run config
    delta: float = 0.4
    dte: int | None = None            # None = timeframe default DTE
    hold: int = 1                     # candles held (1 = next candle close)
    # optimizer sweep axes (used when mode == "optimize"; empty = sane defaults)
    deltas: list[float] = []
    dtes: list[int] = []
    holds: list[int] = []
    # shared knobs
    direction: str = "auto"           # auto | calls | puts
    pricing: str = "real"             # real | bsm
    slippage_pct: float | None = 0.05
    min_confidence: float = 0.0


def _sse(event: str, data: dict) -> str:
    """Format one Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _option_label(ts_ms: int, intraday: bool) -> str:
    """Label an option bar's timestamp on the same grid as the underlying
    candles (ET wall-clock for intraday, date for daily/weekly)."""
    ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    if intraday:
        return ts.astimezone(_ET).strftime("%Y-%m-%dT%H:%M")
    return ts.strftime("%Y-%m-%d")


def _series_from_rows(rows: list[dict], intraday: bool) -> dict[str, float]:
    """Build {candle-label: contract close} from Polygon option aggregates."""
    series: dict[str, float] = {}
    for r in rows:
        t, c = r.get("t"), r.get("c")
        if t is None or c is None:
            continue
        series[_option_label(int(t), intraday)] = float(c)
    return series


def _fetch_option_series(
    client: MassiveClient,
    ticker: str,
    sig,
    sigma: float,
    cfg: TradeConfig,
    option_type: str,
    multiplier: int,
    timespan: str,
    intraday: bool,
):
    """Pick the listed contract closest to (target-delta strike, DTE) as of the
    signal's fire date and return (series, real_strike, contract_ticker), or
    None if nothing usable is listed (caller falls back to BSM)."""
    strike = target_strike(sig, sigma, cfg, option_type)  # type: ignore[arg-type]
    try:
        contract = options_historical.pick_contract(
            client,
            underlying=ticker,
            as_of=date.fromisoformat(sig.fire_date[:10]),
            target_strike=strike,
            target_expiry_days=cfg.dte,
            option_type=option_type,  # type: ignore[arg-type]
        )
    except options_historical.NoSuchContract:
        return None
    except Exception as exc:  # network / provider hiccup — fall back to BSM
        logger.warning("pick_contract failed for %s: %s", ticker, exc)
        return None
    otkr = str(contract["ticker"])
    try:
        aggs = client.get_option_aggregates(
            otkr, sig.fire_date[:10], str(contract["expiration_date"]),
            multiplier=multiplier, timespan=timespan,
        )
    except Exception as exc:
        logger.warning("option aggregates failed for %s: %s", otkr, exc)
        return None
    series = _series_from_rows(aggs.get("results") or [], intraday)
    if not series:
        return None
    return series, float(contract["strike_price"]), otkr


def _price_ticker(
    client: MassiveClient,
    ticker: str,
    candles: list[dict],
    sigma: float,
    signals: list,
    configs: list[TradeConfig],
    direction: str,
    pricing: str,
    slippage_pct: float | None,
    tf: dict,
) -> list[list[Trade]]:
    """Price every (signal x config) for one ticker. Returns one trade list per
    config (parallel to ``configs``). Synchronous — run via asyncio.to_thread.

    Real-fill contract series are cached per (fire_date, delta, dte) so the
    hold-length sweep is free and configs sharing a contract don't refetch.
    """
    multiplier, timespan = tf["multiplier"], tf["timespan"]
    intraday = timespan in ("minute", "hour")
    cache: dict[tuple, object] = {}
    out: list[list[Trade]] = [[] for _ in configs]

    for sig in signals:
        otype = option_type_for(sig, direction)
        for ci, cfg in enumerate(configs):
            trade = None
            if pricing == "real":
                key = (sig.fire_date, cfg.delta, cfg.dte)
                if key not in cache:
                    cache[key] = _fetch_option_series(
                        client, ticker, sig, sigma, cfg, otype,
                        multiplier, timespan, intraday,
                    )
                got = cache[key]
                if got is not None:
                    series, strike, otkr = got  # type: ignore[misc]
                    trade = price_from_series(
                        sig, candles, cfg, otype,
                        series=series, strike=strike, contract=otkr,
                        slippage_pct=slippage_pct,
                    )
            if trade is None:
                trade = price_bsm(
                    sig, candles, sigma, cfg, otype, slippage_pct=slippage_pct,
                )
            if trade is not None:
                out[ci].append(trade)
    return out


def _trade_dict(t: Trade) -> dict:
    return {
        "ticker": t.ticker,
        "pattern": t.pattern,
        "option_type": t.option_type,
        "strike": round(t.strike, 2),
        "open_date": t.open_date,
        "close_date": t.close_date,
        "entry_premium": round(t.entry_premium, 4),
        "exit_premium": round(t.exit_premium, 4),
        "pnl": round(t.pnl, 4),
        "return_pct": round(t.return_pct, 4),
        "confidence": round(t.confidence, 1),
        "synthetic": t.synthetic,
        "contract": t.contract,
    }


@router.post("/backtest")
async def backtest_patterns(req: PatternBacktestRequest, request: Request):
    """Backtest the pattern scanner as an options strategy (SSE).

    Streams ``progress`` per ticker, then a final ``complete`` carrying the
    per-config results (ranked by expectancy) and the trade list of the best
    config. In single mode there's exactly one config.
    """
    tf = _timeframe_cfg(req.timeframe)
    if req.mode not in ("single", "optimize"):
        raise HTTPException(400, "mode must be 'single' or 'optimize'.")
    if req.direction not in ("auto", "calls", "puts"):
        raise HTTPException(400, "direction must be 'auto', 'calls', or 'puts'.")
    if req.pricing not in ("real", "bsm"):
        raise HTTPException(400, "pricing must be 'real' or 'bsm'.")

    tickers = [t.strip().upper() for t in req.tickers if t.strip()]
    if not tickers:
        raise HTTPException(400, "No tickers supplied.")
    sel_patterns = req.patterns or list(PATTERN_DETECTORS.keys())
    unknown = [p for p in sel_patterns if p not in PATTERN_DETECTORS]
    if unknown:
        raise HTTPException(400, f"Unknown patterns: {unknown}")

    default_dte = _PLAN_DTE.get(req.timeframe, 30)
    if req.mode == "single":
        configs = [TradeConfig(delta=req.delta, dte=req.dte or default_dte, hold=max(1, req.hold))]
    else:
        deltas = req.deltas or [0.3, 0.4, 0.5, 0.6]
        dtes = req.dtes or [default_dte]
        holds = req.holds or [1, 2, 3, 5]
        configs = build_grid(deltas, dtes, holds)
        if len(configs) > _MAX_BT_CONFIGS:
            raise HTTPException(
                400,
                f"Grid too large ({len(configs)} combos > {_MAX_BT_CONFIGS}). "
                "Trim the delta / DTE / hold lists.",
            )

    lookback = req.lookback_days or tf["history_days"]
    lookback = min(lookback, tf["max_lookback_days"])
    to_date = date.today().isoformat()
    from_date = (date.today() - timedelta(days=lookback)).isoformat()
    sig_from = (date.today() - timedelta(days=120)).isoformat()

    async def gen():
        client = MassiveClient()
        per_config: list[list[Trade]] = [[] for _ in configs]
        total_signals = 0
        truncated = False
        try:
            yield _sse("progress", {"status": f"Starting — {len(tickers)} tickers, {len(configs)} config(s)"})
            for i, tk in enumerate(tickers):
                if await request.is_disconnected():
                    return
                yield _sse("progress", {"status": f"Scanning {tk} ({i + 1}/{len(tickers)})"})
                try:
                    candles = await fetch_candles(tk, from_date, to_date, tf["timespan"], tf["multiplier"])
                except Exception as exc:
                    yield _sse("progress", {"status": f"{tk}: fetch failed ({exc})"})
                    continue
                if len(candles) > _MAX_BARS:
                    candles = candles[-_MAX_BARS:]
                if len(candles) < 30:
                    continue
                try:
                    daily = await fetch_candles(tk, sig_from, to_date, "day", 1)
                    closes = [c["close"] for c in daily if c.get("close")]
                    sigma = realized_vol(closes, window=30) or 0.30
                except Exception:
                    sigma = 0.30

                signals = replay_signals(
                    tk, candles, PATTERN_DETECTORS, BULLISH_PATTERNS,
                    patterns=sel_patterns, min_confidence=req.min_confidence,
                )
                if not signals:
                    continue
                room = _MAX_BT_SIGNALS - total_signals
                if room <= 0:
                    truncated = True
                    break
                if len(signals) > room:
                    signals = signals[-room:]
                    truncated = True
                total_signals += len(signals)
                yield _sse("progress", {"status": f"{tk}: {len(signals)} signals — pricing options…"})

                results = await asyncio.to_thread(
                    _price_ticker, client, tk, candles, sigma, signals, configs,
                    req.direction, req.pricing, req.slippage_pct, tf,
                )
                for ci, tlist in enumerate(results):
                    per_config[ci].extend(tlist)

            rows = []
            for ci, cfg in enumerate(configs):
                agg = aggregate(per_config[ci])
                rows.append({"delta": cfg.delta, "dte": cfg.dte, "hold": cfg.hold, **agg})
            # Rank by expectancy, then win rate, then sample size.
            rows.sort(key=lambda r: (r["expectancy"], r["win_rate"], r["n_trades"]), reverse=True)

            if req.mode == "single":
                best_trades = [_trade_dict(t) for t in per_config[0]]
            else:
                best_trades = []
                if rows:
                    top = rows[0]
                    for ci, cfg in enumerate(configs):
                        if cfg.delta == top["delta"] and cfg.dte == top["dte"] and cfg.hold == top["hold"]:
                            best_trades = [_trade_dict(t) for t in per_config[ci]]
                            break

            yield _sse("complete", {"data": {
                "mode": req.mode,
                "timeframe": req.timeframe,
                "pricing": req.pricing,
                "direction": req.direction,
                "n_signals": total_signals,
                "truncated": truncated,
                "configs": rows,
                "trades": best_trades,
                "tickers": tickers,
                "patterns": sel_patterns,
                # The actual window replayed (clamped to the timeframe's max).
                "lookback_days": lookback,
                "start_date": from_date,
                "end_date": to_date,
            }})
        except Exception as exc:  # noqa: BLE001 — surface any failure to the client
            logger.exception("pattern backtest failed")
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(gen(), media_type="text/event-stream")
