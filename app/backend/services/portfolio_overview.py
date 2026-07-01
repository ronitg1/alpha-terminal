"""Unified portfolio overview across connected brokerages.

Merges whatever the current user has connected — SnapTrade (Fidelity et al.) and/or
Robinhood — into one list of accounts plus an "All combined" aggregate, enriches
each holding with a live quote (last price + today's change), and computes the
display metrics the Portfolio tab needs: current value, day change, total gain,
% of account, and cost basis.

Connection rules (per product decision 2026-07-01): include both if both are
connected, one if only one is, and neither → the caller shows a "connect a
brokerage" prompt (``connected: False``). A failure in one source never blocks the
other — each is fetched independently and its errors are logged, not raised.

Deliberately NOT here yet (later milestones): 52-week range, market indices,
market movers, earnings — ``week52_low``/``week52_high`` are emitted as ``None``
so the frontend column exists but fills in when M3 lands.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.backend.services import snaptrade_connection_service, snaptrade_service
from app.backend.services.api_key_validation import ROBINHOOD
from app.backend.services.key_resolver import resolve_key
from app.backend.services.portfolio_classify import OTHER, bucket_for, instant_bucket
from app.backend.services.snaptrade_client import snaptrade_configured

logger = logging.getLogger(__name__)

__all__ = ["build_overview"]


def _round(value: float | None, places: int = 2) -> float | None:
    return round(value, places) if value is not None else None


def _pct(part: float | None, whole: float | None) -> float | None:
    if part is None or not whole:
        return None
    return round(part / whole * 100, 2)


def _enrich_position(pos: dict[str, Any], quote: dict[str, Any] | None) -> dict[str, Any]:
    """Turn a normalized SnapTrade/Robinhood position into the display shape,
    layering in today's change from the underlying's quote when available."""
    kind = pos.get("kind", "stock")
    symbol = pos.get("symbol") or pos.get("underlying") or ""
    underlying = pos.get("underlying") or symbol
    qty = pos.get("units")
    last = pos.get("price")
    prev_close = None
    # Prefer the broker-provided security name (SnapTrade sends it); fall back to
    # the Polygon quote's name only if the broker didn't supply one.
    name = pos.get("name") or None
    # A quote is for the underlying stock, so only apply its price to a stock
    # position — never override an option's premium with the underlying's price.
    if quote and kind == "stock":
        if not name:
            name = quote.get("name") or None
        if quote.get("last") is not None:
            last = quote.get("last")
        prev_close = quote.get("prev_close")

    if kind == "stock" and qty is not None and last is not None:
        current_value = qty * last  # prefer the fresh quote for stocks
    else:
        current_value = pos.get("market_value")
        if current_value is None and qty is not None and last is not None:
            current_value = qty * last * (100 if kind == "option" else 1)

    # Today's change: only meaningful for stocks (we quote the underlying, not the
    # option contract). Options carry no intraday change here.
    day_change = None
    day_change_pct = None
    if kind == "stock" and qty is not None and last is not None and prev_close:
        day_change = (last - prev_close) * qty
        day_change_pct = _pct(last - prev_close, prev_close)

    # Total gain/loss = today's value − what you paid (avg cost × qty, ×100 for
    # option contracts). We compute this ourselves rather than trusting SnapTrade's
    # ``open_pnl``, which it reports inconsistently for options and produced absurd
    # values. Null when we lack an average cost, so the UI shows "—" not a bogus
    # number.
    cost_basis = pos.get("cost_basis")
    total_gain = None
    if current_value is not None and cost_basis is not None:
        total_gain = current_value - cost_basis
    total_gain_pct = _pct(total_gain, cost_basis)

    return {
        "symbol": symbol,
        "underlying": underlying,
        "name": name,
        "kind": kind,
        "quantity": _round(qty, 4),
        "last_price": _round(last),
        "day_change": _round(day_change),
        "day_change_pct": day_change_pct,
        "current_value": _round(current_value),
        "pct_of_account": None,  # filled once the account total is known
        "avg_cost": _round(pos.get("avg_cost")),
        "cost_basis_total": _round(cost_basis),
        "total_gain": _round(total_gain),
        "total_gain_pct": total_gain_pct,
        "sector": None,  # allocation bucket, filled in build_overview
        "week52_low": None,   # M3
        "week52_high": None,  # M3
        "option_type": pos.get("option_type"),
        "strike": pos.get("strike"),
        "expiration": pos.get("expiration"),
    }


def _finalize_account(
    account_id: str,
    label: str,
    source: str,
    institution: str | None,
    positions: list[dict[str, Any]],
    *,
    total_balance: float | None = None,
    cash: float | None = None,
) -> dict[str, Any]:
    """Compute account totals and each position's % of account.

    Cash is a *residual*, not a raw field: brokers report a total account value
    (positions + cash), so cash = total − invested. Pass ``total_balance`` (the
    broker's total) and cash is derived; or pass ``cash`` directly (e.g. Robinhood,
    where we have cash but no clean total) and the total is invested + cash. This is
    the fix for cash reading absurdly high — the old code mistook the total balance
    for cash."""
    invested = sum(p["current_value"] for p in positions if p["current_value"] is not None)
    if total_balance is not None:
        # Cash is the residual (total − invested). The broker's total can lag our
        # quote-marked position values (or omit an account), which would make the
        # residual negative — nonsensical for a cash figure. So the total is never
        # less than the positions shown, and cash is floored at zero.
        total_value = max(total_balance, invested)
        cash = total_value - invested
    elif cash is not None:
        total_value = invested + cash
    else:
        total_value = invested
    day_change = sum(p["day_change"] for p in positions if p["day_change"] is not None)
    cost_basis = sum(p["cost_basis_total"] for p in positions if p["cost_basis_total"] is not None)
    total_gain = sum(p["total_gain"] for p in positions if p["total_gain"] is not None)

    for p in positions:
        p["pct_of_account"] = _pct(p["current_value"], total_value)

    prev_value = total_value - day_change
    return {
        "id": account_id,
        "label": label,
        "source": source,
        "institution": institution,
        "cash": _round(cash),
        "total_value": _round(total_value),
        "day_change": _round(day_change),
        "day_change_pct": _pct(day_change, prev_value),
        "total_gain": _round(total_gain),
        "total_gain_pct": _pct(total_gain, cost_basis),
        "positions": sorted(positions, key=lambda p: p["current_value"] or 0, reverse=True),
    }


def _combine(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate all accounts into one 'All combined' account, merging positions
    that share a symbol (units summed, values added, cost basis added)."""
    merged: dict[str, dict[str, Any]] = {}
    # The combined total is the sum of each account's own total (broker-authoritative
    # where available); cash then falls out as total − invested in _finalize_account.
    total_balance = sum(a["total_value"] for a in accounts if a["total_value"] is not None)
    for acct in accounts:
        for p in acct["positions"]:
            key = f"{p['kind']}:{p['symbol']}"
            if key not in merged:
                merged[key] = {**p}
            else:
                m = merged[key]
                for field in ("quantity", "current_value", "day_change", "cost_basis_total", "total_gain"):
                    if p[field] is not None:
                        m[field] = (m[field] or 0) + p[field]
    positions = list(merged.values())
    # Recompute per-position derived rates after merging.
    for p in positions:
        p["total_gain_pct"] = _pct(p["total_gain"], p["cost_basis_total"])
        p["day_change_pct"] = _pct(p["day_change"], (p["current_value"] or 0) - (p["day_change"] or 0))
    return _finalize_account(
        "__combined__", "All accounts", "combined", None, positions, total_balance=total_balance
    )


# OCC contract symbol -> (fetched_at_monotonic, (change_per_share, change_pct) | None).
_OPTION_CHANGE_TTL = 120.0
_option_change_cache: dict[str, tuple[float, tuple[float, float] | None]] = {}


def _occ_ticker(underlying: str | None, expiration: str | None, option_type: str | None, strike: Any) -> str | None:
    """Polygon option contract symbol, e.g. ``O:MSFT261218C00440000``. None if any
    field is missing/unparseable."""
    if not (underlying and expiration and option_type and strike is not None):
        return None
    try:
        exp = str(expiration).replace("-", "")[2:]  # YYMMDD
        cp = "C" if str(option_type).upper().startswith("C") else "P"
        strike_millis = int(round(float(strike) * 1000))
    except (ValueError, TypeError):
        return None
    return f"O:{underlying.upper()}{exp}{cp}{strike_millis:08d}"


def _fetch_option_day(underlying: str, occ: str) -> tuple[float, float] | None:
    """``(change_per_share, change_percent)`` today for one option contract, or
    None. Uses Polygon's option snapshot ``day`` block, which is **live during
    market hours and the last close when the market is shut** — exactly the
    "use live, fall back to close" behavior requested."""
    from src.tools.massive.client import MassiveClient

    try:
        data = MassiveClient(timeout=6).get_option_contract_snapshot(underlying, occ)
    except Exception as exc:  # noqa: BLE001 — best-effort; option may be illiquid/unlisted
        logger.debug("Option snapshot failed for %s: %s", occ, type(exc).__name__)
        return None
    results = data.get("results") if isinstance(data, dict) else None
    day = results.get("day") if isinstance(results, dict) else None
    if not isinstance(day, dict):
        return None
    change, change_pct = day.get("change"), day.get("change_percent")
    if change is None or change_pct is None:
        return None
    try:
        return float(change), float(change_pct)
    except (TypeError, ValueError):
        return None


async def _annotate_option_day_change(positions: list[dict[str, Any]]) -> None:
    """Fill today's $/% change for option positions from Polygon option bars — the
    underlying's quote can't price an option, so this is the only source. Cached
    with a short TTL; best-effort per contract."""
    wanted: dict[str, str] = {}  # occ -> underlying (dedupe)
    for p in positions:
        if p.get("kind") != "option":
            continue
        occ = _occ_ticker(p.get("underlying"), p.get("expiration"), p.get("option_type"), p.get("strike"))
        if occ and p.get("underlying"):
            wanted[occ] = p["underlying"]

    async def _one(occ: str, underlying: str) -> tuple[str, tuple[float, float] | None]:
        cached = _option_change_cache.get(occ)
        if cached and (time.monotonic() - cached[0]) < _OPTION_CHANGE_TTL:
            return occ, cached[1]
        res = await asyncio.to_thread(_fetch_option_day, underlying, occ)
        _option_change_cache[occ] = (time.monotonic(), res)
        return occ, res

    resolved = dict(await asyncio.gather(*[_one(o, u) for o, u in wanted.items()])) if wanted else {}
    for p in positions:
        if p.get("kind") != "option":
            continue
        occ = _occ_ticker(p.get("underlying"), p.get("expiration"), p.get("option_type"), p.get("strike"))
        day = resolved.get(occ) if occ else None
        units = p.get("quantity")
        if not day or units is None:
            continue
        change_per_share, change_pct = day
        p["day_change"] = _round(change_per_share * units * 100)
        p["day_change_pct"] = _round(change_pct)


async def _snaptrade_accounts() -> list[dict[str, Any]]:
    """SnapTrade accounts as raw (pre-enrichment) position bundles, or [] when not
    connected. Errors are logged and swallowed so the other source still loads."""
    if not (snaptrade_configured() and snaptrade_connection_service.get_status()):
        return []
    try:
        payload = await asyncio.to_thread(snaptrade_service.fetch_portfolio)
    except Exception as exc:  # noqa: BLE001 — one source failing must not blank the page
        logger.warning("SnapTrade overview fetch failed: %s", type(exc).__name__)
        return []
    out: list[dict[str, Any]] = []
    for acct in payload.get("accounts", []):
        out.append(
            {
                "id": f"snaptrade:{acct['id']}",
                "label": acct.get("label") or "Account",
                "source": "snaptrade",
                "institution": acct.get("institution"),
                "total_balance": acct.get("total_balance"),
                "raw_positions": list(acct.get("positions", [])) + list(acct.get("options", [])),
            }
        )
    return out


async def _robinhood_accounts() -> list[dict[str, Any]]:
    """Robinhood as a single account, best-effort. Its MCP payload is untyped, so
    parsing is defensive; on any problem we log and return []."""
    if not resolve_key(ROBINHOOD):
        return []
    try:
        from app.backend.services.robinhood_mcp import fetch_portfolio as rh_fetch

        payload = await rh_fetch()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Robinhood overview fetch failed: %s", type(exc).__name__)
        return []
    positions = _parse_robinhood_positions(payload)
    if not positions:
        return []
    return [
        {
            "id": "robinhood:account",
            "label": "Robinhood",
            "source": "robinhood",
            "institution": "Robinhood",
            "cash": _parse_robinhood_cash(payload),
            "raw_positions": positions,
        }
    ]


def _parse_robinhood_positions(payload: Any) -> list[dict[str, Any]]:
    """Best-effort extraction of stock positions from the Robinhood MCP blob into
    the normalized shape ``_enrich_position`` expects. Unknown shapes yield []."""
    out: list[dict[str, Any]] = []
    for row in _iter_robinhood_rows(payload):
        symbol = str(row.get("symbol") or row.get("ticker") or row.get("instrument") or "").strip().upper()
        if not symbol:
            continue
        qty = _num(row.get("quantity") or row.get("units") or row.get("shares"))
        price = _num(row.get("price") or row.get("last_price") or row.get("current_price"))
        avg = _num(row.get("average_buy_price") or row.get("avg_cost") or row.get("average_price"))
        out.append(
            {
                "kind": "stock",
                "symbol": symbol,
                "underlying": symbol,
                "name": row.get("name") or row.get("instrument_name") or row.get("description"),
                "units": qty,
                "price": price,
                "avg_cost": avg,
                "cost_basis": (qty * avg) if (qty is not None and avg is not None) else None,
                "market_value": (qty * price) if (qty is not None and price is not None) else None,
                "open_pnl": None,
            }
        )
    return out


def _iter_robinhood_rows(payload: Any) -> list[dict[str, Any]]:
    """Walk the MCP tool payload for the first list of position-like dicts."""
    rows: list[dict[str, Any]] = []
    tools = payload.get("tools") if isinstance(payload, dict) else None
    for tool in tools or []:
        data = tool.get("data") if isinstance(tool, dict) else None
        rows.extend(_find_position_list(data))
    return rows


def _find_position_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict) and (r.get("symbol") or r.get("ticker"))]
    if isinstance(data, dict):
        for key in ("positions", "holdings", "results"):
            found = _find_position_list(data.get(key))
            if found:
                return found
    return []


def _parse_robinhood_cash(payload: Any) -> float | None:
    for tool in (payload.get("tools") if isinstance(payload, dict) else None) or []:
        data = tool.get("data") if isinstance(tool, dict) else None
        if isinstance(data, dict):
            for key in ("cash", "buying_power", "cash_balance"):
                value = _num(data.get(key))
                if value is not None:
                    return value
    return None


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def build_overview() -> dict[str, Any]:
    """Assemble the current user's cross-brokerage portfolio overview."""
    raw_snaptrade, raw_robinhood = await asyncio.gather(_snaptrade_accounts(), _robinhood_accounts())
    raw_accounts = raw_snaptrade + raw_robinhood

    sources = sorted({a["source"] for a in raw_accounts})
    if not raw_accounts:
        return {"connected": False, "sources": [], "accounts": [], "combined": None}

    # One batched quote fetch for every underlying across all accounts.
    symbols = sorted({p.get("underlying") or p.get("symbol") for a in raw_accounts for p in a["raw_positions"] if (p.get("underlying") or p.get("symbol"))})
    quotes = await _fetch_quotes(symbols)

    enriched_by_account: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    all_positions: list[dict[str, Any]] = []
    for a in raw_accounts:
        enriched = [_enrich_position(p, quotes.get(p.get("underlying") or p.get("symbol"))) for p in a["raw_positions"]]
        enriched_by_account.append((a, enriched))
        all_positions.extend(enriched)

    # Annotate positions BEFORE finalizing accounts, so the account totals (esp.
    # today's change) include options' day change. Best-effort + time-boxed: never
    # let these external lookups hang the response.
    try:
        await asyncio.wait_for(_annotate_sectors(all_positions), timeout=8.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sector annotation skipped: %s", type(exc).__name__)
    try:
        await asyncio.wait_for(_annotate_option_day_change(all_positions), timeout=8.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Option day-change annotation skipped: %s", type(exc).__name__)

    accounts = [
        _finalize_account(
            a["id"], a["label"], a["source"], a["institution"], enriched,
            total_balance=a.get("total_balance"), cash=a.get("cash"),
        )
        for a, enriched in enriched_by_account
    ]
    combined = _combine(accounts) if len(accounts) > 1 else None
    return {"connected": True, "sources": sources, "accounts": accounts, "combined": combined}


async def _annotate_sectors(positions: list[dict[str, Any]]) -> None:
    """Tag every position with its allocation bucket (Cash / Market Index / sector),
    classified by underlying so an option and its shares share a bucket. Lookups
    are cached and best-effort; anything unresolved falls back to "Other"."""
    # Pass 1 — instant (no I/O). Runs before any await and mutates positions in
    # place, so cash/index/curated-map buckets survive even if the Finnhub pass
    # below times out and gets cancelled (that was the "everything is Other" bug).
    need: dict[str, str | None] = {}
    for p in positions:
        u = p.get("underlying")
        if not u:
            p["sector"] = OTHER
            continue
        fast = instant_bucket(u, name=p.get("name"))
        if fast is not None:
            p["sector"] = fast
        else:
            need.setdefault(u, p.get("name"))
    if not need:
        return

    # Pass 2 — Finnhub for the unknown tail only (time-boxed by the caller).
    async def _one(sym: str, name: str | None) -> tuple[str, str]:
        return sym, await asyncio.to_thread(bucket_for, sym, name=name)

    resolved = dict(await asyncio.gather(*[_one(u, n) for u, n in need.items()]))
    for p in positions:
        if p.get("sector") is None:
            p["sector"] = resolved.get(p.get("underlying")) or OTHER


async def _fetch_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Batch quotes via the sleeves quotes endpoint (cached, time-boxed). Returns
    {} on failure so the overview still renders without today's-change data."""
    if not symbols:
        return {}
    try:
        from app.backend.routes.sleeves import get_quotes

        payload = await get_quotes(tickers=",".join(symbols))
        return payload.get("quotes", {}) if isinstance(payload, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Overview quote enrichment failed: %s", type(exc).__name__)
        return {}
