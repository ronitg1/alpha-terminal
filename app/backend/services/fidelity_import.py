"""Fidelity CSV → P&L position importer.

Accepts either of Fidelity's two retail CSV exports and maps rows into
:mod:`pnl_service` records tagged ``source="fidelity"``, ``real=True``:

* **Positions** (Accounts & Trade → Positions → download): one row per
  holding. Detected by a ``Symbol`` + ``Quantity`` + cost-basis header.
  Creates OPEN positions; entry price derives from ``Cost Basis Total``
  (unambiguous dollars) falling back to ``Average Cost Basis``.
* **Transactions / Activity** (Activity & Orders → download): one row per
  execution. Detected by a ``Run Date`` + ``Action`` header. Opening fills
  create open positions; closing fills FIFO-match prior opens (full or
  partial) and produce closed records.

Option symbols arrive in Fidelity's compact format ``-NVDA260717C200`` /
``-NVDA260717P202.5`` (leading dash = option). Stock rows are plain symbols.

Fidelity files carry preamble lines above the header and disclaimer
footers below the data — the parser sniffs for the real header row and
ignores anything that doesn't parse. Re-imports are idempotent: every
imported row carries an ``import_key`` fingerprint and rows whose key is
already stored are skipped.

The raw CSV is never persisted; only parsed positions land in
``app/data/`` (gitignored).
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from typing import Any

from app.backend.services import pnl_service

logger = logging.getLogger(__name__)

# -NVDA260717C200  /  NVDA260717P202.5
_OPTION_SYMBOL_RE = re.compile(r"^-?\s*([A-Z]+)(\d{6})([CP])([\d.]+)$")
_STOCK_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def _parse_option_symbol(symbol: str) -> dict[str, Any] | None:
    """Decode Fidelity's compact option symbol; None if not an option."""
    m = _OPTION_SYMBOL_RE.match(symbol.strip().upper())
    if not m:
        return None
    underlying, yymmdd, cp, strike = m.groups()
    return {
        "ticker": underlying,
        "type": "call" if cp == "C" else "put",
        "strike": float(strike),
        "expiration": f"20{yymmdd[0:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}",
    }


def _to_float(value: str | None) -> float | None:
    """Parse Fidelity's number formats: '$1,234.56', '(123.45)' = negative."""
    if value is None:
        return None
    s = str(value).strip().replace("$", "").replace(",", "")
    if not s or s in {"--", "-", "n/a", "N/A"}:
        return None
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    try:
        out = float(s)
    except ValueError:
        return None
    return -out if negative else out


def _import_key(*parts: Any) -> str:
    """Stable fingerprint for dedupe across re-imports."""
    blob = "|".join(str(p) for p in parts)
    return "fid_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _sniff_rows(text: str) -> tuple[str, list[dict[str, str]]]:
    """Locate the real header line and DictRead the data under it.

    Returns ("positions" | "transactions", rows). Raises ValueError when
    neither flavor's header is present.
    """
    lines = text.splitlines()
    header_idx: int | None = None
    flavor = ""
    for i, line in enumerate(lines):
        cells = [c.strip().strip('"') for c in line.split(",")]
        lowered = [c.lower() for c in cells]
        if "run date" in lowered and any("action" in c for c in lowered):
            header_idx, flavor = i, "transactions"
            break
        if "symbol" in lowered and any("quantity" in c for c in lowered) and any(
            "cost basis" in c or "last price" in c for c in lowered
        ):
            header_idx, flavor = i, "positions"
            break
    if header_idx is None:
        raise ValueError(
            "Not a recognizable Fidelity CSV — expected a Positions export "
            "(Symbol/Quantity/Cost Basis columns) or an Activity export "
            "(Run Date/Action columns)."
        )
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    rows = []
    for raw in reader:
        # Normalize keys; Fidelity headers vary slightly across exports.
        row = { (k or "").strip().lower(): (v or "").strip() for k, v in raw.items() }
        rows.append(row)
    return flavor, rows


def _get(row: dict[str, str], *candidates: str) -> str:
    """First non-empty value among loosely-matched column names."""
    for cand in candidates:
        for key, value in row.items():
            if cand in key and value:
                return value
    return ""


# ─── Positions-export mapping ────────────────────────────────────────────────


def _positions_to_records(rows: list[dict[str, str]], known_keys: set[str]) -> tuple[list[dict], int]:
    records: list[dict] = []
    skipped = 0
    for row in rows:
        symbol = _get(row, "symbol")
        qty = _to_float(_get(row, "quantity"))
        if not symbol or qty is None or qty == 0:
            continue  # disclaimers, cash rows ("SPAXX"-style rows have qty, pass through)
        symbol = symbol.strip().upper()
        if symbol in {"PENDING ACTIVITY", "ACCOUNT TOTAL"}:
            continue

        opt = _parse_option_symbol(symbol)
        if opt is None and not _STOCK_SYMBOL_RE.match(symbol):
            continue

        side = "long" if qty > 0 else "short"
        abs_qty = abs(qty)
        mult = 100.0 if opt else 1.0

        cost_total = _to_float(_get(row, "cost basis total"))
        avg_basis = _to_float(_get(row, "average cost basis", "cost basis per share"))
        if cost_total is not None and abs_qty > 0:
            entry_price = abs(cost_total) / (abs_qty * mult)
        elif avg_basis is not None:
            # Average cost basis is per share for stocks; Fidelity reports it
            # per share for options too in this export.
            entry_price = abs(avg_basis)
        else:
            skipped += 1
            continue

        key = _import_key("pos", symbol, abs_qty, round(entry_price, 4))
        if key in known_keys:
            skipped += 1
            continue
        known_keys.add(key)

        records.append({
            "id": f"pos_{key[4:12]}",
            "kind": "option" if opt else "stock",
            "ticker": opt["ticker"] if opt else symbol,
            "side": side,
            "qty": abs_qty,
            "option": (
                {"type": opt["type"], "strike": opt["strike"],
                 "expiration": opt["expiration"], "contract_ticker": None}
                if opt else None
            ),
            "entry_price": round(entry_price, 4),
            "entry_date": None,  # positions export doesn't carry open dates
            "status": "open",
            "exit_price": None,
            "exit_date": None,
            "source": "fidelity",
            "real": True,
            "notes": "Imported from Fidelity positions CSV",
            "import_key": key,
            "created_at": pnl_service._now(),
            "updated_at": pnl_service._now(),
        })
    return records, skipped


# ─── Transactions-export mapping ─────────────────────────────────────────────


def _normalize_date(raw: str) -> str | None:
    """Fidelity 'Run Date' is MM/DD/YYYY."""
    raw = raw.strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", raw)
    if m:
        mm, dd, yyyy = m.groups()
        return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    return None


def _transactions_to_records(
    rows: list[dict[str, str]], known_keys: set[str]
) -> tuple[list[dict], int]:
    """Opening fills → open positions; closing fills FIFO-close them."""
    records: list[dict] = []
    skipped = 0

    # Oldest-first so FIFO matching is chronological.
    def _row_date(row: dict[str, str]) -> str:
        return _normalize_date(_get(row, "run date")) or "9999-99-99"

    for row in sorted(rows, key=_row_date):
        action = _get(row, "action").upper()
        symbol = _get(row, "symbol").strip().upper()
        qty = _to_float(_get(row, "quantity"))
        price = _to_float(_get(row, "price"))
        run_date = _normalize_date(_get(row, "run date"))
        if not action or not symbol or not qty or price is None or run_date is None:
            continue
        if "BOUGHT" not in action and "SOLD" not in action:
            continue  # dividends, transfers, interest — out of scope

        opt = _parse_option_symbol(symbol)
        if opt is None and not _STOCK_SYMBOL_RE.match(symbol):
            continue

        abs_qty = abs(qty)
        is_opening = "OPENING" in action or (
            "OPENING" not in action and "CLOSING" not in action and "BOUGHT" in action
        )
        # Equity shorts ("SOLD SHORT") open; plain "SOLD" on stock closes.
        if "SOLD SHORT" in action:
            is_opening = True

        key = _import_key("txn", run_date, action, symbol, abs_qty, price)
        if key in known_keys:
            skipped += 1
            continue
        known_keys.add(key)

        instrument = {
            "kind": "option" if opt else "stock",
            "ticker": opt["ticker"] if opt else symbol,
            "option": (
                {"type": opt["type"], "strike": opt["strike"],
                 "expiration": opt["expiration"], "contract_ticker": None}
                if opt else None
            ),
        }

        if is_opening:
            side = "short" if "SOLD" in action else "long"
            records.append({
                "id": f"pos_{key[4:12]}",
                **instrument,
                "side": side,
                "qty": abs_qty,
                "entry_price": abs(price),
                "entry_date": run_date,
                "status": "open",
                "exit_price": None,
                "exit_date": None,
                "source": "fidelity",
                "real": True,
                "notes": "Imported from Fidelity activity CSV",
                "import_key": key,
                "created_at": pnl_service._now(),
                "updated_at": pnl_service._now(),
            })
            continue

        # Closing fill: FIFO against open records from this same import batch.
        ikey = pnl_service.instrument_key({**instrument, "ticker": instrument["ticker"]})
        remaining = abs_qty
        for open_rec in records:
            if remaining <= 0:
                break
            if open_rec["status"] != "open":
                continue
            if pnl_service.instrument_key(open_rec) != ikey:
                continue
            closeable = min(remaining, open_rec["qty"])
            if closeable >= open_rec["qty"]:
                open_rec["status"] = "closed"
                open_rec["exit_price"] = abs(price)
                open_rec["exit_date"] = run_date
                # Remember the closing fill's fingerprint so a re-import of
                # the same file doesn't replay this close.
                open_rec["closing_import_key"] = key
            else:
                # Partial close: split off a closed slice.
                open_rec["qty"] = open_rec["qty"] - closeable
                records.append({
                    **open_rec,
                    "id": f"pos_{_import_key('split', key, closeable)[4:12]}",
                    "qty": closeable,
                    "status": "closed",
                    "exit_price": abs(price),
                    "exit_date": run_date,
                    "import_key": _import_key("split", key, closeable),
                    "closing_import_key": key,
                })
            remaining -= closeable
        if remaining > 0:
            # Close with no matching open in this file (position predates the
            # export window). Record it as a standalone closed trade with an
            # unknown entry so the realized number isn't silently dropped —
            # entry_price falls back to exit (P&L 0) with a loud note.
            records.append({
                "id": f"pos_{key[4:12]}",
                **instrument,
                "side": "long" if "SOLD" in action else "short",
                "qty": remaining,
                "entry_price": abs(price),
                "entry_date": None,
                "status": "closed",
                "exit_price": abs(price),
                "exit_date": run_date,
                "source": "fidelity",
                "real": True,
                "notes": "Closing fill without a matching open in this file — entry unknown, P&L recorded as 0. Edit the entry price to fix.",
                "import_key": key,
                "created_at": pnl_service._now(),
                "updated_at": pnl_service._now(),
            })
    return records, skipped


# ─── Entry point ─────────────────────────────────────────────────────────────


def import_csv(text: str) -> dict[str, Any]:
    """Parse a Fidelity CSV export and persist new positions.

    Returns ``{"flavor", "imported", "skipped", "positions"}``. Raises
    ValueError on unrecognizable files.
    """
    flavor, rows = _sniff_rows(text)
    known = pnl_service.existing_import_keys()
    if flavor == "positions":
        records, skipped = _positions_to_records(rows, known)
    else:
        records, skipped = _transactions_to_records(rows, known)
    pnl_service.bulk_insert(records)
    logger.info("Fidelity import (%s): %d imported, %d skipped", flavor, len(records), skipped)
    return {
        "flavor": flavor,
        "imported": len(records),
        "skipped": skipped,
        "positions": records,
    }
