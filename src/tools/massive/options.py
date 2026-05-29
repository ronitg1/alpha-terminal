"""Helpers for working with Massive's options snapshot endpoint.

Polygon's ``/v3/snapshot/options/{underlying}`` returns rich per-contract
rows nested under ``details``, ``last_quote``, ``last_trade``, ``greeks``,
and ``day``. This module flattens the subset of fields the dashboard
actually renders into a single dict per row, and groups the rows into
``calls`` / ``puts`` lists.

We intentionally do not surface every field Polygon ships — the dashboard
shows strike / last / bid / ask / IV / delta / volume / OI, and that's all
the chain viewer needs. Add fields here as the UI grows; agents that need
the raw rows can call ``MassiveClient.get_options_chain`` directly.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)


def flatten_contract(row: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the fields the chain viewer cares about into a flat dict.

    Returns ``None`` when the row is too malformed to be useful
    (missing strike, expiry, or contract type) so callers can filter
    cleanly.
    """
    details = row.get("details") or {}
    quote = row.get("last_quote") or {}
    trade = row.get("last_trade") or {}
    greeks = row.get("greeks") or {}
    day = row.get("day") or {}

    contract_type = details.get("contract_type")
    strike = details.get("strike_price")
    expiry = details.get("expiration_date")
    if contract_type is None or strike is None or expiry is None:
        return None

    return {
        "type": contract_type,                # "call" | "put"
        "ticker": details.get("ticker"),      # e.g. "O:MSFT260606C00470000"
        "strike": float(strike),
        "expiration": expiry,                 # YYYY-MM-DD
        "bid": quote.get("bid"),
        "ask": quote.get("ask"),
        "last": trade.get("price"),
        "iv": row.get("implied_volatility"),
        "delta": greeks.get("delta"),
        "gamma": greeks.get("gamma"),
        "theta": greeks.get("theta"),
        "vega": greeks.get("vega"),
        "volume": day.get("volume"),
        "open_interest": row.get("open_interest"),
    }


def split_calls_puts(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a chain into ``(calls, puts)``, each sorted by strike ascending.

    Skips contracts ``flatten_contract`` rejected. Sorting is on the flat
    row so the viewer can render in price order without re-sorting.
    """
    calls: list[dict[str, Any]] = []
    puts: list[dict[str, Any]] = []
    for raw in rows:
        flat = flatten_contract(raw)
        if flat is None:
            continue
        if flat["type"] == "call":
            calls.append(flat)
        elif flat["type"] == "put":
            puts.append(flat)
        else:
            logger.debug("Skipping contract with unknown type: %s", flat["type"])
    calls.sort(key=lambda c: c["strike"])
    puts.sort(key=lambda c: c["strike"])
    return calls, puts
