"""Portfolio sleeve definitions and consensus-scoring weights.

Each sleeve names a panel of agents (referring to keys from
:mod:`src.utils.analysts`) plus a per-agent weight used by the morning
scan to combine signals into a single ranking.

Two invariants enforced by :func:`validate_portfolio` at import time so
a bad edit fails loudly:

* Sleeve allocations sum to exactly 100% (the four sleeves cover the full
  notional book).
* Per-sleeve agent weights sum to 1.0.

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
# from sleeve allocations, which always sum to 100%.
CASH_RESERVE_PCT: float = 10.0


# Agent keys must match ANALYST_CONFIG keys in src/utils/analysts.py.
# The short-hand names "damodaran", "burry", "fundamentals" used in the
# project spec are mapped to their canonical registry keys here.
PORTFOLIO_SLEEVES: dict[str, Sleeve] = {
    "energy_transition": {
        "allocation_pct": 50.0,
        "agents": ["energy_transition", "aswath_damodaran", "michael_burry"],
        "agent_weights": {
            "energy_transition": 0.50,
            "aswath_damodaran": 0.30,
            "michael_burry": 0.20,
        },
        "tickers": [
            # ── solar ──
            "FSLR", "CSIQ", "JKS", "ARRY", "SEDG", "ENPH",
            # ── EV charging ──
            "CHPT", "BLNK", "EVGO",
            # ── residential storage ──
            "RUN", "NOVA", "SUNW",
            # ── C&I storage ──
            "STEM", "BE",
            # ── grid / utilities ──
            "PLPC", "POWL", "VST", "CEG", "NEE", "AES",
        ],
    },
    "mega_tech": {
        "allocation_pct": 20.0,
        "agents": ["alpha_seeker", "aswath_damodaran", "fundamentals_analyst"],
        "agent_weights": {
            "alpha_seeker": 0.40,
            "aswath_damodaran": 0.35,
            "fundamentals_analyst": 0.25,
        },
        "tickers": ["NVDA", "MSFT", "GOOGL", "META", "AAPL", "AMZN", "TSLA"],
    },
    "emerging_tech": {
        "allocation_pct": 20.0,
        "agents": ["emerging_tech", "alpha_seeker", "michael_burry"],
        "agent_weights": {
            "emerging_tech": 0.50,
            "alpha_seeker": 0.30,
            "michael_burry": 0.20,
        },
        "tickers": [
            # ── AI infra / semis ──
            "ARM", "AVGO", "ALAB", "SMCI",
            # ── quantum ──
            "IONQ", "RGTI",
            # ── space / defense tech ──
            "RKLB", "LUNR",
            # ── biotech / longevity ──
            "RXRX", "NVAX",
            # ── fintech ──
            "HOOD", "AFRM", "NU",
        ],
    },
    "opportunistic": {
        "allocation_pct": 10.0,
        "agents": ["alpha_seeker", "michael_burry"],
        "agent_weights": {
            "alpha_seeker": 0.60,
            "michael_burry": 0.40,
        },
        # Populated dynamically from src/config/watchlist.py or via the
        # --watchlist CLI flag. Intentionally empty at rest.
        "tickers": [],
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

    total_alloc = sum(s["allocation_pct"] for s in sleeves.values())
    if abs(total_alloc - 100.0) > tolerance:
        raise PortfolioConfigError(
            f"Sleeve allocations must sum to 100%; got {total_alloc}%."
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
