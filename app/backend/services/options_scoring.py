"""Options screener scoring engine.

Pure-computation core extracted from ``app/backend/routes/sleeves.py`` so the
route layer stays focused on HTTP concerns. Holds the per-strategy bar-based
scorers, the conviction-percentage helpers, the dynamic chart-pattern scorer
factory, and the ``_STRATEGY_REGISTRY`` consumed by both the options screener
and the options backtester. No FastAPI routes live here.
"""
from __future__ import annotations

import datetime
import logging
import math
from typing import Any

from src.tools.massive import MassiveClient, MassiveError, convert_prices

logger = logging.getLogger(__name__)


# Benchmark for the lagging-mega-tech screen. Hard-coded since the screener's
# definition is "lagging QQQ" — changing the benchmark would change the
# strategy, not a config knob.
_BENCHMARK_TICKER = "QQQ"


def _compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """Standard 14-day RSI from a closes list (oldest → newest).

    Returns ``None`` if there aren't enough bars. Uses the simple-average
    seed (Wilder smoothing not applied) — appropriate for a short window
    where the difference is in noise.
    """
    if len(closes) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-diff)
    # Use the trailing ``period`` bars for the average.
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _return_over(closes: list[float], days: int) -> float | None:
    """Pct change over the last ``days`` trading bars. Returns fractional
    (0.05 = +5%). ``None`` if not enough history."""
    if len(closes) <= days:
        return None
    end = closes[-1]
    start = closes[-(days + 1)]
    if start == 0:
        return None
    return (end - start) / start


def _fetch_closes(client: MassiveClient, ticker: str, start: str, end: str) -> list[float]:
    """Daily closes for ``ticker`` between dates. Empty on any failure —
    the screener row will just report ``None`` for affected signals."""
    try:
        aggs = client.get_daily_aggregates(ticker, start, end)
    except MassiveError as exc:
        logger.warning("Massive prices failed for %s: %s", ticker, exc)
        return []
    return [p.close for p in convert_prices(aggs)]


def _fetch_bars(client: MassiveClient, ticker: str, start: str, end: str):
    """Daily OHLCV bars for ``ticker``. Returns ``list[Price]`` — same model
    convert_prices emits. Empty on any failure.

    Returns full bars because the new technical-pattern scorers (breakout,
    volume spike, etc) need volume + high + low in addition to close.
    """
    try:
        aggs = client.get_daily_aggregates(ticker, start, end)
    except MassiveError as exc:
        logger.warning("Massive prices failed for %s: %s", ticker, exc)
        return []
    return convert_prices(aggs)


# ─── Screener strategies ────────────────────────────────────────────────────

# Each scorer returns a generic dict:
#   {
#     "conviction": int (0..3),
#     "signals":    list of {label, value_text, fired, tooltip},
#     "sort_key":   float (lower = ranks earlier within same conviction),
#     "last_price": float | None,
#   }
#
# The frontend renders the chips directly from `signals` so adding a new
# strategy here automatically lights up its chips in the UI — no template
# changes required.


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stddev(xs: list[float], mean: float) -> float:
    if len(xs) < 2:
        return 0.0
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


def _signal(label: str, value_text: str, fired: bool, tooltip: str) -> dict[str, Any]:
    return {"label": label, "value_text": value_text, "fired": fired, "tooltip": tooltip}


def _recommendation(
    *,
    direction: str,
    strike_offset_pct: float = 0.0,
    expiry_lean: str = "near",
    reasoning: str,
) -> dict[str, Any]:
    """Per-strategy contract recommendation packaged for the frontend.

    The chain viewer uses ``direction`` ('call' | 'put') to pick which side
    of the chain to highlight, ``strike_offset_pct`` to find the strike
    closest to ``spot × (1 + offset/100)``, and renders ``reasoning`` as the
    explanation banner. ``expiry_lean`` is a hint ('near' = weeklies, 'mid'
    = 2-4 weeks, 'far' = monthlies+) shown alongside the highlighted row.
    """
    return {
        "direction": direction,
        "strike_offset_pct": strike_offset_pct,
        "expiry_lean": expiry_lean,
        "reasoning": reasoning,
    }


def _fmt_pct(n: float | None) -> str:
    if n is None or not math.isfinite(n):
        return "—"
    sign = "+" if n >= 0 else ""
    return f"{sign}{n * 100:.1f}%"


def _fmt_num(n: float | None, digits: int = 1) -> str:
    if n is None or not math.isfinite(n):
        return "—"
    return f"{n:.{digits}f}"


def _closes(bars: list) -> list[float]:
    return [b.close for b in bars]


def _volumes(bars: list) -> list[int]:
    return [b.volume for b in bars]


def _highs(bars: list) -> list[float]:
    return [b.high for b in bars]


def _lows(bars: list) -> list[float]:
    return [b.low for b in bars]


# ─── Conviction % helpers ────────────────────────────────────────────────────
#
# Magnitude functions map raw signal values to a 1.0–1.5 intensity multiplier.
# _conviction_pct normalises against the theoretical max so 3/3 at extreme
# thresholds ≈ 100 % and 1/3 barely-fired ≈ 26 %.


def _mag_rsi(rsi: float | None, threshold: float) -> float:
    """Higher multiplier when RSI is further past the trigger threshold."""
    if rsi is None:
        return 1.0
    return min(1.5, 1.0 + abs(rsi - threshold) / 20.0)


def _mag_return(pct: float | None, threshold: float) -> float:
    """Higher multiplier when the return/gap is a larger multiple of the threshold."""
    if pct is None or threshold == 0:
        return 1.0
    return min(1.5, 1.0 + (abs(pct) - abs(threshold)) / abs(threshold))


def _mag_volume(ratio: float | None, trigger: float) -> float:
    """Higher multiplier for more extreme volume ratios."""
    if ratio is None or trigger == 0:
        return 1.0
    return min(1.5, 1.0 + (ratio - trigger) / trigger)


def _mag_zscore(z: float | None) -> float:
    """Higher multiplier for more extreme z-scores."""
    if z is None:
        return 1.0
    return min(1.5, 1.0 + (abs(z) - 1.5) / 2.0)


def _conviction_pct(
    signals: list[dict],
    weights: tuple[float, ...],
    magnitudes: list[float],
) -> float:
    """Weighted conviction score 0–100.

    Normalised against the theoretical maximum (all signals at mag 1.5):
      1 signal barely fired ≈ 26 %  |  2 signals ≈ 50 %  |  3 signals ≈ 67 %
      3 signals at max magnitude + consistency bonus ≈ 100 %
    """
    max_possible = sum(w * 1.5 for w in weights)
    if max_possible == 0:
        return 0.0
    fired_score = sum(
        w * max(1.0, m)
        for s, w, m in zip(signals, weights, magnitudes)
        if s["fired"]
    )
    base = (fired_score / max_possible) * 100.0
    if all(s["fired"] for s in signals) and magnitudes and min(magnitudes) >= 1.15:
        base = min(100.0, base + 8.0)
    return round(base, 1)


# Expiry tier table: (strategy, conviction bucket) → 2 recommended tiers.
# "call"/"put" strings in structures for direction-variable strategies are
# substituted by _expiry_tiers() based on the runtime direction.
# Expiry tiers use three canonical DTE values that represent calendar-day cycles:
#   14d = weekly cycle (2 weeks out — avoids 0DTE/near-expiry)
#   35d = monthly cycle (~5 weeks)
#   63d = quarterly cycle (~9 weeks, high conviction only)
# These are fixed calendar-day offsets from today, not "trading days from now."
_EXPIRY_TIERS: dict[str, dict[str, list[dict[str, Any]]]] = {
    "weakness": {
        "low": [
            {"dte": 14, "label": "14d · spread", "structure": "call debit spread",
             "rationale": "Defined-risk bounce — sell higher strike to offset premium cost."},
            {"dte": 35, "label": "35d · ATM", "structure": "ATM call",
             "rationale": "Monthly cycle gives room if the QQQ rotation takes time to materialize."},
        ],
        "med": [
            {"dte": 35, "label": "35d · ATM", "structure": "ATM call",
             "rationale": "Mean-reversion lean — oversold names snap back within a monthly cycle."},
            {"dte": 14, "label": "14d · spread", "structure": "call debit spread",
             "rationale": "Cheaper weekly entry; sell 2–3% higher strike to offset cost."},
        ],
        "high": [
            {"dte": 63, "label": "63d · conviction", "structure": "long call",
             "rationale": "Oversold + lagging QQQ — trend repair can run far; quarterly gives room."},
            {"dte": 35, "label": "35d · tactical", "structure": "ATM call",
             "rationale": "Monthly cycle for quicker theta resolution."},
        ],
    },
    "strength": {
        "low": [
            {"dte": 14, "label": "14d · spread", "structure": "put debit spread",
             "rationale": "Defined-risk fade — buy ATM put, sell lower strike."},
            {"dte": 35, "label": "35d · ATM", "structure": "ATM put",
             "rationale": "Monthly cycle gives more time for the mean reversion to develop."},
        ],
        "med": [
            {"dte": 35, "label": "35d · ATM", "structure": "ATM put",
             "rationale": "Overbought fade — leading names stall before snapping back; monthly cycle."},
            {"dte": 14, "label": "14d · spread", "structure": "put debit spread",
             "rationale": "Weekly spread reduces premium on high-IV names."},
        ],
        "high": [
            {"dte": 63, "label": "63d · conviction", "structure": "long put",
             "rationale": "Leading + deeply overbought — reversion can be sharp; quarterly room."},
            {"dte": 35, "label": "35d · tactical", "structure": "ATM put",
             "rationale": "Monthly cycle if RSI is past 70 and you want faster resolution."},
        ],
    },
    "momentum": {
        "low": [
            {"dte": 35, "label": "35d · spread", "structure": "call debit spread",
             "rationale": "Defined risk on continuation — cap upside to reduce premium."},
            {"dte": 35, "label": "35d · ATM", "structure": "ATM call",
             "rationale": "Monthly cycle gives room if the trend needs a few weeks to extend."},
        ],
        "med": [
            {"dte": 35, "label": "35d · OTM", "structure": "2% OTM call",
             "rationale": "Momentum payoff window is 3–5 weeks; slight OTM for leverage."},
            {"dte": 35, "label": "35d · spread", "structure": "call debit spread",
             "rationale": "Spread if IV is elevated on the name."},
        ],
        "high": [
            {"dte": 63, "label": "63d · position", "structure": "long call",
             "rationale": "Strong absolute trend — quarterly cycle gives the move room to compound."},
            {"dte": 35, "label": "35d · OTM", "structure": "2% OTM call",
             "rationale": "Monthly cycle if you prefer a 5-week catalyst horizon."},
        ],
    },
    "mean_reversion": {
        "low": [
            {"dte": 14, "label": "14d · spread", "structure": "call debit spread",
             "rationale": "Snap-back with defined risk — thesis resolves in ≤2 weeks."},
            {"dte": 35, "label": "35d · fallback", "structure": "ATM call",
             "rationale": "Monthly cycle buffer if consolidation extends beyond the initial snap."},
        ],
        "med": [
            {"dte": 14, "label": "14d · ATM", "structure": "ATM call",
             "rationale": "Snap-backs happen fast — ATM for max gamma on the first move."},
            {"dte": 35, "label": "35d · safety", "structure": "ATM call",
             "rationale": "Monthly cycle buffer if price needs time to turn."},
        ],
        "high": [
            {"dte": 35, "label": "35d · long", "structure": "long call",
             "rationale": "Extreme z-score + RSI extreme — reversion can be violent; monthly cycle."},
            {"dte": 14, "label": "14d · ATM", "structure": "ATM call",
             "rationale": "Fast gamma play on the initial snap when z > 2.5."},
        ],
    },
    "breakout": {
        "low": [
            {"dte": 35, "label": "35d · spread", "structure": "call debit spread",
             "rationale": "Defined risk on the breakout — cap cost if move stalls."},
            {"dte": 35, "label": "35d · ATM", "structure": "ATM call",
             "rationale": "Monthly cycle gives time for the 52w-high breakout to extend."},
        ],
        "med": [
            {"dte": 35, "label": "35d · OTM", "structure": "2% OTM call",
             "rationale": "52w-high breakout on volume — monthly cycle for momentum continuation."},
            {"dte": 35, "label": "35d · spread", "structure": "call debit spread",
             "rationale": "Spread to reduce cost; sell strike at prior resistance."},
        ],
        "high": [
            {"dte": 63, "label": "63d · conviction", "structure": "long call",
             "rationale": "Volume breakout above a year-high — quarterly cycle to ride the extension."},
            {"dte": 35, "label": "35d · OTM", "structure": "2% OTM call",
             "rationale": "Monthly leveraged play if you expect near-term acceleration."},
        ],
    },
    "breakdown": {
        "low": [
            {"dte": 35, "label": "35d · spread", "structure": "put debit spread",
             "rationale": "Defined risk on the break — cap cost if a bounce materializes."},
            {"dte": 35, "label": "35d · ATM", "structure": "ATM put",
             "rationale": "Monthly cycle gives time to outlast a dead-cat bounce."},
        ],
        "med": [
            {"dte": 35, "label": "35d · OTM", "structure": "2% OTM put",
             "rationale": "Breakdown below 52w low on volume — monthly cycle for continuation."},
            {"dte": 35, "label": "35d · spread", "structure": "put debit spread",
             "rationale": "Spread if the name has wide bid-ask on outright puts."},
        ],
        "high": [
            {"dte": 63, "label": "63d · conviction", "structure": "long put",
             "rationale": "High-conviction break — oversold can stay oversold; quarterly cycle."},
            {"dte": 35, "label": "35d · OTM", "structure": "2% OTM put",
             "rationale": "Monthly play if you expect a fast continuation flush."},
        ],
    },
    "volume_spike": {
        "low": [
            {"dte": 14, "label": "14d · fast", "structure": "ATM call",
             "rationale": "Unusual flow — follow it into the next 1–2 weekly cycles."},
            {"dte": 14, "label": "14d · spread", "structure": "call debit spread",
             "rationale": "Defined risk if direction persistence is uncertain."},
        ],
        "med": [
            {"dte": 14, "label": "14d · ATM", "structure": "ATM call",
             "rationale": "Flow confirmation — give the move 1–2 weeks to develop."},
            {"dte": 35, "label": "35d · follow", "structure": "ATM call",
             "rationale": "Monthly cycle if close-in-range is extreme (>90%) and you want more time."},
        ],
        "high": [
            {"dte": 35, "label": "35d · long", "structure": "long call",
             "rationale": "Extreme volume + close-at-wick — institutional conviction; monthly cycle."},
            {"dte": 14, "label": "14d · ATM", "structure": "ATM call",
             "rationale": "Weekly exit if the flow was a single-day event."},
        ],
    },
    "pullback": {
        "low": [
            {"dte": 35, "label": "35d · spread", "structure": "call debit spread",
             "rationale": "Buy-the-dip with defined risk — MA bounces can take 2–4 weeks."},
            {"dte": 35, "label": "35d · ATM", "structure": "ATM call",
             "rationale": "Monthly cycle gives more time if consolidation at the MA extends."},
        ],
        "med": [
            {"dte": 35, "label": "35d · ATM", "structure": "ATM call",
             "rationale": "Uptrend intact — 20/50d MA bounce is a high-probability setup; monthly."},
            {"dte": 35, "label": "35d · spread", "structure": "call debit spread",
             "rationale": "Spread to reduce cost; sell strike above recent resistance."},
        ],
        "high": [
            {"dte": 63, "label": "63d · diagonal", "structure": "diagonal call spread",
             "rationale": "Strong pullback signal — sell near-dated call to offset the quarterly leg."},
            {"dte": 35, "label": "35d · ATM", "structure": "ATM call",
             "rationale": "Monthly outright if you prefer simple directional exposure."},
        ],
    },
    "trend_bias": {
        "low": [
            {"dte": 63, "label": "63d · spread", "structure": "call debit spread",
             "rationale": "Strategic directional bet — defined risk on the MA cross thesis."},
            {"dte": 63, "label": "63d · ATM", "structure": "ATM call",
             "rationale": "Quarterly cycle gives time for the structural trend to develop fully."},
        ],
        "med": [
            {"dte": 63, "label": "63d · ATM", "structure": "ATM call",
             "rationale": "MA cross regime — quarterly cycle gives room to compound over 2 months."},
            {"dte": 63, "label": "63d · spread", "structure": "call debit spread",
             "rationale": "Reduce premium on the structural bet."},
        ],
        "high": [
            {"dte": 63, "label": "63d · LEAPS", "structure": "LEAPS call",
             "rationale": "Accelerating cross + price on trend side — quarterly conviction position."},
            {"dte": 63, "label": "63d · ATM", "structure": "ATM call",
             "rationale": "Outright at quarterly if you prefer a defined exit window."},
        ],
    },
    "vol_expansion": {
        "low": [
            {"dte": 14, "label": "14d · fast", "structure": "ATM call",
             "rationale": "Vol regime blowouts are short-lived — capitalize before IV mean-reverts."},
            {"dte": 14, "label": "14d · spread", "structure": "call debit spread",
             "rationale": "Defined risk in case the directional call is wrong."},
        ],
        "med": [
            {"dte": 14, "label": "14d · ATM", "structure": "ATM call",
             "rationale": "Vol expansion + big move — follow the regime shift, weekly cycle."},
            {"dte": 35, "label": "35d · ATM", "structure": "ATM call",
             "rationale": "Monthly cycle if realized vol is blowing out and may sustain."},
        ],
        "high": [
            {"dte": 35, "label": "35d · long", "structure": "long call",
             "rationale": "Extreme vol expansion + large catalyst — monthly cycle for regime change."},
            {"dte": 14, "label": "14d · ATM", "structure": "ATM call",
             "rationale": "Weekly cycle if you expect vol to mean-revert within 2 weeks."},
        ],
    },
    "unusual_options_activity": {
        "low": [
            {"dte": 35, "label": "35d · follow", "structure": "ATM call",
             "rationale": "Follow institutional flow; monthly cycle matches typical sweep positioning."},
            {"dte": 14, "label": "14d · fast", "structure": "ATM call",
             "rationale": "Weekly cycle if OTM strikes suggest a near-term catalyst."},
        ],
        "med": [
            {"dte": 35, "label": "35d · position", "structure": "ATM call",
             "rationale": "Institutional bets typically have a monthly thesis — match their cycle."},
            {"dte": 35, "label": "35d · follow", "structure": "ATM call",
             "rationale": "Monthly cycle for single-day sweep activity."},
        ],
        "high": [
            {"dte": 63, "label": "63d · conviction", "structure": "long call",
             "rationale": "Heavy vol/OI + OTM concentration — high-conviction smart-money; quarterly."},
            {"dte": 35, "label": "35d · position", "structure": "ATM call",
             "rationale": "Monthly institutional position horizon."},
        ],
    },
}


# Spread width between the two strikes of a vertical/diagonal, as a percent of
# spot. The short leg is sold this far OTM of the long (ATM) leg.
_SPREAD_WIDTH_PCT = 5.0


def _structure_to_legs(structure: str) -> list[dict[str, Any]]:
    """Map a tier ``structure`` string to its option legs for chain highlighting.

    Each leg is ``{side, direction, strike_offset_pct}`` where:
      - ``side``               : 'long' (bought) or 'short' (sold)
      - ``direction``          : 'call' | 'put'
      - ``strike_offset_pct``  : 0 = ATM, positive = strike above spot,
                                 negative = strike below spot. This matches the
                                 ScreenerRecommendation offset convention so the
                                 chain viewer resolves every leg with the same
                                 ``spot * (1 + offset/100)`` math.

    Single-leg structures (ATM/long/LEAPS/2% OTM) return one leg; debit verticals
    and diagonals return two so both strikes light up in the chain.
    """
    s = structure.lower()
    is_put = "put" in s
    direction = "put" if is_put else "call"
    otm = -1.0 if is_put else 1.0  # OTM is below spot for puts, above for calls

    if "debit spread" in s or "diagonal" in s:
        # Buy ATM, sell one spread-width further OTM (cap upside, cut cost).
        return [
            {"side": "long", "direction": direction, "strike_offset_pct": 0.0},
            {"side": "short", "direction": direction, "strike_offset_pct": otm * _SPREAD_WIDTH_PCT},
        ]
    if "2% otm" in s:
        return [{"side": "long", "direction": direction, "strike_offset_pct": otm * 2.0}]
    # ATM call/put, long call/put, LEAPS call/put → single ATM long leg.
    return [{"side": "long", "direction": direction, "strike_offset_pct": 0.0}]


def _expiry_tiers(
    strategy: str,
    conviction_pct: float,
    direction: str,
) -> list[dict[str, Any]]:
    """Return 2 expiry tier recs for a (strategy, conviction %, direction) triple.

    Returns an empty list when conviction_pct < 40 (no credible play).
    For put-direction strategies the call structure names are substituted.
    Each tier carries a ``legs`` list so the chain viewer can highlight every
    strike of a multi-leg structure (e.g. both legs of a debit spread).
    """
    if conviction_pct < 40:
        return []
    bucket = "high" if conviction_pct >= 80 else ("med" if conviction_pct >= 60 else "low")
    raw = _EXPIRY_TIERS.get(strategy, {}).get(bucket, [])
    result = []
    for tier in raw:
        t = tier.copy()
        if direction == "put":
            t["structure"] = (
                t["structure"]
                .replace("call debit spread", "put debit spread")
                .replace("2% OTM call", "2% OTM put")
                .replace("LEAPS call", "LEAPS put")
                .replace("diagonal call spread", "diagonal put spread")
                .replace("ATM call", "ATM put")
                .replace("long call", "long put")
            )
        t["legs"] = _structure_to_legs(t["structure"])
        result.append(t)
    return result


# ─── Scorers (bars-based) ────────────────────────────────────────────────────
#
# All scorers take ``(bars, qqq_bars, **kwargs)`` so the dispatcher can pass
# optional context (ticker, client) for strategies that need to fetch the
# options chain inline. Most scorers ignore the kwargs.


def _score_weakness(bars: list, qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Lagging QQQ + oversold. Bounce-trade calls or continuation puts."""
    closes = _closes(bars)
    qqq_closes = _closes(qqq_bars)
    last_price = closes[-1] if closes else None
    r20 = _return_over(closes, 20)
    r5 = _return_over(closes, 5)
    rsi = _compute_rsi(closes, 14)
    q20 = _return_over(qqq_closes, 20)
    q5 = _return_over(qqq_closes, 5)
    d20 = (r20 - q20) if (r20 is not None and q20 is not None) else None
    d5 = (r5 - q5) if (r5 is not None and q5 is not None) else None

    # Signal 4: price vs 20d SMA
    sma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
    below_sma20 = (last_price is not None and sma20 is not None and last_price < sma20 * 0.98)
    sma20_dist_mag = (sma20 / last_price) if (below_sma20 and last_price and last_price > 0) else 1.0

    # Signal 5: 5-day rate of change
    roc5 = _return_over(closes, 5)  # same as r5 — negative = falling
    roc5_fired = roc5 is not None and roc5 < -0.02

    signals = [
        _signal(
            "20d vs QQQ",
            _fmt_pct(d20),
            d20 is not None and d20 < -0.02,
            f"Ticker {_fmt_pct(r20)} vs QQQ {_fmt_pct(q20)} over 20 trading days. Fires when gap is worse than −2%.",
        ),
        _signal(
            "5d vs QQQ",
            _fmt_pct(d5),
            d5 is not None and d5 < -0.01,
            f"Ticker {_fmt_pct(r5)} vs QQQ {_fmt_pct(q5)} over 5 trading days. Fires when gap is worse than −1%.",
        ),
        _signal(
            "RSI",
            _fmt_num(rsi, 0),
            rsi is not None and rsi < 45,
            f"Relative Strength Index (14d). Fires when below 45 (approaching oversold). Current: {_fmt_num(rsi, 1)}.",
        ),
        _signal(
            "vs 20d SMA",
            _fmt_pct((last_price - sma20) / sma20 if (last_price and sma20) else None),
            below_sma20,
            "Price below 20d SMA confirms weak trend; deeper = stronger setup.",
        ),
        _signal(
            "5d ROC",
            _fmt_pct(roc5),
            roc5_fired,
            "5-day momentum negative; recent selling pressure.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _m = [
        _mag_return(d20, -0.02) if (d20 is not None and d20 < -0.02) else 1.0,
        _mag_return(d5, -0.01) if (d5 is not None and d5 < -0.01) else 1.0,
        _mag_rsi(rsi, 45.0) if (rsi is not None and rsi < 45) else 1.0,
        min(1.5, sma20_dist_mag) if below_sma20 else 1.0,
        min(1.5, 1.0 + abs(roc5 or 0) / 0.02) if roc5_fired else 1.0,
    ]
    c_pct = _conviction_pct(signals, (26.25, 18.75, 30.0, 15.0, 10.0), _m)
    sort_key = d20 if d20 is not None else 0.0
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("weakness", c_pct, "call"),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction="call",
            strike_offset_pct=0.0,
            expiry_lean="near",
            reasoning=(
                "Oversold name lagging QQQ — bounce trade. ATM call captures the "
                "snap-back with ~0.5 delta exposure. Near-term weekly keeps theta "
                "manageable; if the bounce hasn't started in 1–2 weeks, the thesis is wrong."
            ),
        ),
    }


def _score_strength(bars: list, qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Leading QQQ + overbought. Breakout calls or mean-reversion puts."""
    closes = _closes(bars)
    highs = _highs(bars)
    qqq_closes = _closes(qqq_bars)
    last_price = closes[-1] if closes else None
    r20 = _return_over(closes, 20)
    r5 = _return_over(closes, 5)
    rsi = _compute_rsi(closes, 14)
    q20 = _return_over(qqq_closes, 20)
    q5 = _return_over(qqq_closes, 5)
    d20 = (r20 - q20) if (r20 is not None and q20 is not None) else None
    d5 = (r5 - q5) if (r5 is not None and q5 is not None) else None

    # Signal 4: price vs 20d SMA (overbought fade)
    sma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
    above_sma20 = (last_price is not None and sma20 is not None and last_price > sma20 * 1.02)

    # Signal 5: distance from 52-week high
    yr_highs = highs[-252:] if len(highs) >= 252 else highs
    high_52w = max(yr_highs) if yr_highs else None
    near_52w_high = (
        last_price is not None and high_52w is not None
        and high_52w > 0
        and (high_52w - last_price) / high_52w <= 0.03
    )

    signals = [
        _signal(
            "20d vs QQQ",
            _fmt_pct(d20),
            d20 is not None and d20 > 0.02,
            f"Ticker {_fmt_pct(r20)} vs QQQ {_fmt_pct(q20)} over 20 trading days. Fires when gap is better than +2%.",
        ),
        _signal(
            "5d vs QQQ",
            _fmt_pct(d5),
            d5 is not None and d5 > 0.01,
            f"Ticker {_fmt_pct(r5)} vs QQQ {_fmt_pct(q5)} over 5 trading days. Fires when gap is better than +1%.",
        ),
        _signal(
            "RSI",
            _fmt_num(rsi, 0),
            rsi is not None and rsi > 55,
            f"Relative Strength Index (14d). Fires when above 55 (approaching overbought). Current: {_fmt_num(rsi, 1)}.",
        ),
        _signal(
            "vs 20d SMA",
            _fmt_pct((last_price - sma20) / sma20 if (last_price and sma20) else None),
            above_sma20,
            "Price above 20d SMA by >2% — extended above short-term trend; fade risk rises.",
        ),
        _signal(
            "52w high",
            _fmt_pct((last_price - high_52w) / high_52w if (last_price and high_52w) else None),
            near_52w_high,
            "Near 52-week high increases fade risk.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _m = [
        _mag_return(d20, 0.02) if (d20 is not None and d20 > 0.02) else 1.0,
        _mag_return(d5, 0.01) if (d5 is not None and d5 > 0.01) else 1.0,
        _mag_rsi(rsi, 55.0) if (rsi is not None and rsi > 55) else 1.0,
        1.2 if above_sma20 else 1.0,
        1.2 if near_52w_high else 1.0,
    ]
    c_pct = _conviction_pct(signals, (26.25, 18.75, 30.0, 15.0, 10.0), _m)
    sort_key = -d20 if d20 is not None else 0.0
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("strength", c_pct, "put"),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction="put",
            strike_offset_pct=0.0,
            expiry_lean="near",
            reasoning=(
                "Overbought name leading QQQ — mean-reversion fade. ATM put "
                "captures the pullback. Use a near-term weekly so you're not "
                "fighting theta if the trend keeps running."
            ),
        ),
    }


def _score_momentum(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Pure absolute trend follow. No benchmark — just up-and-to-the-right."""
    closes = _closes(bars)
    vols = _volumes(bars)
    last_price = closes[-1] if closes else None
    r20 = _return_over(closes, 20)
    r5 = _return_over(closes, 5)
    rsi = _compute_rsi(closes, 14)

    # Signal 4: 20d SMA slope (trend acceleration)
    sma20_now = _mean(closes[-20:]) if len(closes) >= 20 else None
    sma20_10d_ago = _mean(closes[-30:-10]) if len(closes) >= 30 else None
    sma20_slope: float | None = None
    if sma20_now is not None and sma20_10d_ago is not None and sma20_10d_ago > 0:
        sma20_slope = (sma20_now - sma20_10d_ago) / sma20_10d_ago * 100.0
    sma20_slope_fired = sma20_slope is not None and sma20_slope > 0.5

    # Signal 5: volume trend (avg 5d vs avg 20d)
    avg_vol_5d = _mean([float(v) for v in vols[-5:]]) if len(vols) >= 5 else None
    avg_vol_20d = _mean([float(v) for v in vols[-20:]]) if len(vols) >= 20 else None
    vol_trend_fired = (
        avg_vol_5d is not None and avg_vol_20d is not None
        and avg_vol_20d > 0 and avg_vol_5d > avg_vol_20d * 1.1
    )

    signals = [
        _signal(
            "20d return",
            _fmt_pct(r20),
            r20 is not None and r20 > 0.05,
            f"Absolute 20-day return. Fires when > +5%. Current: {_fmt_pct(r20)}.",
        ),
        _signal(
            "5d return",
            _fmt_pct(r5),
            r5 is not None and r5 > 0.02,
            f"Absolute 5-day return. Fires when > +2%. Current: {_fmt_pct(r5)}.",
        ),
        _signal(
            "RSI",
            _fmt_num(rsi, 0),
            rsi is not None and rsi > 60,
            f"RSI (14d). Fires when > 60 (strong momentum, not yet exhausted). Current: {_fmt_num(rsi, 1)}.",
        ),
        _signal(
            "SMA slope",
            f"{_fmt_num(sma20_slope, 2)}%" if sma20_slope is not None else "—",
            sma20_slope_fired,
            "20d SMA slope over last 10 days. Fires when > 0.5% — trend is accelerating, not flattening.",
        ),
        _signal(
            "volume trend",
            f"{_fmt_num((avg_vol_5d or 0) / (avg_vol_20d or 1), 2)}×" if avg_vol_20d else "—",
            vol_trend_fired,
            "Avg volume last 5d vs avg volume last 20d. Fires when 5d avg > 20d avg × 1.1 — rising participation.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _m = [
        _mag_return(r20, 0.05) if (r20 is not None and r20 > 0.05) else 1.0,
        _mag_return(r5, 0.02) if (r5 is not None and r5 > 0.02) else 1.0,
        _mag_rsi(rsi, 60.0) if (rsi is not None and rsi > 60) else 1.0,
        min(1.5, 1.0 + (sma20_slope or 0) / 0.5 * 0.5) if sma20_slope_fired else 1.0,
        min(1.5, 1.0 + ((avg_vol_5d or 0) / (avg_vol_20d or 1) - 1.1) / 0.5) if vol_trend_fired else 1.0,
    ]
    c_pct = _conviction_pct(signals, (30.0, 22.5, 22.5, 15.0, 10.0), _m)
    sort_key = -r20 if r20 is not None else 0.0
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("momentum", c_pct, "call"),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction="call",
            strike_offset_pct=2.0,
            expiry_lean="mid",
            reasoning=(
                "Trending up with momentum — ride the move. 2% OTM call gives "
                "leverage on continuation while keeping premium reasonable. Use a "
                "2–4 week expiry so theta isn't punishing if the move stalls a few days."
            ),
        ),
    }


def _score_mean_reversion(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Stretched far from 20-day average. Bet on a snap-back in either direction."""
    closes = _closes(bars)
    vols = _volumes(bars)
    last_price = closes[-1] if closes else None
    rsi = _compute_rsi(closes, 14)

    z = None
    pct_from_ma = None
    if len(closes) >= 20:
        window = closes[-20:]
        mean = _mean(window)
        sd = _stddev(window, mean)
        if sd > 0 and last_price is not None:
            z = (last_price - mean) / sd
            pct_from_ma = (last_price - mean) / mean

    r5 = _return_over(closes, 5)

    # Signal 4: 5d ROC in reversion direction (confirms the stretch is real)
    # For above-mean (z>0) reversion: we want negative recent ROC (already falling back)
    # For below-mean (z<0) reversion: we want positive recent ROC (already bouncing)
    roc5_rev_fired = False
    if r5 is not None and z is not None:
        if z > 0 and r5 < -0.03:
            roc5_rev_fired = True  # above mean, already reversing down
        elif z < 0 and r5 > 0.03:
            roc5_rev_fired = True  # below mean, already bouncing up

    # Signal 5: volume contraction during the stretch (exhaustion, not distribution)
    avg_vol_5d = _mean([float(v) for v in vols[-5:]]) if len(vols) >= 5 else None
    avg_vol_20d = _mean([float(v) for v in vols[-20:]]) if len(vols) >= 20 else None
    vol_contraction_fired = (
        avg_vol_5d is not None and avg_vol_20d is not None
        and avg_vol_20d > 0 and avg_vol_5d < avg_vol_20d * 0.8
    )

    signals = [
        _signal(
            "Z-score (20d)",
            _fmt_num(z, 2),
            z is not None and abs(z) > 1.5,
            f"How many 20-day standard deviations the price is from its 20-day mean. Fires when |z| > 1.5 (statistically stretched). "
            f"Current: z={_fmt_num(z, 2)} ({_fmt_pct(pct_from_ma)} from 20d MA).",
        ),
        _signal(
            "5d move",
            _fmt_pct(r5),
            r5 is not None and abs(r5) > 0.05,
            f"Absolute 5-day return. Fires when |move| > 5% (large recent swing). Current: {_fmt_pct(r5)}.",
        ),
        _signal(
            "RSI extreme",
            _fmt_num(rsi, 0),
            rsi is not None and (rsi > 70 or rsi < 30),
            f"RSI (14d). Fires when > 70 (overbought, snap-back via puts) or < 30 (oversold, snap-back via calls). Current: {_fmt_num(rsi, 1)}.",
        ),
        _signal(
            "5d ROC",
            _fmt_pct(r5),
            roc5_rev_fired,
            "5-day rate of change in the reversion direction. Fires when |5d ROC| > 3% — confirms the stretch is already unwinding.",
        ),
        _signal(
            "vol contraction",
            f"{_fmt_num((avg_vol_5d or 0) / (avg_vol_20d or 1), 2)}×" if avg_vol_20d else "—",
            vol_contraction_fired,
            "Low volume during pullback suggests exhaustion, not distribution.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _m = [
        _mag_zscore(z) if (z is not None and abs(z) > 1.5) else 1.0,
        _mag_return(r5, 0.05) if (r5 is not None and abs(r5) > 0.05) else 1.0,
        _mag_rsi(rsi, 70.0 if (z or 0) > 0 else 30.0) if (rsi is not None and (rsi > 70 or rsi < 30)) else 1.0,
        min(1.5, 1.0 + abs(r5 or 0) / 0.03) if roc5_rev_fired else 1.0,
        1.2 if vol_contraction_fired else 1.0,
    ]
    c_pct = _conviction_pct(signals, (33.75, 18.75, 22.5, 15.0, 10.0), _m)
    sort_key = -abs(z) if z is not None else 0.0
    # Direction inverts based on which side of the mean the price has stretched
    # to: above-mean → expect pullback (puts), below-mean → expect bounce (calls).
    if z is not None and z > 0:
        direction = "put"
        why = "Price stretched above the 20-day mean — fade with a put expecting reversion."
    else:
        direction = "call"
        why = "Price stretched below the 20-day mean — bounce trade with a call."
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("mean_reversion", c_pct, direction),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction=direction,
            strike_offset_pct=0.0,
            expiry_lean="near",
            reasoning=(
                f"{why} ATM strike for ~0.5 delta — symmetric exposure to the "
                "snap-back. Near-term weekly: if reversion doesn't start fast, the setup decays."
            ),
        ),
    }


# ─── New: technical-pattern scorers ─────────────────────────────────────────


def _score_breakout(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Near 52-week high + volume surge + RSI momentum. Bullish continuation
    setup — calls on the breakout. Reliable in trending markets, less so in chop."""
    closes = _closes(bars)
    highs = _highs(bars)
    lows = _lows(bars)
    vols = _volumes(bars)
    last_price = closes[-1] if closes else None
    rsi = _compute_rsi(closes, 14)

    # 52-week high (up to ~252 trading days)
    yr = highs[-252:] if len(highs) >= 252 else highs
    high_52w = max(yr) if yr else None
    pct_to_high = ((last_price - high_52w) / high_52w) if (last_price and high_52w) else None
    # negative = below high; near-high means pct_to_high in [-0.05, 0]
    near_high = pct_to_high is not None and -0.05 <= pct_to_high <= 0.005

    # Volume surge: today vs trailing 20d average (excluding today)
    vol_ratio = None
    if len(vols) >= 21:
        baseline = sum(vols[-21:-1]) / 20
        if baseline > 0:
            vol_ratio = vols[-1] / baseline

    # Signal 4: price above 20d SMA (breakout above short-term trend)
    sma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
    above_sma20 = last_price is not None and sma20 is not None and last_price > sma20

    # Signal 5: range expansion — today's H-L range vs avg true range last 10d
    atr_10d: float | None = None
    if len(highs) >= 11 and len(lows) >= 11 and len(closes) >= 11:
        true_ranges = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(-10, 0)
        ]
        atr_10d = _mean(true_ranges)
    today_range = (highs[-1] - lows[-1]) if (highs and lows) else None
    range_expansion_fired = (
        today_range is not None and atr_10d is not None
        and atr_10d > 0 and today_range > atr_10d * 1.3
    )

    signals = [
        _signal(
            "near 52w high",
            _fmt_pct(pct_to_high),
            near_high,
            f"Distance from the trailing 52-week high. Fires when within 5% of the high (and not already above by >0.5%). "
            f"52w high: {_fmt_num(high_52w, 2)}, current: {_fmt_num(last_price, 2)}.",
        ),
        _signal(
            "volume surge",
            f"{_fmt_num(vol_ratio, 2)}×" if vol_ratio is not None else "—",
            vol_ratio is not None and vol_ratio > 1.5,
            f"Today's volume vs trailing 20-day average. Fires when > 1.5× (participation confirms the move). "
            f"Current: {_fmt_num(vol_ratio, 2)}×.",
        ),
        _signal(
            "RSI",
            _fmt_num(rsi, 0),
            rsi is not None and rsi > 60,
            f"RSI (14d). Fires when > 60 — momentum behind the breakout, not yet overbought. Current: {_fmt_num(rsi, 1)}.",
        ),
        _signal(
            "above 20d SMA",
            "yes" if above_sma20 else "no",
            above_sma20,
            "Price above 20d SMA — breakout has reclaimed short-term trend support.",
        ),
        _signal(
            "range expansion",
            _fmt_pct((today_range / atr_10d - 1.0) if (today_range and atr_10d) else None),
            range_expansion_fired,
            "Today's H-L range > 10d avg true range × 1.3 — expanded range confirms breakout conviction.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _near_high_mag = max(1.0, 1.5 - abs(pct_to_high or -0.05) / 0.1) if near_high else 1.0
    _m = [
        _near_high_mag,
        _mag_volume(vol_ratio, 1.5) if (vol_ratio is not None and vol_ratio > 1.5) else 1.0,
        _mag_rsi(rsi, 60.0) if (rsi is not None and rsi > 60) else 1.0,
        1.2 if above_sma20 else 1.0,
        min(1.5, 1.0 + (today_range / atr_10d - 1.3) / 0.5) if range_expansion_fired else 1.0,
    ]
    c_pct = _conviction_pct(signals, (30.0, 26.25, 18.75, 15.0, 10.0), _m)
    sort_key = -pct_to_high if pct_to_high is not None else 0.0
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("breakout", c_pct, "call"),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction="call",
            strike_offset_pct=2.0,
            expiry_lean="mid",
            reasoning=(
                "Pressing the 52-week high on volume — momentum breakout. "
                "2% OTM call above the breakout level: cheap leverage that pays "
                "if the breakout sticks. 2–4 week expiry gives the trend room "
                "without bleeding theta on a failed breakout."
            ),
        ),
    }


def _score_breakdown(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Near 52-week low + volume surge + downside momentum. Bearish mirror
    of Breakout — puts on the continuation."""
    closes = _closes(bars)
    highs = _highs(bars)
    lows = _lows(bars)
    vols = _volumes(bars)
    last_price = closes[-1] if closes else None
    rsi = _compute_rsi(closes, 14)

    yr_lows = lows[-252:] if len(lows) >= 252 else lows
    low_52w = min(yr_lows) if yr_lows else None
    pct_above_low = ((last_price - low_52w) / low_52w) if (last_price and low_52w) else None
    near_low = pct_above_low is not None and -0.005 <= pct_above_low <= 0.05

    vol_ratio = None
    if len(vols) >= 21:
        baseline = sum(vols[-21:-1]) / 20
        if baseline > 0:
            vol_ratio = vols[-1] / baseline

    # Signal 4: price below 20d SMA
    sma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
    below_sma20 = last_price is not None and sma20 is not None and last_price < sma20

    # Signal 5: range expansion on a bearish candle (close in lower half of range)
    atr_10d: float | None = None
    if len(highs) >= 11 and len(lows) >= 11 and len(closes) >= 11:
        true_ranges = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(-10, 0)
        ]
        atr_10d = _mean(true_ranges)
    today_range = (highs[-1] - lows[-1]) if (highs and lows) else None
    # Bearish range expansion: expanded range AND close in lower half
    today_close_in_range = (
        ((closes[-1] - lows[-1]) / today_range) if (today_range and today_range > 0) else 0.5
    )
    range_expansion_fired = (
        today_range is not None and atr_10d is not None
        and atr_10d > 0 and today_range > atr_10d * 1.3
        and today_close_in_range <= 0.5
    )

    signals = [
        _signal(
            "near 52w low",
            _fmt_pct(pct_above_low),
            near_low,
            f"Distance above the trailing 52-week low. Fires when within 5% of the low. "
            f"52w low: {_fmt_num(low_52w, 2)}, current: {_fmt_num(last_price, 2)}.",
        ),
        _signal(
            "volume surge",
            f"{_fmt_num(vol_ratio, 2)}×" if vol_ratio is not None else "—",
            vol_ratio is not None and vol_ratio > 1.5,
            f"Today's volume vs trailing 20-day average. Fires when > 1.5× (capitulation flow). "
            f"Current: {_fmt_num(vol_ratio, 2)}×.",
        ),
        _signal(
            "RSI",
            _fmt_num(rsi, 0),
            rsi is not None and rsi < 40,
            f"RSI (14d). Fires when < 40 — sustained downside momentum. Current: {_fmt_num(rsi, 1)}.",
        ),
        _signal(
            "below 20d SMA",
            "yes" if below_sma20 else "no",
            below_sma20,
            "Price below 20d SMA — breakdown has lost short-term trend support.",
        ),
        _signal(
            "range expansion",
            _fmt_pct((today_range / atr_10d - 1.0) if (today_range and atr_10d) else None),
            range_expansion_fired,
            "Today's H-L range > 10d ATR × 1.3, close in lower half — expanded bearish candle confirms capitulation.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _near_low_mag = max(1.0, 1.5 - abs(pct_above_low or 0.05) / 0.1) if near_low else 1.0
    _m = [
        _near_low_mag,
        _mag_volume(vol_ratio, 1.5) if (vol_ratio is not None and vol_ratio > 1.5) else 1.0,
        _mag_rsi(rsi, 40.0) if (rsi is not None and rsi < 40) else 1.0,
        1.2 if below_sma20 else 1.0,
        min(1.5, 1.0 + (today_range / atr_10d - 1.3) / 0.5) if range_expansion_fired else 1.0,
    ]
    c_pct = _conviction_pct(signals, (30.0, 26.25, 18.75, 15.0, 10.0), _m)
    sort_key = pct_above_low if pct_above_low is not None else 0.0
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("breakdown", c_pct, "put"),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction="put",
            strike_offset_pct=-2.0,
            expiry_lean="mid",
            reasoning=(
                "Breaking 52-week support on volume — capitulation in motion. "
                "2% OTM put below current price: leveraged downside if the "
                "breakdown continues. 2–4 week expiry to outlast a dead-cat bounce."
            ),
        ),
    }


def _score_volume_spike(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Unusual volume + big move + close at the wick extreme. Direction-agnostic:
    surfaces "something is happening" names. User picks calls or puts based on
    whether the close is at the day's high (bullish) or low (bearish)."""
    closes = _closes(bars)
    highs = _highs(bars)
    lows = _lows(bars)
    vols = _volumes(bars)

    if not bars:
        return {"conviction": 0, "signals": [], "sort_key": 0.0, "last_price": None}

    last_price = closes[-1]
    # Today's move vs yesterday's close.
    today_return = ((closes[-1] - closes[-2]) / closes[-2]) if len(closes) >= 2 and closes[-2] else None

    # Volume ratio
    vol_ratio = None
    if len(vols) >= 21:
        baseline = sum(vols[-21:-1]) / 20
        if baseline > 0:
            vol_ratio = vols[-1] / baseline

    # Close-in-range: 0 = closed at day's low, 1 = closed at day's high.
    today_hi = highs[-1]
    today_lo = lows[-1]
    rng = today_hi - today_lo
    close_in_range = ((closes[-1] - today_lo) / rng) if rng > 0 else 0.5
    # Conviction in either direction: top 25% bullish, bottom 25% bearish.
    wick_extreme = close_in_range >= 0.75 or close_in_range <= 0.25

    # Signal 4: close position — bullish if close in upper 60%, bearish if lower 40%
    close_position_bullish = close_in_range >= 0.60
    close_position_bearish = close_in_range <= 0.40
    close_position_fired = close_position_bullish or close_position_bearish

    # Signal 5: price impact — abs return on spike day > 1.5%
    price_impact_fired = today_return is not None and abs(today_return) > 0.015

    signals = [
        _signal(
            "volume",
            f"{_fmt_num(vol_ratio, 2)}×" if vol_ratio is not None else "—",
            vol_ratio is not None and vol_ratio > 2.0,
            f"Today's volume vs trailing 20-day average. Fires when > 2× (real flow, not noise). "
            f"Current: {_fmt_num(vol_ratio, 2)}×.",
        ),
        _signal(
            "today move",
            _fmt_pct(today_return),
            today_return is not None and abs(today_return) > 0.03,
            f"Today's return vs prior close. Fires when |move| > 3%. Current: {_fmt_pct(today_return)}.",
        ),
        _signal(
            "close-at-wick",
            f"{close_in_range:.0%} of range",
            wick_extreme,
            "Where today's close sits inside today's high–low range. Fires when in the top 25% "
            "(close-on-high → bullish conviction) or bottom 25% (close-on-low → bearish). "
            f"Current: {close_in_range:.0%}.",
        ),
        _signal(
            "close position",
            f"{close_in_range:.0%} of range",
            close_position_fired,
            "Close in upper 60% of day's range (bullish) or lower 40% (bearish). Broader test of directional conviction.",
        ),
        _signal(
            "price impact",
            _fmt_pct(today_return),
            price_impact_fired,
            "Absolute return on spike day > 1.5% — volume is moving the price, not just noise.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _m = [
        _mag_volume(vol_ratio, 2.0) if (vol_ratio is not None and vol_ratio > 2.0) else 1.0,
        min(1.5, 1.0 + (abs(today_return or 0) - 0.03) / 0.03 * 0.5) if (today_return is not None and abs(today_return) > 0.03) else 1.0,
        min(1.5, 1.0 + max(0.0, abs(close_in_range - 0.5) - 0.25) / 0.25) if wick_extreme else 1.0,
        min(1.5, 1.0 + max(0.0, abs(close_in_range - 0.5) - 0.1) / 0.4) if close_position_fired else 1.0,
        min(1.5, 1.0 + (abs(today_return or 0) - 0.015) / 0.015) if price_impact_fired else 1.0,
    ]
    c_pct = _conviction_pct(signals, (22.5, 26.25, 26.25, 15.0, 10.0), _m)
    direction_sign = 1 if close_in_range >= 0.5 else -1
    score = (vol_ratio or 0) * abs(today_return or 0) * direction_sign
    sort_key = -score
    # Direction follows the close: top of day's range → bullish flow → calls;
    # bottom of range → bearish flow → puts.
    if close_in_range >= 0.5:
        rec_dir, rec_why = "call", "Closed near the high of the day on unusual volume — bullish flow."
    else:
        rec_dir, rec_why = "put", "Closed near the low of the day on unusual volume — bearish flow."
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("volume_spike", c_pct, rec_dir),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction=rec_dir,
            strike_offset_pct=0.0,
            expiry_lean="near",
            reasoning=(
                f"{rec_why} ATM strike captures direction with maximum gamma. "
                "Near-term weekly: ride the day's flow into the next 1–5 sessions."
            ),
        ),
    }


def _score_pullback(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Price near 20/50d MA + above 200d MA + mild RSI dip. The "buy the dip"
    retail setup — calls on the bounce off the moving average."""
    closes = _closes(bars)
    vols = _volumes(bars)
    last_price = closes[-1] if closes else None
    rsi = _compute_rsi(closes, 14)

    sma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
    sma50 = _mean(closes[-50:]) if len(closes) >= 50 else None
    sma200 = _mean(closes[-200:]) if len(closes) >= 200 else None

    near_ma_pct = None
    if last_price is not None:
        dists = []
        if sma20 is not None:
            dists.append(abs(last_price - sma20) / sma20)
        if sma50 is not None:
            dists.append(abs(last_price - sma50) / sma50)
        if dists:
            near_ma_pct = min(dists)
    near_ma = near_ma_pct is not None and near_ma_pct < 0.03

    above_200 = sma200 is not None and last_price is not None and last_price > sma200

    # Signal 4: above 50d SMA (pullback in uptrend should stay above it)
    above_sma50 = sma50 is not None and last_price is not None and last_price > sma50

    # Signal 5: volume contraction during pullback (healthy dip, not breakdown)
    avg_vol_5d = _mean([float(v) for v in vols[-5:]]) if len(vols) >= 5 else None
    avg_vol_20d = _mean([float(v) for v in vols[-20:]]) if len(vols) >= 20 else None
    vol_contraction_fired = (
        avg_vol_5d is not None and avg_vol_20d is not None
        and avg_vol_20d > 0 and avg_vol_5d < avg_vol_20d * 0.85
    )

    signals = [
        _signal(
            "near 20/50d MA",
            _fmt_pct(near_ma_pct),
            near_ma,
            f"Distance to the closer of the 20d or 50d moving average. Fires when within 3% — price is testing support. "
            f"Current: {_fmt_pct(near_ma_pct)} away.",
        ),
        _signal(
            "uptrend (>200d MA)",
            "yes" if above_200 else "no",
            above_200,
            f"Price > 200-day MA confirms the longer-term uptrend, so the pullback is a dip in a bull, not a downtrend. "
            f"200d MA: {_fmt_num(sma200, 2)}, current: {_fmt_num(last_price, 2)}.",
        ),
        _signal(
            "RSI dip",
            _fmt_num(rsi, 0),
            rsi is not None and 35 <= rsi <= 55,
            f"RSI (14d) in the 35–55 band. Fires when momentum has cooled but isn't crashing. "
            f"Current: {_fmt_num(rsi, 1)}.",
        ),
        _signal(
            "above 50d SMA",
            "yes" if above_sma50 else "no",
            above_sma50,
            f"Pullback in uptrend should stay above 50d SMA. 50d MA: {_fmt_num(sma50, 2)}, current: {_fmt_num(last_price, 2)}.",
        ),
        _signal(
            "vol contraction",
            f"{_fmt_num((avg_vol_5d or 0) / (avg_vol_20d or 1), 2)}×" if avg_vol_20d else "—",
            vol_contraction_fired,
            "Low volume during pullback suggests exhaustion, not distribution.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _near_ma_mag = max(1.0, 1.5 - (near_ma_pct or 0.03) / 0.06) if near_ma else 1.0
    _above200_mag = 1.2 if above_200 else 1.0
    _rsi_pb_mag = max(1.0, 1.0 + abs(45.0 - (rsi or 45)) / 10.0 * 0.5) if (rsi is not None and 35 <= rsi <= 55) else 1.0
    _m = [
        _near_ma_mag, _above200_mag, _rsi_pb_mag,
        1.2 if above_sma50 else 1.0,
        1.2 if vol_contraction_fired else 1.0,
    ]
    c_pct = _conviction_pct(signals, (30.0, 18.75, 26.25, 15.0, 10.0), _m)
    sort_key = near_ma_pct if near_ma_pct is not None else 1.0
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("pullback", c_pct, "call"),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction="call",
            strike_offset_pct=0.0,
            expiry_lean="near",
            reasoning=(
                "Buy-the-dip in an uptrend — price has retraced to support at the "
                "20/50d MA. ATM call captures the bounce with ~0.5 delta. "
                "Near-term weekly: the bounce off support usually happens within 3–5 sessions."
            ),
        ),
    }


def _score_trend_bias(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """50d/200d MA cross context + accelerating gap. Slow, strategic signal —
    pairs naturally with longer-dated options. Direction follows whichever side
    the 50d is on relative to the 200d.

    All three signals look the same in either direction; the user reads the
    sign of the chip values to know which way to lean.
    """
    closes = _closes(bars)
    last_price = closes[-1] if closes else None

    if len(closes) < 210:
        return {
            "conviction": 0,
            "signals": [
                _signal("50/200d cross", "—", False, "Need ≥210 bars of history to compute the 200d MA."),
                _signal("price vs 50d MA", "—", False, "Need ≥50 bars of history."),
                _signal("gap widening", "—", False, "Need ≥10 bars of history past the cross."),
                _signal("MA separation", "—", False, "Need ≥210 bars of history."),
                _signal("RSI bias", "—", False, "Need ≥210 bars of history."),
            ],
            "sort_key": 0.0,
            "last_price": last_price,
            "recommendation": _recommendation(
                direction="call",
                strike_offset_pct=0.0,
                expiry_lean="far",
                reasoning="Not enough history to compute a Trend Bias recommendation.",
            ),
        }

    sma50_now = _mean(closes[-50:])
    sma200_now = _mean(closes[-200:])
    cross_gap = sma50_now - sma200_now
    cross_state = "Golden" if cross_gap > 0 else "Death"

    sma50_then = _mean(closes[-60:-10])
    sma200_then = _mean(closes[-210:-10])
    cross_gap_then = sma50_then - sma200_then

    # Widening: gap has grown (in the direction of the current cross) over the last 10 days.
    widening = (cross_gap - cross_gap_then) * (1 if cross_gap > 0 else -1) > 0

    price_vs_50 = (last_price - sma50_now) / sma50_now if sma50_now > 0 else 0.0
    price_trend_aligned = (price_vs_50 > 0 and cross_gap > 0) or (price_vs_50 < 0 and cross_gap < 0)

    # Signal 4: MA separation — (sma50 - sma200) / sma200
    ma_sep = (sma50_now - sma200_now) / sma200_now if sma200_now and sma200_now > 0 else 0.0
    # Bullish bias: ma_sep > 0.02 (golden cross with >2% separation)
    # Bearish bias: ma_sep < -0.02 (death cross with >2% separation)
    ma_sep_fired = abs(ma_sep) > 0.02

    # Signal 5: RSI bias — above 55 for bullish, below 45 for bearish
    rsi = _compute_rsi(closes, 14)
    rsi_bias_bullish = cross_gap > 0 and rsi is not None and rsi > 55
    rsi_bias_bearish = cross_gap < 0 and rsi is not None and rsi < 45
    rsi_bias_fired = rsi_bias_bullish or rsi_bias_bearish

    signals = [
        _signal(
            "50/200d cross",
            cross_state,
            True,  # always informational — fired = "we have a stance"
            f"50d MA {_fmt_num(sma50_now, 2)} vs 200d MA {_fmt_num(sma200_now, 2)}. "
            f"50d above = Golden Cross (uptrend); below = Death Cross (downtrend). "
            f"Current gap: {_fmt_num(cross_gap, 2)}.",
        ),
        _signal(
            "price vs 50d",
            _fmt_pct(price_vs_50),
            price_trend_aligned,
            f"Price {_fmt_pct(price_vs_50)} from 50d MA. Fires when price is on the same side of the 50d MA as "
            f"the trend (above in Golden, below in Death) — riding the trend, not fading it.",
        ),
        _signal(
            "trend accelerating",
            "yes" if widening else "no",
            widening,
            f"50d–200d gap has widened in the trend's direction over the last 10 days. "
            f"Fires when the trend is gaining strength, not stalling.",
        ),
        _signal(
            "MA separation",
            _fmt_pct(ma_sep),
            ma_sep_fired,
            f"(50d MA - 200d MA) / 200d MA. Fires when separation > 2% in either direction — meaningful structural gap.",
        ),
        _signal(
            "RSI bias",
            _fmt_num(rsi, 0),
            rsi_bias_fired,
            "RSI > 55 in a Golden Cross (bullish momentum) or RSI < 45 in a Death Cross (bearish momentum). Confirms trend.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _gap_mag = min(1.5, 1.0 + abs(cross_gap) / max(sma200_now or 1, 1) * 3.0)
    _aligned_mag = 1.2 if price_trend_aligned else 1.0
    _widen_mag = 1.2 if widening else 1.0
    _m = [
        _gap_mag, _aligned_mag, _widen_mag,
        min(1.5, 1.0 + (abs(ma_sep) - 0.02) / 0.02) if ma_sep_fired else 1.0,
        _mag_rsi(rsi, 55.0 if cross_gap > 0 else 45.0) if rsi_bias_fired else 1.0,
    ]
    c_pct = _conviction_pct(signals, (30.0, 26.25, 18.75, 15.0, 10.0), _m)
    sort_key = -abs(cross_gap) if cross_gap is not None else 0.0
    # Direction follows the cross state.
    if cross_gap > 0:
        rec_dir, cross_label = "call", "Golden Cross"
    else:
        rec_dir, cross_label = "put", "Death Cross"
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("trend_bias", c_pct, rec_dir),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction=rec_dir,
            strike_offset_pct=0.0,
            expiry_lean="far",
            reasoning=(
                f"{cross_label} regime — long-term trend signal. ATM strike with "
                "a 1–2 month expiry: this is a strategic position, not a tactical "
                "one. Give it time to play out."
            ),
        ),
    }


def _realized_vol(closes: list[float], window: int) -> float | None:
    """Annualized realized vol from log returns over the trailing ``window`` bars.

    Returns ``None`` when there isn't enough history. Annualized via √252.
    """
    if len(closes) <= window:
        return None
    rets: list[float] = []
    for i in range(len(closes) - window, len(closes)):
        if i == 0 or closes[i - 1] <= 0:
            continue
        rets.append(math.log(closes[i] / closes[i - 1]))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def _score_vol_expansion(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Realized-vol regime change. Surfaces names where short-term vol has
    blown out relative to the trailing baseline — options are likely rich
    and the underlying is moving enough to make either premium-selling or
    long-vol plays interesting.

    We use realized vol as an IV proxy because the screener doesn't have
    historical IV rank wired (would need a separate vol-history pipeline).
    Realized-vol expansion is a strong leading indicator of IV expansion in
    practice.
    """
    closes = _closes(bars)
    last_price = closes[-1] if closes else None
    rv5 = _realized_vol(closes, 5)
    rv30 = _realized_vol(closes, 30)
    today_return = ((closes[-1] - closes[-2]) / closes[-2]) if len(closes) >= 2 and closes[-2] else None
    vol_ratio = (rv5 / rv30) if (rv5 is not None and rv30 is not None and rv30 > 0) else None

    # Signal 4: ATR expansion — 5d avg ATR vs 20d avg ATR
    highs = _highs(bars)
    lows = _lows(bars)
    atr_5d: float | None = None
    atr_20d: float | None = None
    if len(highs) >= 21 and len(lows) >= 21 and len(closes) >= 21:
        tr_series = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(-20, 0)
        ]
        atr_5d = _mean(tr_series[-5:])
        atr_20d = _mean(tr_series)
    atr_expansion_fired = (
        atr_5d is not None and atr_20d is not None
        and atr_20d > 0 and atr_5d / atr_20d > 1.2
    )
    atr_ratio = (atr_5d / atr_20d) if (atr_5d and atr_20d and atr_20d > 0) else None

    # Signal 5: Bollinger Band width (20d, 2σ)
    bb_width: float | None = None
    bb_recent_avg: float | None = None
    if len(closes) >= 40:
        _bb_mid_now = _mean(closes[-20:])
        _bb_sd_now = _stddev(closes[-20:], _bb_mid_now)
        if _bb_mid_now > 0:
            bb_width = (4.0 * _bb_sd_now) / _bb_mid_now  # (upper-lower)/mid
        # Use 20-bar rolling average of BB width from bars -40..-20 as baseline
        _bb_mid_prev = _mean(closes[-40:-20])
        _bb_sd_prev = _stddev(closes[-40:-20], _bb_mid_prev)
        if _bb_mid_prev > 0:
            bb_recent_avg = (4.0 * _bb_sd_prev) / _bb_mid_prev
    bb_width_fired = (
        bb_width is not None and bb_recent_avg is not None
        and bb_recent_avg > 0 and bb_width > bb_recent_avg
    )

    signals = [
        _signal(
            "vol expansion",
            f"{_fmt_num(vol_ratio, 2)}×" if vol_ratio is not None else "—",
            vol_ratio is not None and vol_ratio > 1.5,
            f"5-day realized vol vs trailing 30-day. Fires when > 1.5× (regime change in motion). "
            f"5d realized vol {_fmt_num((rv5 or 0) * 100, 1)}% annualized, 30d {_fmt_num((rv30 or 0) * 100, 1)}%.",
        ),
        _signal(
            "5d realized vol",
            f"{_fmt_num((rv5 or 0) * 100, 0)}%",
            rv5 is not None and rv5 > 0.40,
            f"Annualized 5-day realized vol. Fires when > 40% (premium is rich in absolute terms — good for selling).",
        ),
        _signal(
            "today move",
            _fmt_pct(today_return),
            today_return is not None and abs(today_return) > 0.02,
            f"Today's return vs prior close. Fires when |move| > 2% (the trigger that's driving the vol regime change).",
        ),
        _signal(
            "ATR expansion",
            f"{_fmt_num(atr_ratio, 2)}×" if atr_ratio is not None else "—",
            atr_expansion_fired,
            "5d avg true range vs 20d avg true range. Fires when ratio > 1.2 — price bars are expanding, vol regime is shifting.",
        ),
        _signal(
            "BB width",
            _fmt_pct(bb_width),
            bb_width_fired,
            "20d Bollinger Band width ((upper-lower)/mid). Fires when current width exceeds the prior 20d average — bands are expanding.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _m = [
        _mag_volume(vol_ratio, 1.5) if (vol_ratio is not None and vol_ratio > 1.5) else 1.0,
        min(1.5, 1.0 + ((rv5 or 0) - 0.40) / 0.40 * 0.5) if (rv5 is not None and rv5 > 0.40) else 1.0,
        min(1.5, 1.0 + (abs(today_return or 0) - 0.02) / 0.02 * 0.5) if (today_return is not None and abs(today_return) > 0.02) else 1.0,
        min(1.5, 1.0 + (atr_ratio - 1.2) / 0.3) if (atr_ratio and atr_expansion_fired) else 1.0,
        min(1.5, 1.0 + (bb_width - bb_recent_avg) / (bb_recent_avg or 0.01)) if (bb_width and bb_recent_avg and bb_width_fired) else 1.0,
    ]
    c_pct = _conviction_pct(signals, (33.75, 22.5, 18.75, 15.0, 10.0), _m)
    sort_key = -rv5 if rv5 is not None else 0.0
    # Direction follows today's trigger move — wherever the regime is breaking.
    if today_return is not None and today_return >= 0:
        rec_dir, rec_why = "call", "Vol regime expanding to the upside — follow with calls."
    else:
        rec_dir, rec_why = "put", "Vol regime expanding to the downside — follow with puts."
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("vol_expansion", c_pct, rec_dir),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction=rec_dir,
            strike_offset_pct=0.0,
            expiry_lean="near",
            reasoning=(
                f"{rec_why} ATM strike for maximum gamma into the next move. "
                "Near-term weekly: realized vol blowouts usually mean-revert within "
                "1–2 weeks, so don't pay for more time than you need."
            ),
        ),
    }


def _score_unusual_options_activity(
    bars: list,
    _qqq_bars: list,
    *,
    ticker: str | None = None,
    client: MassiveClient | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Unusual options activity — scans the underlying's chain looking for
    individual contracts with volume that significantly exceeds open interest
    (vol/OI > 2 = new positioning, often called 'sweep' or 'flow').

    Cost: one chain HTTP call per ticker. The screener caches its full result
    so a refresh only pays once per (sleeve, strategy, min_price) per 5 min.

    Surfaces tickers where smart-money may be establishing a directional
    position. The chain viewer below the card shows the actual contracts so
    the user can spot which strikes are being bought.
    """
    closes = _closes(bars)
    last_price = closes[-1] if closes else None

    # Degenerate / harness inputs: return an empty scorecard rather than
    # making an HTTP call we can't satisfy.
    if ticker is None or client is None or last_price is None:
        return {
            "conviction": 0,
            "conviction_pct": 0.0,
            "expiry_tiers": [],
            "signals": [
                _signal("max vol/OI", "—", False, "No chain context available."),
                _signal("max contract vol", "—", False, "No chain context available."),
                _signal("OTM concentration", "—", False, "No chain context available."),
                _signal("vol/OI ratio", "—", False, "No chain context available."),
                _signal("IV rank", "—", False, "No chain context available."),
            ],
            "sort_key": 0.0,
            "last_price": last_price,
            "recommendation": _recommendation(
                direction="call",
                strike_offset_pct=0.0,
                expiry_lean="near",
                reasoning="No chain data available — recommendation unavailable.",
            ),
        }

    # Pull the chain across the next 30 days at a wider strike band (±15%)
    # so we catch OTM activity — speculative flow lives OTM.
    today = datetime.date.today()
    horizon_end = (today + datetime.timedelta(days=30)).isoformat()
    low_strike = last_price * 0.85
    high_strike = last_price * 1.15

    try:
        raw = client.get_options_chain(
            ticker,
            expiration_date_gte=today.isoformat(),
            expiration_date_lte=horizon_end,
            strike_price_gte=low_strike,
            strike_price_lte=high_strike,
            limit=250,
        )
    except MassiveError as exc:
        logger.warning("UOA chain fetch failed for %s: %s", ticker, exc)
        return {
            "conviction": 0,
            "conviction_pct": 0.0,
            "expiry_tiers": [],
            "signals": [
                _signal("max vol/OI", "—", False, f"Chain fetch failed: {exc}"),
                _signal("max contract vol", "—", False, ""),
                _signal("OTM concentration", "—", False, ""),
                _signal("vol/OI ratio", "—", False, ""),
                _signal("IV rank", "—", False, ""),
            ],
            "sort_key": 0.0,
            "last_price": last_price,
            "recommendation": _recommendation(
                direction="call",
                strike_offset_pct=0.0,
                expiry_lean="near",
                reasoning=f"Chain fetch failed — recommendation unavailable. {exc}",
            ),
        }

    rows = raw.get("results") or []
    if not rows:
        return {
            "conviction": 0,
            "conviction_pct": 0.0,
            "expiry_tiers": [],
            "signals": [
                _signal("max vol/OI", "—", False, "No contracts in window."),
                _signal("max contract vol", "—", False, ""),
                _signal("OTM concentration", "—", False, ""),
                _signal("vol/OI ratio", "—", False, ""),
                _signal("IV rank", "—", False, ""),
            ],
            "sort_key": 0.0,
            "last_price": last_price,
            "recommendation": _recommendation(
                direction="call",
                strike_offset_pct=0.0,
                expiry_lean="near",
                reasoning="No contracts in the scan window — recommendation unavailable.",
            ),
        }

    max_vol_oi = 0.0
    max_contract_vol = 0
    total_vol = 0
    otm_vol = 0
    otm_call_vol = 0
    otm_put_vol = 0
    for r in rows:
        day = r.get("day") or {}
        vol = day.get("volume") or 0
        oi = r.get("open_interest") or 0
        details = r.get("details") or {}
        strike = details.get("strike_price")
        contract_type = details.get("contract_type")
        if vol > max_contract_vol:
            max_contract_vol = vol
        if oi > 0 and vol > 0:
            ratio = vol / oi
            if ratio > max_vol_oi:
                max_vol_oi = ratio
        total_vol += vol
        if strike is not None and contract_type is not None:
            is_otm_call = contract_type == "call" and strike > last_price
            is_otm_put = contract_type == "put" and strike < last_price
            if is_otm_call:
                otm_vol += vol
                otm_call_vol += vol
            elif is_otm_put:
                otm_vol += vol
                otm_put_vol += vol

    otm_pct = (otm_vol / total_vol) if total_vol > 0 else None

    # Signal 4: vol/OI ratio across all contracts (>0.3 = unusual relative activity)
    total_oi = sum(r.get("open_interest") or 0 for r in rows)
    agg_vol_oi_ratio = (total_vol / total_oi) if total_oi > 0 else None
    vol_oi_ratio_fired = agg_vol_oi_ratio is not None and agg_vol_oi_ratio > 0.3

    # Signal 5: IV rank proxy — current avg IV vs trailing estimate from chain
    # Use the avg mid-IV from contracts that have it; if unavailable, never fires.
    iv_samples: list[float] = []
    for r in rows:
        greeks = r.get("greeks") or {}
        implied_vol = greeks.get("iv") or r.get("implied_volatility")
        if implied_vol and isinstance(implied_vol, (int, float)) and implied_vol > 0:
            iv_samples.append(float(implied_vol))
    current_iv: float | None = (_mean(iv_samples) if iv_samples else None)
    iv_rank_fired = False  # placeholder that never fires when chain IV is unavailable
    iv_rank_text = "—"
    if current_iv is not None:
        # Basic signal: fire when IV > 50% annualized (unusually high)
        iv_rank_fired = current_iv > 0.50
        iv_rank_text = f"{current_iv * 100:.0f}%"

    signals = [
        _signal(
            "max vol/OI",
            f"{_fmt_num(max_vol_oi, 2)}×",
            max_vol_oi > 2.0,
            f"Highest volume-to-open-interest ratio on any contract in the chain. Fires when > 2× — "
            f"contracts are trading at double their outstanding count, a sign new positions are being opened.",
        ),
        _signal(
            "max contract vol",
            f"{max_contract_vol:,}" if max_contract_vol < 10000 else f"{max_contract_vol / 1000:.1f}k",
            max_contract_vol > 500,
            f"Single most-traded contract today. Fires when > 500 contracts — real flow, not noise. "
            f"Open the chain to see which strikes are seeing the action.",
        ),
        _signal(
            "OTM concentration",
            _fmt_pct(otm_pct),
            otm_pct is not None and otm_pct > 0.6,
            f"Share of today's volume in out-of-the-money strikes. Fires when > 60% — speculative directional bets, "
            f"not boring at-the-money rolls. Current: {_fmt_pct(otm_pct)} of {total_vol:,} contracts.",
        ),
        _signal(
            "vol/OI ratio",
            f"{_fmt_num(agg_vol_oi_ratio, 2)}×" if agg_vol_oi_ratio is not None else "—",
            vol_oi_ratio_fired,
            "Total volume / total open interest across the chain. Fires when > 0.3 — unusual activity relative to float.",
        ),
        _signal(
            "IV rank",
            iv_rank_text,
            iv_rank_fired,
            "Current average implied volatility from chain data. Fires when IV > 50% annualized — options are rich, confirming unusual activity.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _m = [
        min(1.5, 1.0 + (max_vol_oi - 2.0) / 4.0) if max_vol_oi > 2.0 else 1.0,
        1.2 if max_contract_vol > 500 else 1.0,
        1.2 if (otm_pct is not None and otm_pct > 0.6) else 1.0,
        min(1.5, 1.0 + (agg_vol_oi_ratio - 0.3) / 0.3) if vol_oi_ratio_fired else 1.0,
        min(1.5, 1.0 + (current_iv - 0.50) / 0.25) if (current_iv and iv_rank_fired) else 1.0,
    ]
    c_pct = _conviction_pct(signals, (30.0, 22.5, 22.5, 15.0, 10.0), _m)
    sort_key = -max_vol_oi
    # Recommendation: follow the OTM volume skew. More OTM call volume = smart
    # money is betting up. More OTM put volume = betting down.
    if otm_call_vol > otm_put_vol * 1.2:
        rec_dir, rec_why = "call", f"OTM call volume ({otm_call_vol:,}) significantly exceeds OTM put volume ({otm_put_vol:,}) — flow is bullish."
    elif otm_put_vol > otm_call_vol * 1.2:
        rec_dir, rec_why = "put", f"OTM put volume ({otm_put_vol:,}) significantly exceeds OTM call volume ({otm_call_vol:,}) — flow is bearish."
    else:
        rec_dir, rec_why = "call", f"OTM call/put volume mixed ({otm_call_vol:,} calls vs {otm_put_vol:,} puts). Default to calls; check the chain to confirm direction."
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("unusual_options_activity", c_pct, rec_dir),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction=rec_dir,
            strike_offset_pct=0.0,
            expiry_lean="near",
            reasoning=(
                f"{rec_why} Highlighted ATM strike anchors your position; open the "
                "chain and look for the specific OTM strikes with the highest vol/OI — "
                "that's where the conviction lies."
            ),
        ),
    }


def _bars_to_candles(bars: list) -> list[dict]:
    """Convert Price bar objects to candle dicts the pattern engine expects."""
    return [
        {
            "date": b.time,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        }
        for b in bars
    ]


def _make_pattern_scorer(pattern_name: str, detector_fn: Any, is_bullish: bool):
    """Return a strategy scorer closure for a single chart pattern.

    Runs the detector on the most recent 120 bars and checks whether a
    confirmed breakout completed within the last 10 bars. Three binary
    signals drive the 0–3 conviction score.
    """
    _direction = "call" if is_bullish else "put"
    _bias = "bullish" if is_bullish else "bearish"
    _strike_offset = 0.02 if is_bullish else -0.02
    _reasoning = (
        f"{pattern_name} breakout detected — {_bias} continuation setup. "
        f"Strike slightly {'above' if is_bullish else 'below'} spot to capture "
        "the post-breakout move. Medium-term expiry (3–5 weeks) gives the "
        "pattern room to develop without excessive theta drag."
    )

    def _scorer(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
        last_price: float | None = bars[-1].close if bars else None

        candles = _bars_to_candles(bars[-120:])
        try:
            detections = detector_fn(candles)
        except Exception:
            detections = []

        bar_dates = [b.time for b in bars[-120:]]
        recent: list[dict] = []
        for det in detections:
            try:
                idx = bar_dates.index(det["end_date"])
            except ValueError:
                continue
            bars_ago = len(bar_dates) - 1 - idx
            if bars_ago <= 10:
                recent.append({**det, "_bars_ago": bars_ago})

        recent.sort(key=lambda x: -x["confidence"])
        best = recent[0] if recent else None
        best_conf = best["confidence"] if best else 0.0
        best_bars_ago = best["_bars_ago"] if best else 999

        detected = best is not None
        high_conf = best_conf >= 60
        fresh = detected and best_bars_ago <= 5

        signals = [
            _signal(
                "Pattern Detected",
                f"{best_conf:.0f}%" if detected else "—",
                detected,
                (
                    f"{pattern_name} confirmed within the last 10 bars at "
                    f"{best_conf:.0f}% confidence. Fires whenever a completed "
                    "breakout is present in the recent window."
                ),
            ),
            _signal(
                "Strong Signal",
                f"{best_conf:.0f}%" if detected else "—",
                high_conf,
                (
                    f"Confidence ≥ 60 (current: {best_conf:.0f}%). High-confidence "
                    "detections have well-formed geometry and volume confirmation."
                ),
            ),
            _signal(
                "Recent Breakout",
                f"{best_bars_ago}d ago" if detected else "—",
                fresh,
                (
                    f"Breakout within the last 5 bars (currently {best_bars_ago} ago). "
                    "Fresher breakouts capture more of the post-pattern momentum."
                ),
            ),
        ]
        conviction = sum(1 for s in signals if s["fired"])

        return {
            "conviction": conviction,
            "signals": signals,
            "sort_key": -best_conf,
            "last_price": last_price,
            "recommendation": _recommendation(
                direction=_direction,
                strike_offset_pct=_strike_offset,
                expiry_lean="medium",
                reasoning=_reasoning,
            ),
        }

    return _scorer


_STRATEGY_REGISTRY: dict[str, dict[str, Any]] = {
    "weakness": {
        "label": "Weakness",
        "subtitle": "lagging QQQ + oversold",
        "description": "Names trailing QQQ that are oversold. Natural plays: bounce calls or continuation puts.",
        "scorer": _score_weakness,
    },
    "strength": {
        "label": "Strength",
        "subtitle": "leading QQQ + overbought",
        "description": "Names beating QQQ that are overbought. Natural plays: breakout calls or mean-reversion puts.",
        "scorer": _score_strength,
    },
    "momentum": {
        "label": "Momentum",
        "subtitle": "strong absolute trend",
        "description": "Pure trend-follow, no benchmark. Up >5% over 20d and >2% over 5d with RSI above 60. Plays: ride the trend with calls.",
        "scorer": _score_momentum,
    },
    "mean_reversion": {
        "label": "Mean Reversion",
        "subtitle": "stretched from 20d mean",
        "description": "Price >1.5 σ from its 20-day mean + RSI extreme. Bet the move overshoots and snaps back. Direction depends on which side it's stretched.",
        "scorer": _score_mean_reversion,
    },
    "breakout": {
        "label": "Breakout",
        "subtitle": "near 52w high + volume",
        "description": "Near 52-week high + volume surge + RSI > 60. Classic momentum continuation. Plays: calls.",
        "scorer": _score_breakout,
    },
    "breakdown": {
        "label": "Breakdown",
        "subtitle": "near 52w low + volume",
        "description": "Near 52-week low + volume surge + downside momentum. Bearish mirror of Breakout. Plays: puts.",
        "scorer": _score_breakdown,
    },
    "volume_spike": {
        "label": "Volume Spike",
        "subtitle": "unusual volume + big move",
        "description": "Today's volume > 2× trailing average + |move| > 3% + close at the wick extreme. Direction-agnostic — pick calls or puts based on the move's direction.",
        "scorer": _score_volume_spike,
    },
    "pullback": {
        "label": "Pullback",
        "subtitle": "dip in an uptrend",
        "description": "Price within 3% of 20d or 50d MA, still above 200d MA, RSI in 35–55 (mild dip). The 'buy the dip' setup. Plays: calls on the bounce.",
        "scorer": _score_pullback,
    },
    "trend_bias": {
        "label": "Trend Bias",
        "subtitle": "50/200d MA cross context",
        "description": "Golden/Death cross + price riding the trend + accelerating gap. Slower, strategic signal — good for longer-dated calls (Golden) or puts (Death).",
        "scorer": _score_trend_bias,
    },
    "vol_expansion": {
        "label": "Vol Expansion",
        "subtitle": "realized vol regime change",
        "description": "5-day realized vol vs 30-day baseline. Surfaces names where the vol regime is changing. High realized vol = options premium is rich (sell premium); paired with a fresh big move = continuation candidate.",
        "scorer": _score_vol_expansion,
    },
    "unusual_options_activity": {
        "label": "Unusual Options Activity",
        "subtitle": "vol/OI extremes in the chain",
        "description": "Scans the underlying's option chain for individual contracts with volume far above open interest — a sign of new directional positioning. Plays: follow the flow (calls if OTM call activity, puts if OTM puts).",
        "scorer": _score_unusual_options_activity,
    },
}

_PATTERN_DESCRIPTIONS: dict[str, str] = {
    "Bullish Flag": (
        "Sharp pole up followed by a tight, slightly downward-sloping consolidation. "
        "Breakout above the channel signals continuation. Plays: calls."
    ),
    "Bearish Flag": (
        "Sharp pole down followed by a tight, slightly upward-sloping consolidation. "
        "Breakdown below the channel signals continuation. Plays: puts."
    ),
    "Bull Pennant": (
        "Explosive move up (pole) into a symmetrical triangle consolidation. "
        "Volume contracts during the pennant; breakout on a volume surge. Plays: calls."
    ),
    "Double Bottom": (
        "Two roughly equal lows with a moderate bounce between them. "
        "Neckline breakout confirms the reversal from downtrend to uptrend. Plays: calls."
    ),
    "Double Top": (
        "Two roughly equal highs with a moderate pullback between them. "
        "Neckline breakdown confirms the reversal from uptrend to downtrend. Plays: puts."
    ),
    "Head and Shoulders": (
        "Three-peak top (left shoulder, higher head, right shoulder) with a neckline. "
        "Breakdown below the neckline is bearish. Plays: puts."
    ),
    "Inverse Head and Shoulders": (
        "Three-trough bottom (left shoulder, lower head, right shoulder). "
        "Breakout above the neckline is bullish. Plays: calls."
    ),
    "Ascending Triangle": (
        "Flat resistance + rising support — buyers pressing harder each swing. "
        "Breakout above resistance on volume confirms continuation. Plays: calls."
    ),
    "Descending Triangle": (
        "Flat support + descending resistance — sellers pressing harder each swing. "
        "Breakdown below support on volume confirms continuation. Plays: puts."
    ),
    "Cup and Handle": (
        "Rounded U-shaped base (cup) followed by a small consolidation dip (handle). "
        "Breakout above the rim is a classic bull signal. Plays: calls."
    ),
    "Rising Wedge": (
        "Price compressed into a rising channel with converging trendlines. "
        "Bearish resolution — prices typically break down. Plays: puts."
    ),
    "Falling Wedge": (
        "Price compressed into a falling channel with converging trendlines. "
        "Bullish resolution — prices typically break upward. Plays: calls."
    ),
}


def _pattern_key(name: str) -> str:
    """Normalize a pattern display name to a registry key."""
    return "pattern_" + name.lower().replace(" ", "_").replace("&", "and").replace("-", "_")


_VALID_STRATEGIES = set(_STRATEGY_REGISTRY.keys())


# Legacy single-strategy scorer kept for the BSM options-strategy backtest,
# which calls it with closes-only lists. Internally builds bar-shaped objects
# so the bars-based scorer works without an HTTP roundtrip.
class _ClosesOnlyBar:
    __slots__ = ("close", "high", "low", "volume")

    def __init__(self, close: float):
        self.close = close
        self.high = close
        self.low = close
        self.volume = 0


def _score_ticker(closes: list[float], qqq_closes: list[float]) -> dict[str, Any]:
    bars = [_ClosesOnlyBar(c) for c in closes]
    qqq_bars = [_ClosesOnlyBar(c) for c in qqq_closes]
    return _score_weakness(bars, qqq_bars)


# Ordered registry for UI rendering. Adding a strategy here + its scorer
# above is the only change needed to surface a new pill.
_STRATEGY_ORDER = [
    "weakness",
    "strength",
    "momentum",
    "mean_reversion",
    "breakout",
    "breakdown",
    "volume_spike",
    "pullback",
    "trend_bias",
    "vol_expansion",
    "unusual_options_activity",
]
