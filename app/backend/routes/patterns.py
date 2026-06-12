"""
Pattern Scanner API routes — mounted at /patterns/*.

Provides OHLCV-backed chart-pattern detection (12 pattern types) with
per-signal historical win-rate analysis and options strategy recommendations.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.backend.routes.pattern_data import fetch_candles
from src.patterns.patterns import BULLISH_PATTERNS, PATTERN_DETECTORS
from src.patterns.trade_plan import (
    annualized_vol,
    build_option_plan,
    build_trade_plan,
    classify_signal,
    compute_atr,
)
from src.tools.massive import MassiveClient, MassiveError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/patterns", tags=["patterns"])

_executor = ThreadPoolExecutor(max_workers=6)

# Timeframe registry. Each entry sets the Polygon aggregate params plus the
# guardrails that keep intraday sane: a lookback clamp (dense bars + the
# O(n²)-ish detectors), the signal-analysis history window, and the win
# threshold for the historical backtest (3% in 20 daily bars is a different
# beast from 3% in 20 fifteen-minute bars — thresholds scale down with the
# bar size so "win" stays meaningful).
_TIMEFRAMES: dict[str, dict] = {
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

@router.post("/scan", response_model=list[ScanResult])
async def scan(req: ScanRequest) -> list[dict]:
    """Scan a custom list of tickers for chart patterns."""
    if not req.tickers:
        raise HTTPException(status_code=400, detail="At least one ticker required.")

    pattern_names = req.patterns if req.patterns else list(PATTERN_DETECTORS.keys())
    unknown = [p for p in pattern_names if p not in PATTERN_DETECTORS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown patterns: {unknown}")

    cfg = _timeframe_cfg(req.timeframe)
    lookback = min(req.lookback_days, cfg["max_lookback_days"])
    from_date, to_date = _date_range(lookback)

    tasks = [
        _scan_ticker(
            ticker.upper().strip(),
            from_date,
            to_date,
            pattern_names,
            timespan=cfg["timespan"],
            multiplier=cfg["multiplier"],
        )
        for ticker in req.tickers
    ]
    nested = await asyncio.gather(*tasks)
    results = [item for sublist in nested for item in sublist]
    results.sort(key=lambda x: -x["confidence"])
    return results


@router.get("/watchlist/scan", response_model=list[ScanResult])
async def watchlist_scan(
    patterns: Optional[str] = Query(None, description="Comma-separated pattern names"),
    lookback_days: int = Query(180, ge=30, le=365),
    timeframe: str = Query("day", description="Bar size: day | 1h | 15m"),
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
    lookback_days: int = Query(365, ge=1, le=730),
    timeframe: str = Query("day", description="Bar size: day | 1h | 15m"),
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
    timeframe: str = Query("day", description="Bar size: day | 1h | 15m"),
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
# hours-to-days, swing patterns in weeks — the option should outlive the move.
_PLAN_DTE: dict[str, int] = {"day": 30, "1h": 14, "15m": 7}

# Expected hold (calendar days) for the theta haircut on the target premium.
# Derived from the 20-bar outcome window each timeframe's win-rate uses:
# 20 daily bars ≈ weeks; 20 hourly bars ≈ 3 trading days; 20×15m ≈ a day.
_PLAN_HOLD_DAYS: dict[str, float] = {"day": 10.0, "1h": 3.0, "15m": 1.0}


def _pick_plan_contract(
    ticker: str, entry: float, bullish: bool, preferred_dte: int
) -> dict | None:
    """Snapshot the chain and pick the play's contract: ATM **at the entry
    level** (not spot — the trade triggers at the breakout), expiry nearest
    the preferred DTE. Returns the fields build_option_plan needs, or None
    when the chain is empty/unavailable (the premium plan is then omitted)."""
    import datetime as _dt

    today = _dt.date.today()
    try:
        client = MassiveClient()
        raw = client.get_options_chain(
            ticker,
            contract_type="call" if bullish else "put",
            expiration_date_gte=(today + _dt.timedelta(days=max(3, preferred_dte - 10))).isoformat(),
            expiration_date_lte=(today + _dt.timedelta(days=preferred_dte + 17)).isoformat(),
            strike_price_gte=entry * 0.92,
            strike_price_lte=entry * 1.08,
            limit=250,
        )
    except MassiveError as exc:
        logger.warning("Chain fetch failed for %s trade plan: %s", ticker, exc)
        return None

    best: dict | None = None
    best_score = float("inf")
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
        # Score: strike distance from ENTRY (normalized) + expiry distance
        # from preferred DTE. Strike proximity dominates.
        score = abs(strike - entry) / max(entry, 1) * 100 + abs(dte - preferred_dte) * 0.2
        if score < best_score:
            best_score = score
            greeks = row.get("greeks") or {}
            best = {
                "ticker": details.get("ticker"),
                "type": details.get("contract_type"),
                "strike": float(strike),
                "expiration": exp,
                "dte": dte,
                "mid": float(mid),
                "iv": row.get("implied_volatility") or greeks.get("iv"),
                "delta": greeks.get("delta"),
            }
    return best


@router.get("/trade-plan/{ticker}/{pattern_name}")
async def trade_plan(
    ticker: str,
    pattern_name: str,
    risk: str = Query("moderate", description="conservative | moderate | aggressive"),
    timeframe: str = Query("day", description="Bar size: day | 1h | 15m"),
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

    bars_per_trading_day = {"day": 1.0, "1h": 6.5, "15m": 26.0}.get(timeframe, 1.0)
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

    if status == "stale":
        # Don't price option plans on dead setups (also saves a chain call).
        return {
            **base,
            "signal_date": latest["end_date"],
            "confidence": latest.get("confidence"),
            "plan": plan,
            "option": None,
        }

    # Premium-space plan for the play's contract. Chain snapshot is a sync
    # provider call — keep the event loop free.
    preferred_dte = _PLAN_DTE.get(timeframe, 30)
    hold_days = _PLAN_HOLD_DAYS.get(timeframe, 10.0)

    async def _plan_for_dte(dte: int) -> dict | None:
        contract = await asyncio.to_thread(
            _pick_plan_contract, ticker, plan["entry"], bullish, dte
        )
        if not contract:
            return None
        return build_option_plan(
            underlying_plan=plan, spot=current_price, contract=contract, hold_days=hold_days
        )

    option = await _plan_for_dte(preferred_dte)
    # Theta-negative on the preferred expiry (the measured move doesn't outrun
    # decay)? A longer-dated contract decays slower per day held — try ~2x DTE
    # and keep whichever is viable / better.
    if option is not None and not option["viable"]:
        longer = await _plan_for_dte(preferred_dte * 2 + 7)
        if longer is not None and longer["viable"]:
            longer["pricing_basis"] += "; expiry extended beyond the preferred window so the move outruns theta"
            option = longer

    return {
        **base,
        "signal_date": latest["end_date"],
        "confidence": latest.get("confidence"),
        "plan": plan,
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
