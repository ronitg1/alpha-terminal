"""Black-Scholes proxy pricing for the options-strategy backtest.

We deliberately do **not** look up historical option contract aggregates for
the backtest. Constructing valid Polygon option tickers (correct expiry,
correct strike rounding) per backtest day is brittle and gappy — moneyness
straddles often miss the closest-actual-listed strike, and weekly expiries
don't always exist. A Black-Scholes proxy using realized vol from the
underlying gives a deterministic, intelligible P&L that's *good enough* to
rank strategies. Document this assumption prominently in the UI.

Pricing assumes:
- European-style exercise (good for short-dated index/equity options).
- Constant risk-free rate (0.0434, matching BacktestService).
- No dividends (acceptable for short-dated mega-tech options).
- Volatility = trailing realized vol of the underlying, annualized.

If a real historical-chain integration ships later, callers should treat
this module as a fallback rather than removing it — short-dated weeklies
often have stale/illiquid quotes that BSM still prices sensibly.
"""
from __future__ import annotations

import math
from typing import Literal

# Matches BacktestService._update_performance_metrics — keep in sync.
RISK_FREE_RATE = 0.0434
TRADING_DAYS_PER_YEAR = 252


def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via erf — no scipy import needed."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bsm_price(
    *,
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    sigma: float,
    option_type: Literal["call", "put"],
    risk_free: float = RISK_FREE_RATE,
) -> float:
    """Closed-form European Black-Scholes price.

    Degenerate-input behavior:
    - ``time_to_expiry_years <= 0`` → intrinsic value (max(S-K, 0) / max(K-S, 0)).
    - ``sigma <= 0`` → discounted intrinsic (forward intrinsic discounted at r).

    Returns a price in the same units as spot / strike (typically dollars
    per share — premium per contract is 100× this in US markets, but the
    backtest tracks per-share P&L so we don't multiply here).
    """
    if time_to_expiry_years <= 0:
        return max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)
    if sigma <= 0:
        # No vol → option is just forward intrinsic, discounted.
        forward = spot * math.exp(risk_free * time_to_expiry_years)
        intrinsic = (
            max(0.0, forward - strike) if option_type == "call" else max(0.0, strike - forward)
        )
        return math.exp(-risk_free * time_to_expiry_years) * intrinsic

    sqrt_t = math.sqrt(time_to_expiry_years)
    d1 = (math.log(spot / strike) + (risk_free + 0.5 * sigma * sigma) * time_to_expiry_years) / (
        sigma * sqrt_t
    )
    d2 = d1 - sigma * sqrt_t
    discount = math.exp(-risk_free * time_to_expiry_years)

    if option_type == "call":
        return spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
    return strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def realized_vol(closes: list[float], *, window: int = 30) -> float | None:
    """Annualized realized vol from log returns over the trailing ``window``
    bars. Returns ``None`` if there isn't enough history.

    Uses log returns (not arithmetic) because BSM is parameterized on
    log-normal vol — matching the model.
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
    return math.sqrt(var) * math.sqrt(TRADING_DAYS_PER_YEAR)


def straddle_price(
    *,
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    sigma: float,
    risk_free: float = RISK_FREE_RATE,
) -> float:
    """ATM-ish straddle = call + put at the same strike. Convenience wrapper
    so callers don't have to call ``bsm_price`` twice."""
    call = bsm_price(
        spot=spot,
        strike=strike,
        time_to_expiry_years=time_to_expiry_years,
        sigma=sigma,
        option_type="call",
        risk_free=risk_free,
    )
    put = bsm_price(
        spot=spot,
        strike=strike,
        time_to_expiry_years=time_to_expiry_years,
        sigma=sigma,
        option_type="put",
        risk_free=risk_free,
    )
    return call + put
