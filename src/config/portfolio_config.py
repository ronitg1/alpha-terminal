"""Portfolio sleeve definitions and consensus-scoring weights.

Each sleeve names a panel of agents (referring to keys from
:mod:`src.utils.analysts`) plus a per-agent weight used by the morning
scan to combine signals into a single ranking.

Invariants enforced by :func:`validate_portfolio` at import time so a bad
edit fails loudly:

* Per-sleeve agent weights sum to 1.0.
* Sleeve allocations don't *over*-allocate the book (total <= 100%).

Note: sleeve ``allocation_pct`` is informational — real capital allocation is
tracked per-ticker in the portfolio-settings overlay — so allocations are NOT
required to sum to exactly 100% (this is what lets sleeves be added/deleted
freely from the dashboard).

``CASH_RESERVE_PCT`` is a *runtime floor* — the morning scan / allocator
must refuse to size positions that would drop free cash below this level.
It is not subtracted from sleeve allocations.
"""
from __future__ import annotations

from typing import TypedDict


class Sleeve(TypedDict):
    """Type hint for a sleeve definition entry."""

    allocation_pct: float
    agents: list[str]
    agent_weights: dict[str, float]
    tickers: list[str]


# Runtime floor — the morning scan / allocator refuses to size positions
# that would drop free cash below this percentage of the book. Distinct
# from (informational) sleeve allocations.
CASH_RESERVE_PCT: float = 10.0


# Agent keys must match ANALYST_CONFIG keys in src/utils/analysts.py.
# The short-hand names "damodaran", "burry", "fundamentals" used in the
# project spec are mapped to their canonical registry keys here.
PORTFOLIO_SLEEVES: dict[str, Sleeve] = {
    "energy_transition": {
        "allocation_pct": 50.0,
        "agents": ["energy_transition", "aswath_damodaran", "michael_burry"],
        "agent_weights": {
            "energy_transition": 0.3333333333333333,
            "aswath_damodaran": 0.3333333333333333,
            "michael_burry": 0.33333333333333337,
        },
        "tickers": [
            "FSLR",
            "CSIQ",
            "JKS",
            "ARRY",
            "SEDG",
            "ENPH",
            "CHPT",
            "BLNK",
            "EVGO",
            "RUN",
            "STEM",
            "BE",
            "PLPC",
            "POWL",
            "VST",
            "CEG",
            "NEE",
            "AES",
        ],
    },
    "mega_tech": {
        "allocation_pct": 20.0,
        "agents": ["alpha_seeker", "aswath_damodaran", "fundamentals_analyst"],
        "agent_weights": {
            "alpha_seeker": 0.3333333333333333,
            "aswath_damodaran": 0.3333333333333333,
            "fundamentals_analyst": 0.33333333333333337,
        },
        "tickers": ["NVDA", "MSFT", "GOOGL", "META", "AAPL", "AMZN", "TSLA"],
    },
    "opportunistic": {
        "allocation_pct": 10.0,
        "agents": ["alpha_seeker", "stanley_druckenmiller", "charlie_munger", "aswath_damodaran", "ben_graham"],
        "agent_weights": {
            "alpha_seeker": 0.2,
            "stanley_druckenmiller": 0.2,
            "charlie_munger": 0.2,
            "aswath_damodaran": 0.2,
            "ben_graham": 0.19999999999999996,
        },
        "tickers": ["NBIS", "ASTS", "DELL", "MU", "NOW"],
    },
    "emerging_tech": {
        "allocation_pct": 20.0,
        "agents": ["emerging_tech", "alpha_seeker", "michael_burry"],
        "agent_weights": {
            "alpha_seeker": 0.3333333333333333,
            "michael_burry": 0.33333333333333337,
            "emerging_tech": 0.3333333333333333,
        },
        "tickers": [
            "ARM",
            "AVGO",
            "ALAB",
            "SMCI",
            "IONQ",
            "RGTI",
            "RKLB",
            "LUNR",
            "RXRX",
            "NVAX",
            "HOOD",
            "AFRM",
            "NU",
        ],
    },
}


class PortfolioConfigError(ValueError):
    """Raised when the portfolio config violates an invariant."""


def validate_portfolio(
    sleeves: dict[str, Sleeve] | None = None,
    cash_reserve_pct: float | None = None,
    *,
    tolerance: float = 1e-6,
) -> None:
    """Validate sleeve weights and allocations. Raises on any violation."""
    sleeves = sleeves if sleeves is not None else PORTFOLIO_SLEEVES
    cash_reserve_pct = cash_reserve_pct if cash_reserve_pct is not None else CASH_RESERVE_PCT

    # NOTE: sleeve ``allocation_pct`` is informational only — nothing in the
    # scan/agents/backtest computes against it, and real capital allocation is
    # tracked per-ticker in the portfolio-settings overlay. We therefore do NOT
    # require the cross-sleeve total to equal 100% (that rule blocked adding or
    # deleting sleeves via the dashboard). We only guard against over-allocating
    # the book beyond 100%.
    total_alloc = sum(s["allocation_pct"] for s in sleeves.values())
    if total_alloc > 100.0 + tolerance:
        raise PortfolioConfigError(
            f"Sleeve allocations cannot exceed 100%; got {total_alloc}%."
        )
    if not 0 <= cash_reserve_pct <= 100:
        raise PortfolioConfigError(
            f"CASH_RESERVE_PCT must be in [0, 100]; got {cash_reserve_pct}."
        )

    for name, sleeve in sleeves.items():
        # Every agent must have a weight, and weights must sum to 1.0.
        agent_set = set(sleeve["agents"])
        weight_keys = set(sleeve["agent_weights"].keys())
        if agent_set != weight_keys:
            raise PortfolioConfigError(
                f"Sleeve '{name}': agent list {sorted(agent_set)} "
                f"does not match agent_weights keys {sorted(weight_keys)}."
            )
        total_weight = sum(sleeve["agent_weights"].values())
        if abs(total_weight - 1.0) > tolerance:
            raise PortfolioConfigError(
                f"Sleeve '{name}': agent weights must sum to 1.0; got {total_weight}."
            )
        if sleeve["allocation_pct"] < 0:
            raise PortfolioConfigError(f"Sleeve '{name}': allocation_pct cannot be negative.")


def sleeve_for_ticker(ticker: str) -> str | None:
    """Return the sleeve name that contains ``ticker``, or None."""
    ticker = ticker.upper()
    for name, sleeve in PORTFOLIO_SLEEVES.items():
        if ticker in (t.upper() for t in sleeve["tickers"]):
            return name
    return None


# Fail fast at import — if someone edits the config and breaks an invariant,
# every script importing this module surfaces the error immediately.
validate_portfolio()
