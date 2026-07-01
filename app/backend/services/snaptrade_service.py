"""SnapTrade orchestration — the routes-facing layer over the signed client and
the per-user connection store.

Responsibilities:
- :func:`connect_url` — register the user with SnapTrade on first use (persisting
  the issued secret) and return a one-time connection-portal URL.
- :func:`fetch_portfolio` — read every connected account's stock and option
  positions and normalize them to a stable, display-ready shape.
- :func:`disconnect` — forget the local connection.

Normalization keeps the useful fields (symbol, units, price, market value, and —
for options — the underlying, type, strike, expiry) and preserves the untouched
SnapTrade object under ``raw`` for a drill view. :func:`underlying_of` is the
single source of truth for collapsing a position to its underlying ticker; the
Phase B auto-portfolio sync reuses it so shares and options net to one symbol.
"""
from __future__ import annotations

import logging
from typing import Any

from app.backend.services import snaptrade_client as client
from app.backend.services import snaptrade_connection_service as store
from app.backend.services._storage import current_user_id

logger = logging.getLogger(__name__)

__all__ = [
    "connect_url",
    "fetch_portfolio",
    "disconnect",
    "connection_status",
    "underlying_of",
    "normalize_stock_position",
    "normalize_option_position",
]


def connection_status() -> dict[str, Any]:
    """Whether the server is configured and whether *this* user is connected."""
    status = store.get_status()
    return {
        "configured": client.snaptrade_configured(),
        "connected": bool(status),
        "connection": status,
    }


def connect_url(custom_redirect: str | None = None) -> str:
    """Ensure the current user is registered with SnapTrade, then return a
    one-time connection-portal URL for them to link a brokerage."""
    creds = store.get_credentials()
    if creds is None:
        snaptrade_user_id = current_user_id()
        secret = client.register_user(snaptrade_user_id)
        store.save(snaptrade_user_id, secret)
        creds = (snaptrade_user_id, secret)
    snaptrade_user_id, user_secret = creds
    return client.login_portal_url(snaptrade_user_id, user_secret, custom_redirect=custom_redirect)


def disconnect() -> bool:
    """Forget the current user's local connection. Does not delete the SnapTrade
    user (their linked accounts persist and can be re-read after reconnecting)."""
    return store.delete()


# ─── normalization ───────────────────────────────────────────────────────────

def _symbol_text(value: Any) -> str:
    """Pull a ticker string out of SnapTrade's nested symbol objects, which vary
    in shape by endpoint. Falls back through the common nestings."""
    if isinstance(value, str):
        return value.strip().upper()
    if not isinstance(value, dict):
        return ""
    # positions: {"symbol": {"symbol": {"symbol": "NVDA"}}} or {"symbol": {"symbol": "NVDA"}}
    for key in ("symbol", "raw_symbol", "ticker"):
        inner = value.get(key)
        text = _symbol_text(inner)
        if text:
            return text
    return ""


def underlying_of(position: dict[str, Any]) -> str:
    """The underlying ticker for a position — the stock symbol, or an option's
    underlying. Returns "" if it can't be determined. Single source of truth used
    by both the positions view and the Phase B portfolio sync."""
    symbol = position.get("symbol")
    if isinstance(symbol, dict):
        option_symbol = symbol.get("option_symbol")
        if isinstance(option_symbol, dict):
            underlying = _symbol_text(option_symbol.get("underlying_symbol"))
            if underlying:
                return underlying
    # option holdings sometimes carry the option object at the top level
    option_symbol = position.get("option_symbol")
    if isinstance(option_symbol, dict):
        underlying = _symbol_text(option_symbol.get("underlying_symbol"))
        if underlying:
            return underlying
    return _symbol_text(symbol)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_stock_position(position: dict[str, Any]) -> dict[str, Any]:
    ticker = underlying_of(position)
    units = _as_float(position.get("units")) or _as_float(position.get("fractional_units"))
    price = _as_float(position.get("price"))
    avg_cost = _as_float(position.get("average_purchase_price"))
    market_value = (units * price) if (units is not None and price is not None) else None
    cost_basis = (units * avg_cost) if (units is not None and avg_cost is not None) else None
    return {
        "kind": "stock",
        "symbol": ticker,
        "underlying": ticker,
        "units": units,
        "price": price,
        "avg_cost": avg_cost,
        "cost_basis": cost_basis,
        "market_value": market_value,
        "open_pnl": _as_float(position.get("open_pnl")),
        "raw": position,
    }


def normalize_option_position(position: dict[str, Any]) -> dict[str, Any]:
    underlying = underlying_of(position)
    symbol_obj = position.get("symbol") if isinstance(position.get("symbol"), dict) else {}
    option_symbol = symbol_obj.get("option_symbol") if isinstance(symbol_obj, dict) else None
    if not isinstance(option_symbol, dict):
        option_symbol = position.get("option_symbol") if isinstance(position.get("option_symbol"), dict) else {}
    units = _as_float(position.get("units"))
    price = _as_float(position.get("price"))
    # SnapTrade quirk: for options it returns the LAST PRICE per share but the
    # AVERAGE COST per CONTRACT (total premium, already ×100). Convert avg cost to
    # per-share so both are per-share and cost basis uses the same ×100 as value —
    # otherwise cost basis is inflated 100× (a $17 option showed a $325k basis).
    avg_cost_per_contract = _as_float(position.get("average_purchase_price"))
    avg_cost = (avg_cost_per_contract / 100) if avg_cost_per_contract is not None else None
    # option contracts are 100 shares; SnapTrade's last price is per share.
    market_value = (units * price * 100) if (units is not None and price is not None) else None
    cost_basis = (units * avg_cost * 100) if (units is not None and avg_cost is not None) else None
    return {
        "kind": "option",
        "symbol": _symbol_text(option_symbol.get("ticker")) or underlying,
        "underlying": underlying,
        "option_type": (option_symbol.get("option_type") or "").upper() or None,
        "strike": _as_float(option_symbol.get("strike_price")),
        "expiration": option_symbol.get("expiration_date"),
        "units": units,
        "price": price,
        "avg_cost": avg_cost,
        "cost_basis": cost_basis,
        "market_value": market_value,
        "open_pnl": _as_float(position.get("open_pnl")),
        "raw": position,
    }


def _account_label(account: dict[str, Any]) -> str:
    name = account.get("name") or account.get("institution_name") or "Account"
    number = account.get("number")
    return f"{name} ({number})" if number else str(name)


def _account_total_balance(account: dict[str, Any]) -> float | None:
    """Best-effort TOTAL account value (positions + cash) from a SnapTrade account
    object. This is the broker-authoritative total; the overview derives cash from
    it as total − invested. NOTE: ``balance.total.amount`` is the *total*, not cash
    — treating it as cash was the bug behind an absurdly high cash figure."""
    for path in (("balance", "total", "amount"), ("total_value",), ("balance", "total")):
        node: Any = account
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        value = _as_float(node)
        if value is not None:
            return value
    return None


def fetch_portfolio() -> dict[str, Any]:
    """Read all connected accounts with normalized stock + option positions.

    Raises :class:`SnapTradeError` subclasses on API failure and ``LookupError``
    when the user has no stored connection (the route maps it to a clean 400)."""
    creds = store.get_credentials()
    if creds is None:
        raise LookupError("No SnapTrade connection for this user.")
    snaptrade_user_id, user_secret = creds

    accounts = client.list_accounts(snaptrade_user_id, user_secret)
    out_accounts: list[dict[str, Any]] = []
    for account in accounts:
        account_id = str(account.get("id") or "")
        if not account_id:
            continue
        stocks = client.list_positions(snaptrade_user_id, user_secret, account_id)
        options = client.list_option_holdings(snaptrade_user_id, user_secret, account_id)
        out_accounts.append(
            {
                "id": account_id,
                "label": _account_label(account),
                "name": account.get("name"),
                "institution": account.get("institution_name"),
                "number": account.get("number"),
                "total_balance": _account_total_balance(account),
                "positions": [normalize_stock_position(p) for p in stocks],
                "options": [normalize_option_position(p) for p in options],
            }
        )
    return {"status": "ok", "accounts": out_accounts}
