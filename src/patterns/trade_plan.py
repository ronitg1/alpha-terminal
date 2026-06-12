"""Trade-plan synthesis for pattern-scanner plays.

Given a detected chart pattern's key levels plus the underlying's recent
volatility, produce a concrete **entry / stop-loss / target** where the stop
is sized to the trader's risk tolerance and the stock's Average True Range
(ATR) — wider stops for more volatile names, tighter for calmer ones.

Pure functions, no I/O: the route layer fetches candles and calls these.

Design:
* **Entry** = the pattern's breakout level (the price that confirms the play).
* **Stop**  = ``entry ∓ risk_multiple × ATR``. The risk multiple comes from
  the trader's tolerance (conservative = tight, aggressive = wide), and ATR
  is the volatility term — so the same tolerance gives a wider dollar stop on
  a jumpy stock than on a sleepy one. The pattern's structural invalidation
  level is reported alongside as context.
* **Target** = the pattern's measured move (its height projected from the
  breakout), falling back to a 2R target when the geometry isn't available.
* **R:R**   = reward ÷ risk.

Because the scanner's plays are expressed through OPTIONS (long an ATM
call/put in the pattern's direction), :func:`build_option_plan` translates
those underlying levels into **premium space** for a specific contract:
reprice the option (Black-Scholes at the contract's IV, anchored to the live
market mid) at the entry / stop / target underlying prices, so the user gets
"buy ~$X, cut at ~$Y, take profit at ~$Z" on the contract itself.
"""
from __future__ import annotations

import math
from typing import Any

from src.backtesting.options_proxy import bsm_price

# Risk tolerance -> ATR multiple for the protective stop. A tighter multiple
# means smaller per-share risk but more exposure to noise stop-outs; wider
# gives the trade room at the cost of a bigger loss if hit.
RISK_ATR_MULTIPLE: dict[str, float] = {
    "conservative": 1.0,
    "moderate": 1.5,
    "aggressive": 2.5,
}
DEFAULT_RISK = "moderate"


def normalize_risk(risk: str | None) -> str:
    r = (risk or "").strip().lower()
    return r if r in RISK_ATR_MULTIPLE else DEFAULT_RISK


def compute_atr(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    """Average True Range over the last ``period`` bars (simple average)."""
    if len(candles) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(candles)):
        h, low, pc = candles[i].get("high"), candles[i].get("low"), candles[i - 1].get("close")
        if h is None or low is None or pc is None:
            continue
        trs.append(max(h - low, abs(h - pc), abs(low - pc)))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def annualized_vol(candles: list[dict[str, Any]], lookback: int = 30) -> float | None:
    """Annualized realized volatility (percent) from daily log returns."""
    closes = [c["close"] for c in candles if c.get("close")]
    if len(closes) < 6:
        return None
    lookback = min(lookback, len(closes) - 1)
    window = closes[-(lookback + 1):]
    rets = [math.log(window[i] / window[i - 1]) for i in range(1, len(window)) if window[i - 1]]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100.0


def _levels(pattern: str, kl: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    """Map a pattern's ``key_levels`` to (breakout, invalidation, target).

    ``breakout`` triggers the entry, ``invalidation`` is the structural stop
    reference, ``target`` is the measured-move objective. Any may be None when
    the detector didn't emit the needed levels (caller falls back to ATR math).
    """
    g = kl.get

    def height(a, b):
        return abs(a - b) if a is not None and b is not None else None

    if pattern == "Bullish Flag":
        brk, inv = g("channel_high"), g("channel_low")
        h = height(g("pole_high"), g("channel_low"))
        return brk, inv, (brk + h) if brk is not None and h is not None else None
    if pattern == "Bearish Flag":
        brk, inv = g("channel_low"), g("channel_high")
        h = height(g("channel_high"), g("pole_low"))
        return brk, inv, (brk - h) if brk is not None and h is not None else None
    if pattern == "Bull Pennant":
        brk = g("upper_trendline") or g("pole_high")
        inv = g("lower_trendline")
        h = height(g("pole_high"), g("lower_trendline"))
        return brk, inv, (brk + h) if brk is not None and h is not None else None
    if pattern == "Double Bottom":
        brk = g("neckline")
        lo = min(x for x in (g("bottom_1"), g("bottom_2")) if x is not None) if (g("bottom_1") or g("bottom_2")) else None
        h = height(brk, lo)
        return brk, lo, (brk + h) if brk is not None and h is not None else None
    if pattern == "Double Top":
        brk = g("neckline")
        hi = max(x for x in (g("top_1"), g("top_2")) if x is not None) if (g("top_1") or g("top_2")) else None
        h = height(hi, brk)
        return brk, hi, (brk - h) if brk is not None and h is not None else None
    if pattern == "Head and Shoulders":
        brk = g("neckline")
        h = height(g("head"), brk)
        return brk, g("right_shoulder") or g("head"), (brk - h) if brk is not None and h is not None else None
    if pattern == "Inverse Head and Shoulders":
        brk = g("neckline")
        h = height(brk, g("head"))
        return brk, g("right_shoulder") or g("head"), (brk + h) if brk is not None and h is not None else None
    if pattern == "Ascending Triangle":
        brk, inv = g("resistance"), g("support_at_start")
        h = height(g("resistance"), g("support_at_start"))
        return brk, inv, (brk + h) if brk is not None and h is not None else None
    if pattern == "Descending Triangle":
        brk, inv = g("support"), g("resistance_at_start")
        h = height(g("resistance_at_start"), g("support"))
        return brk, inv, (brk - h) if brk is not None and h is not None else None
    if pattern == "Cup and Handle":
        brk, inv = g("cup_lip"), g("handle_low")
        h = height(g("cup_lip"), g("cup_bottom"))
        return brk, inv, (brk + h) if brk is not None and h is not None else None
    if pattern == "Rising Wedge":  # bearish breakdown
        brk, inv = g("lower_trendline"), g("upper_trendline")
        h = height(g("upper_trendline"), g("lower_trendline"))
        return brk, inv, (brk - h) if brk is not None and h is not None else None
    if pattern == "Falling Wedge":  # bullish breakout
        brk, inv = g("upper_trendline"), g("lower_trendline")
        h = height(g("upper_trendline"), g("lower_trendline"))
        return brk, inv, (brk + h) if brk is not None and h is not None else None
    return None, None, None


def build_trade_plan(
    *,
    pattern: str,
    key_levels: dict[str, Any],
    current_price: float,
    atr: float | None,
    bullish: bool,
    risk: str,
) -> dict[str, Any]:
    """Assemble entry / stop / target for one detected play.

    Returns a JSON-friendly dict. ``stop`` is always volatility-sized
    (``entry ∓ risk_multiple × ATR``); ``structural_invalidation`` reports the
    pattern's own breakdown level so the user can compare.
    """
    risk = normalize_risk(risk)
    mult = RISK_ATR_MULTIPLE[risk]
    sign = 1.0 if bullish else -1.0

    breakout, invalidation, target = _levels(pattern, key_levels)
    entry = breakout if breakout is not None else current_price

    # Volatility-sized stop. Without an ATR (too few bars) fall back to a
    # 2% structural buffer so the plan still renders.
    atr_used = atr if atr and atr > 0 else max(entry * 0.02, 0.01)
    stop = entry - sign * mult * atr_used

    # Measured-move target, or a 2R default on the same side as the trade.
    risk_per_share = abs(entry - stop)
    if target is None or (bullish and target <= entry) or (not bullish and target >= entry):
        target = entry + sign * 2.0 * risk_per_share
        target_basis = "2R default (pattern geometry unavailable)"
    else:
        target_basis = "measured move (pattern height projected from breakout)"

    reward_per_share = abs(target - entry)
    rr = round(reward_per_share / risk_per_share, 2) if risk_per_share > 0 else None

    def pct(level: float) -> float:
        return round((level - entry) / entry * 100, 2) if entry else 0.0

    already_triggered = (bullish and current_price >= entry) or (not bullish and current_price <= entry)

    return {
        "direction": "long" if bullish else "short",
        "risk": risk,
        "atr_multiple": mult,
        "entry": round(entry, 2),
        "entry_basis": (
            "breakout level" if breakout is not None else "current price (no breakout level on this detection)"
        ),
        "already_triggered": already_triggered,
        "stop": round(stop, 2),
        "stop_pct": pct(stop),
        "stop_basis": f"{mult:g}x ATR ({round(atr_used, 2)}) {'below' if bullish else 'above'} entry",
        "structural_invalidation": round(invalidation, 2) if invalidation is not None else None,
        "target": round(target, 2),
        "target_pct": pct(target),
        "target_basis": target_basis,
        "risk_per_share": round(risk_per_share, 2),
        "reward_per_share": round(reward_per_share, 2),
        "risk_reward": rr,
    }


# ─── Option-premium translation ──────────────────────────────────────────────


def build_option_plan(
    *,
    underlying_plan: dict[str, Any],
    spot: float,
    contract: dict[str, Any],
    hold_days: float = 10.0,
) -> dict[str, Any] | None:
    """Translate an underlying trade plan into premium space for one contract.

    ``contract`` carries ``{type, strike, expiration, dte, mid, iv, delta,
    ticker}`` — ``mid`` is the live premium per share, ``iv`` a fraction
    (0.45 = 45%), ``delta`` signed. The play is always LONG premium (call for
    bullish patterns, put for bearish), so in premium space entry < target
    and stop < entry regardless of the underlying's direction.

    Repricing: Black-Scholes at the contract's IV, **anchored to the market
    mid** (model values are scaled so the model's price at today's spot equals
    the live mid — removes model-vs-market bias). Theta is acknowledged by
    repricing the stop at ``dte − hold/2`` and the target at ``dte − hold``
    (a stop tends to hit early; the measured move takes the full expected
    hold). Falls back to a delta-linear approximation when IV is missing,
    and returns None when neither IV nor delta is usable.
    """
    mid = contract.get("mid")
    dte = contract.get("dte")
    strike = contract.get("strike")
    opt_type = contract.get("type")
    if not mid or mid <= 0 or not dte or dte <= 0 or not strike or opt_type not in ("call", "put"):
        return None

    entry_u, stop_u, target_u = underlying_plan["entry"], underlying_plan["stop"], underlying_plan["target"]
    iv = contract.get("iv")
    delta = contract.get("delta")

    def _floor(p: float) -> float:
        return max(p, 0.01)

    basis: str
    if iv and iv > 0:
        def model(s: float, days_left: float) -> float:
            return bsm_price(
                spot=s, strike=float(strike),
                time_to_expiry_years=max(days_left, 0.5) / 365.0,
                sigma=float(iv), option_type=opt_type,
            )

        model_now = model(spot, dte)
        calib = (mid / model_now) if model_now > 0.01 else 1.0
        entry_p = _floor(model(entry_u, dte) * calib)
        stop_p = _floor(model(stop_u, dte - hold_days / 2) * calib)
        target_p = _floor(model(target_u, dte - hold_days) * calib)
        basis = f"Black-Scholes at {iv * 100:.0f}% IV, anchored to the live mid; target priced {hold_days:.0f}d of theta in"
    elif delta is not None:
        entry_p = _floor(mid + delta * (entry_u - spot))
        stop_p = _floor(mid + delta * (stop_u - spot))
        target_p = _floor(mid + delta * (target_u - spot))
        basis = f"delta approximation (Δ {delta:+.2f}; no IV on this contract)"
    else:
        return None

    risk_per_contract = (entry_p - stop_p) * 100.0
    reward_per_contract = (target_p - entry_p) * 100.0
    rr = round(reward_per_contract / risk_per_contract, 2) if risk_per_contract > 0 else None

    # A long option is only a sensible vehicle when the move outruns theta:
    # if the repriced target premium isn't above entry, the contract loses
    # even when the pattern works. Callers should try a longer expiry.
    viable = reward_per_contract > 0

    return {
        "viable": viable,
        "contract_ticker": contract.get("ticker"),
        "type": opt_type,
        "strike": float(strike),
        "expiration": contract.get("expiration"),
        "dte": int(dte),
        "iv_pct": round(iv * 100, 1) if iv else None,
        "delta": round(delta, 2) if delta is not None else None,
        "current_mid": round(mid, 2),
        "entry_premium": round(entry_p, 2),
        "stop_premium": round(stop_p, 2),
        "target_premium": round(target_p, 2),
        "risk_per_contract": round(risk_per_contract, 2),
        "reward_per_contract": round(reward_per_contract, 2),
        # From the ROUNDED entry premium so the card's numbers agree.
        "max_loss_per_contract": round(round(entry_p, 2) * 100.0, 2),
        "risk_reward": rr,
        "pricing_basis": basis,
    }
