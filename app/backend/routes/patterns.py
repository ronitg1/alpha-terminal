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

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/patterns", tags=["patterns"])

_executor = ThreadPoolExecutor(max_workers=6)

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
) -> list[dict]:
    """Fetch candles then run all selected detectors concurrently in the thread pool."""
    try:
        candles = await fetch_candles(ticker, from_date, to_date)
        if not candles:
            return []
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

    from_date, to_date = _date_range(req.lookback_days)

    tasks = [
        _scan_ticker(ticker.upper().strip(), from_date, to_date, pattern_names)
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
) -> list[dict]:
    """Scan the built-in 50-ticker large-cap watchlist."""
    pattern_names = (
        [p.strip() for p in patterns.split(",") if p.strip()]
        if patterns
        else list(PATTERN_DETECTORS.keys())
    )
    from_date, to_date = _date_range(lookback_days)

    tasks = [
        _scan_ticker(ticker, from_date, to_date, pattern_names)
        for ticker in DEFAULT_WATCHLIST
    ]
    nested = await asyncio.gather(*tasks)
    results = [item for sublist in nested for item in sublist]
    results.sort(key=lambda x: -x["confidence"])
    return results


@router.get("/chart/{ticker}")
async def chart(
    ticker: str,
    lookback_days: int = Query(365, ge=30, le=730),
) -> dict:
    """Return all OHLCV bars + all detected patterns (with trendlines) for one ticker."""
    from_date, to_date = _date_range(lookback_days)
    ticker = ticker.upper()

    candles = await fetch_candles(ticker, from_date, to_date)
    if not candles:
        raise HTTPException(status_code=404, detail=f"No data found for {ticker}")

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
async def signal_analysis(ticker: str, pattern_name: str) -> dict:
    """
    Run 730-day historical backtest for one ticker+pattern pair.

    Win = max favourable excursion >= 3% over the next 20 bars.
    Recent signals where 20 future bars don't yet exist are excluded from
    the denominator so win-rate is not artificially deflated.
    """
    ticker = ticker.upper()
    pattern_name_decoded = pattern_name.replace("-", " ")

    if pattern_name_decoded not in PATTERN_DETECTORS:
        raise HTTPException(
            status_code=400, detail=f"Unknown pattern: {pattern_name_decoded}"
        )

    bullish = pattern_name_decoded in BULLISH_PATTERNS
    from_date, to_date = _date_range(730)

    candles = await fetch_candles(ticker, from_date, to_date)
    if not candles:
        raise HTTPException(status_code=404, detail=f"No data found for {ticker}")

    loop = asyncio.get_running_loop()
    detections: list[dict] = await loop.run_in_executor(
        _executor,
        _run_one_detector,
        (pattern_name_decoded, PATTERN_DETECTORS[pattern_name_decoded], candles),
    )

    current_price = candles[-1]["close"]
    WIN_THRESHOLD = 0.03
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
