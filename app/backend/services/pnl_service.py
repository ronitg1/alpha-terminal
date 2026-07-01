"""P&L position store + pure math.

Persists every tracked position — manual entries, one-click "Track this"
ideas from the screeners, and imported Fidelity fills — to
``app/data/pnl_positions.json``. Writes are atomic (temp file +
``os.replace``) and thread-safe, mirroring ``portfolio_settings_service``.

A position record:

    {
        "id": "pos_ab12cd34",
        "kind": "option" | "stock",
        "ticker": "NVDA",                  # underlying symbol
        "side": "long" | "short",
        "qty": 2.0,                        # contracts (options) or shares
        "option": {                        # null for stock positions
            "type": "call" | "put",
            "strike": 200.0,
            "expiration": "2026-07-17",
            "contract_ticker": "O:NVDA..." | null
        },
        "entry_price": 5.40,               # per share (option premium/share)
        "entry_date": "2026-06-10" | null,
        "status": "open" | "closed",
        "exit_price": null | float,        # per share
        "exit_date": null | "YYYY-MM-DD",
        "source": "manual" | "screener" | "pattern" | "fidelity",
        "real": false,                     # true = actual fill, false = paper idea
        "notes": "",
        "import_key": null | str,          # dedupe key for CSV re-imports
        "created_at": iso, "updated_at": iso
    }

Money math convention: ``entry_price``/``exit_price``/marks are always
PER SHARE. Options carry a 100x contract multiplier applied by
:func:`position_multiplier`, never baked into stored prices.

Storage backend: when ``STORAGE_BACKEND=db`` the PERSISTENCE functions (get_all,
create, update, delete, bulk_insert, existing_import_keys) dispatch to
:class:`PnlRepository` (Postgres). Id generation, timestamps, validation, and
all the P&L math below stay here — only the read/write target changes. Position
dict shapes are preserved. Default ``file`` — local behavior unchanged. See
:mod:`app.backend.services._storage`.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.backend.repositories.pnl_repository import PnlRepository
from app.backend.services._storage import current_user_id, session_scope, use_db

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "pnl_positions.json"
_lock = threading.Lock()

VALID_KINDS = {"option", "stock"}
VALID_SIDES = {"long", "short"}
VALID_STATUS = {"open", "closed"}
VALID_SOURCES = {"manual", "screener", "pattern", "fidelity"}


# ─── Persistence ─────────────────────────────────────────────────────────────


def _load() -> list[dict[str, Any]]:
    if not _DATA_PATH.exists():
        return []
    try:
        with _DATA_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("pnl store unreadable (%s) — starting empty", exc)
        return []


def _save(positions: list[dict[str, Any]]) -> None:
    _DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pnl.", suffix=".tmp", dir=str(_DATA_PATH.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(positions, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, _DATA_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─── Pure math ───────────────────────────────────────────────────────────────


def position_multiplier(position: dict[str, Any]) -> float:
    """Contract multiplier: 100 per option contract, 1 per share."""
    return 100.0 if position.get("kind") == "option" else 1.0


def _direction(position: dict[str, Any]) -> float:
    return 1.0 if position.get("side", "long") == "long" else -1.0


def realized_pnl(position: dict[str, Any]) -> float | None:
    """Dollar P&L for a closed position; None while open or unpriced."""
    if position.get("status") != "closed":
        return None
    entry = position.get("entry_price")
    exit_ = position.get("exit_price")
    if entry is None or exit_ is None:
        return None
    qty = float(position.get("qty") or 0)
    return (float(exit_) - float(entry)) * qty * position_multiplier(position) * _direction(position)


def unrealized_pnl(position: dict[str, Any], mark: float | None) -> float | None:
    """Dollar P&L for an open position at ``mark`` (per share); None if unmarkable."""
    if position.get("status") != "open" or mark is None:
        return None
    entry = position.get("entry_price")
    if entry is None:
        return None
    qty = float(position.get("qty") or 0)
    return (float(mark) - float(entry)) * qty * position_multiplier(position) * _direction(position)


def cost_basis(position: dict[str, Any]) -> float | None:
    """Total dollars at entry (always positive)."""
    entry = position.get("entry_price")
    if entry is None:
        return None
    return abs(float(entry) * float(position.get("qty") or 0) * position_multiplier(position))


# Paper-trading simulated account: everyone starts with this much buying power.
# Cash is DERIVED from the positions (opening pays the premium, closing returns
# proceeds), so there's no separate mutable balance to keep in sync.
DEFAULT_STARTING_CASH = 100_000.0


def account_snapshot(
    positions: list[dict[str, Any]],
    marks: dict[str, float | None] | None = None,
    starting_cash: float = DEFAULT_STARTING_CASH,
) -> dict[str, Any]:
    """Simulated paper-trading account state derived from the positions.

    Cash flow per position: opening debits ``entry × qty × mult × dir`` (a long
    pays premium, a short receives it); closing credits ``exit × …``. So cash =
    starting − Σ(entry cash flow, all) + Σ(exit cash flow, closed), buying power =
    cash, and equity = cash + market value of open positions. ``total_pnl`` equals
    realized + unrealized by construction."""
    marks = marks or {}
    open_cf = close_cf = pos_value = realized = unrealized = 0.0
    for p in positions:
        entry = p.get("entry_price")
        if entry is None:
            continue
        qty = float(p.get("qty") or 0)
        mult = position_multiplier(p)
        d = _direction(p)
        open_cf -= float(entry) * qty * mult * d
        if p.get("status") == "closed":
            exit_ = p.get("exit_price")
            if exit_ is not None:
                close_cf += float(exit_) * qty * mult * d
                realized += (float(exit_) - float(entry)) * qty * mult * d
        else:
            mark = marks.get(p["id"])
            if mark is not None:
                pos_value += float(mark) * qty * mult * d
                unrealized += (float(mark) - float(entry)) * qty * mult * d
    cash = starting_cash + open_cf + close_cf
    equity = cash + pos_value
    sharpe = realized_sharpe(positions, starting_cash=starting_cash)
    return {
        "starting_cash": round(starting_cash, 2),
        "cash": round(cash, 2),
        "buying_power": round(cash, 2),
        "positions_value": round(pos_value, 2),
        "equity": round(equity, 2),
        "realized": round(realized, 2),
        "unrealized": round(unrealized, 2),
        "total_pnl": round(equity - starting_cash, 2),
        "total_pnl_pct": round((equity - starting_cash) / starting_cash * 100, 2) if starting_cash else None,
        "sharpe": sharpe["sharpe"] if sharpe else None,
        "sharpe_days": sharpe["days"] if sharpe else None,
    }


# Gates below which an annualized Sharpe off the realized curve is noise, not
# signal: fewer trades than this, or a shorter span, returns None ("needs more
# history" in the UI) instead of a wild number off two lucky closes.
MIN_SHARPE_TRADE_DATES = 5
MIN_SHARPE_SPAN_DAYS = 30


def realized_sharpe(
    positions: list[dict[str, Any]],
    *,
    starting_cash: float = DEFAULT_STARTING_CASH,
    rf_annual: float = 0.045,
) -> dict[str, Any] | None:
    """Annualized Sharpe of the paper account's REALIZED equity curve, or None.

    No daily account marks are stored, so this walks the closed trades: equity on
    any weekday = starting cash + realized P&L closed through that day, with flat
    (zero-return) weekdays between trades kept in the series — the realized book
    genuinely was flat then. Unrealized P&L is invisible to it, so it understates
    swings while positions are open; it is labeled approximate in the UI.
    """
    pnl_by_day: dict[date, float] = {}
    for p in positions:
        if p.get("status") != "closed" or not p.get("exit_date"):
            continue
        r = realized_pnl(p)
        if r is None:
            continue
        try:
            day = date.fromisoformat(str(p["exit_date"])[:10])
        except ValueError:
            continue
        pnl_by_day[day] = pnl_by_day.get(day, 0.0) + r
    if len(pnl_by_day) < MIN_SHARPE_TRADE_DATES or starting_cash <= 0:
        return None
    first, last = min(pnl_by_day), max(pnl_by_day)
    if (last - first).days < MIN_SHARPE_SPAN_DAYS:
        return None

    # Daily returns over the weekday grid from the day before the first close.
    returns: list[float] = []
    equity = starting_cash
    day = first
    while day <= last:
        if day.weekday() < 5 or day in pnl_by_day:  # weekend closes still count
            prev = equity
            equity += pnl_by_day.get(day, 0.0)
            if prev > 0:
                returns.append(equity / prev - 1)
        day += timedelta(days=1)

    from app.backend.services.portfolio_stats import sharpe_from_daily_returns

    stats = sharpe_from_daily_returns(returns, rf_annual=rf_annual, min_days=MIN_SHARPE_SPAN_DAYS)
    if stats is None:
        return None
    return {"sharpe": stats["sharpe"], "days": len(returns)}


def instrument_key(position: dict[str, Any]) -> str:
    """Identity of the tradable instrument — used to match closes to opens."""
    if position.get("kind") == "option" and position.get("option"):
        o = position["option"]
        return f"{position['ticker']}:{o.get('type')}:{o.get('strike')}:{o.get('expiration')}"
    return f"{position['ticker']}:stock"


# ─── CRUD ────────────────────────────────────────────────────────────────────


def get_all() -> list[dict[str, Any]]:
    if use_db():
        with session_scope() as db:
            return PnlRepository(db, current_user_id()).get_all()
    with _lock:
        return _load()


def create(fields: dict[str, Any]) -> dict[str, Any]:
    """Insert a new position. Caller is responsible for field validation
    (the route layer uses Pydantic); this enforces only the invariants."""
    record = {
        "id": f"pos_{uuid.uuid4().hex[:8]}",
        "kind": fields["kind"],
        "ticker": fields["ticker"].upper(),
        "side": fields.get("side", "long"),
        "qty": float(fields["qty"]),
        "option": fields.get("option"),
        "entry_price": float(fields["entry_price"]),
        "entry_date": fields.get("entry_date"),
        "status": fields.get("status", "open"),
        "exit_price": fields.get("exit_price"),
        "exit_date": fields.get("exit_date"),
        "source": fields.get("source", "manual"),
        "real": bool(fields.get("real", False)),
        "notes": fields.get("notes", ""),
        "import_key": fields.get("import_key"),
        # Include closing_import_key (None for a fresh open) so a created record
        # has the same full key set the DB backend / importer produce — no
        # shape divergence between backends.
        "closing_import_key": fields.get("closing_import_key"),
        "created_at": _now(),
        "updated_at": _now(),
    }
    if record["kind"] not in VALID_KINDS:
        raise ValueError(f"kind must be one of {sorted(VALID_KINDS)}")
    if record["side"] not in VALID_SIDES:
        raise ValueError(f"side must be one of {sorted(VALID_SIDES)}")
    if record["kind"] == "option" and not record["option"]:
        raise ValueError("option positions need an option leg (type/strike/expiration)")
    if record["qty"] <= 0:
        raise ValueError("qty must be positive (use side='short' for shorts)")
    if use_db():
        with session_scope() as db:
            return PnlRepository(db, current_user_id()).insert(record)
    with _lock:
        positions = _load()
        positions.append(record)
        _save(positions)
    return record


def update(position_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    """Patch mutable fields. Returns the updated record or None if not found."""
    allowed = {
        "qty", "entry_price", "entry_date", "notes", "real",
        "status", "exit_price", "exit_date", "side",
    }
    # Same filter both backends: only the allowed, non-None fields, plus a fresh
    # updated_at stamp.
    patch = {k: v for k, v in fields.items() if k in allowed and v is not None}
    patch["updated_at"] = _now()
    if use_db():
        with session_scope() as db:
            return PnlRepository(db, current_user_id()).update(position_id, patch)
    with _lock:
        positions = _load()
        for p in positions:
            if p["id"] == position_id:
                p.update(patch)
                _save(positions)
                return p
    return None


def close(position_id: str, exit_price: float, exit_date: str | None = None) -> dict[str, Any] | None:
    """Close a position at ``exit_price`` (per share)."""
    return update(
        position_id,
        {
            "status": "closed",
            "exit_price": float(exit_price),
            "exit_date": exit_date or datetime.now(timezone.utc).date().isoformat(),
        },
    )


def delete(position_id: str) -> bool:
    if use_db():
        with session_scope() as db:
            return PnlRepository(db, current_user_id()).delete(position_id)
    with _lock:
        positions = _load()
        kept = [p for p in positions if p["id"] != position_id]
        if len(kept) == len(positions):
            return False
        _save(kept)
    return True


def clear_all() -> int:
    """Remove every position for the current user (paper-trading 'reset'). Returns
    the number removed."""
    if use_db():
        with session_scope() as db:
            repo = PnlRepository(db, current_user_id())
            existing = repo.get_all()
            for p in existing:
                repo.delete(p["id"])
            return len(existing)
    with _lock:
        positions = _load()
        _save([])
        return len(positions)


def existing_import_keys() -> set[str]:
    """Import-dedupe support: every fingerprint currently stored.

    Includes ``closing_import_key`` — set when a closing fill consumed an
    open record — so re-importing the same activity file doesn't replay
    the close as a spurious standalone trade.
    """
    if use_db():
        with session_scope() as db:
            return PnlRepository(db, current_user_id()).existing_import_keys()
    with _lock:
        keys: set[str] = set()
        for p in _load():
            if p.get("import_key"):
                keys.add(p["import_key"])
            if p.get("closing_import_key"):
                keys.add(p["closing_import_key"])
        return keys


def bulk_insert(records: list[dict[str, Any]]) -> int:
    """Insert pre-validated records (used by the Fidelity importer)."""
    if use_db():
        with session_scope() as db:
            return PnlRepository(db, current_user_id()).bulk_insert(records)
    with _lock:
        positions = _load()
        positions.extend(records)
        _save(positions)
    return len(records)


# ─── Summary ─────────────────────────────────────────────────────────────────


def summarize(
    positions: list[dict[str, Any]],
    marks: dict[str, float | None] | None = None,
) -> dict[str, Any]:
    """Aggregate P&L across positions.

    ``marks`` maps position id → per-share mark for open positions; pass
    None (or omit ids) to summarize realized-only.
    """
    marks = marks or {}
    open_pos = [p for p in positions if p.get("status") == "open"]
    closed_pos = [p for p in positions if p.get("status") == "closed"]

    realized = [r for r in (realized_pnl(p) for p in closed_pos) if r is not None]
    unrealized = [
        u for u in (unrealized_pnl(p, marks.get(p["id"])) for p in open_pos) if u is not None
    ]
    wins = sum(1 for r in realized if r > 0)

    # Per-underlying rollup (realized + unrealized).
    by_underlying: dict[str, dict[str, float]] = {}
    for p in closed_pos:
        r = realized_pnl(p)
        if r is None:
            continue
        row = by_underlying.setdefault(p["ticker"], {"realized": 0.0, "unrealized": 0.0})
        row["realized"] += r
    for p in open_pos:
        u = unrealized_pnl(p, marks.get(p["id"]))
        if u is None:
            continue
        row = by_underlying.setdefault(p["ticker"], {"realized": 0.0, "unrealized": 0.0})
        row["unrealized"] += u

    # Equity curve: cumulative realized P&L by exit date.
    curve: list[dict[str, Any]] = []
    cum = 0.0
    dated = sorted(
        (p for p in closed_pos if p.get("exit_date") and realized_pnl(p) is not None),
        key=lambda p: p["exit_date"],
    )
    for p in dated:
        cum += realized_pnl(p)  # type: ignore[arg-type]  (filtered above)
        curve.append({"date": p["exit_date"], "cum_realized": round(cum, 2)})

    return {
        "n_open": len(open_pos),
        "n_closed": len(closed_pos),
        "realized_total": round(sum(realized), 2),
        "unrealized_total": round(sum(unrealized), 2),
        "n_wins": wins,
        "n_losses": len(realized) - wins,
        "win_rate": round(wins / len(realized) * 100, 1) if realized else None,
        "by_underlying": {
            k: {"realized": round(v["realized"], 2), "unrealized": round(v["unrealized"], 2)}
            for k, v in sorted(by_underlying.items())
        },
        "equity_curve": curve,
    }
