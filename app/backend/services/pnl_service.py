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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.backend.repositories.pnl_repository import PnlRepository
from app.backend.services._storage import DEFAULT_USER_ID, session_scope, use_db

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
            return PnlRepository(db, DEFAULT_USER_ID).get_all()
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
            return PnlRepository(db, DEFAULT_USER_ID).insert(record)
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
            return PnlRepository(db, DEFAULT_USER_ID).update(position_id, patch)
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
            return PnlRepository(db, DEFAULT_USER_ID).delete(position_id)
    with _lock:
        positions = _load()
        kept = [p for p in positions if p["id"] != position_id]
        if len(kept) == len(positions):
            return False
        _save(kept)
    return True


def existing_import_keys() -> set[str]:
    """Import-dedupe support: every fingerprint currently stored.

    Includes ``closing_import_key`` — set when a closing fill consumed an
    open record — so re-importing the same activity file doesn't replay
    the close as a spurious standalone trade.
    """
    if use_db():
        with session_scope() as db:
            return PnlRepository(db, DEFAULT_USER_ID).existing_import_keys()
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
            return PnlRepository(db, DEFAULT_USER_ID).bulk_insert(records)
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
