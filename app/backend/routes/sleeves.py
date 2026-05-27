"""Sleeves Dashboard API.

Phase 1 endpoints (read-only):

* ``GET  /sleeves/config``        — returns the ``PORTFOLIO_SLEEVES`` config + ``CASH_RESERVE_PCT``.
* ``GET  /sleeves/scans``         — lists past morning-scan CSVs in ``outputs/``.
* ``GET  /sleeves/scans/latest``  — parsed rows from the most recent scan.
* ``GET  /sleeves/scans/{date}``  — parsed rows from a specific date.

Phase 2/3 endpoints (``POST /sleeves/scan/run``, ``GET/PUT /sleeves/watchlist``)
are intentionally not implemented here yet — they ship in follow-up phases.

All scan CSVs are produced by ``src/run_morning_scan.py`` and live under
``outputs/YYYY-MM-DD_morning_scan.csv``. Each row carries an aggregated
weighted_score and the per-agent verdicts as a serialized string, which we
parse back into a structured ``per_agent`` list for the UI.
"""
from __future__ import annotations

import csv
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from src.config.portfolio_config import CASH_RESERVE_PCT, PORTFOLIO_SLEEVES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sleeves")

# Project root resolved relative to this file. app/backend/routes/sleeves.py
# is three directories deep from the project root.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_OUTPUTS_DIR = _PROJECT_ROOT / "outputs"

# Per-agent verdict format from the CSV's per_agent_signals column:
#   "alpha_seeker=neutral(20); aswath_damodaran=bullish(75); ..."
_PER_AGENT_RE = re.compile(r"\s*([a-zA-Z0-9_]+)\s*=\s*([a-z]+)\s*\(\s*(-?\d+(?:\.\d+)?)\s*\)")


# ─── /sleeves/config ────────────────────────────────────────────────────────


@router.get("/config")
async def get_config() -> dict[str, Any]:
    """Return sleeve definitions + cash-reserve floor.

    Lists are serialized verbatim from ``PORTFOLIO_SLEEVES``. The frontend
    treats this as the source of truth for sleeve membership, agent panels,
    and display weights.
    """
    sleeves = []
    for name, sleeve in PORTFOLIO_SLEEVES.items():
        sleeves.append(
            {
                "name": name,
                "allocation_pct": sleeve["allocation_pct"],
                "agents": list(sleeve["agents"]),
                "agent_weights": dict(sleeve["agent_weights"]),
                "tickers": list(sleeve["tickers"]),
            }
        )
    return {"sleeves": sleeves, "cash_reserve_pct": CASH_RESERVE_PCT}


# ─── /sleeves/scans ─────────────────────────────────────────────────────────


@router.get("/scans")
async def list_scans(limit: int = 30) -> dict[str, Any]:
    """List past scan files (newest first) with basic metadata."""
    files = _list_scan_files()
    out = []
    for path in files[:limit]:
        out.append(
            {
                "date": _date_from_path(path),
                "path": str(path.relative_to(_PROJECT_ROOT)).replace("\\", "/"),
                "size_bytes": path.stat().st_size,
            }
        )
    return {"scans": out}


@router.get("/scans/latest")
async def get_latest_scan() -> dict[str, Any]:
    """Return the most recent scan, fully parsed."""
    files = _list_scan_files()
    if not files:
        raise HTTPException(
            status_code=404,
            detail=(
                "No scans found in outputs/. Run `poetry run python -m src.run_morning_scan` "
                "(or use the Run Scan button when Phase 2 ships) to produce one."
            ),
        )
    return _read_scan_csv(files[0])


@router.get("/scans/{scan_date}")
async def get_scan_by_date(scan_date: str) -> dict[str, Any]:
    """Return the scan for a specific date (YYYY-MM-DD)."""
    try:
        date.fromisoformat(scan_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date '{scan_date}': {exc}")
    path = _OUTPUTS_DIR / f"{scan_date}_morning_scan.csv"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No scan for {scan_date}")
    return _read_scan_csv(path)


# ─── helpers ────────────────────────────────────────────────────────────────


def _list_scan_files() -> list[Path]:
    """Return all morning_scan CSVs in outputs/, sorted newest first by name.

    Filenames are ``YYYY-MM-DD_morning_scan.csv`` so reverse-sort on name
    is equivalent to reverse-sort on date — avoids touching mtimes.
    """
    if not _OUTPUTS_DIR.exists():
        return []
    return sorted(
        _OUTPUTS_DIR.glob("*_morning_scan.csv"),
        key=lambda p: p.name,
        reverse=True,
    )


def _date_from_path(path: Path) -> str:
    """Extract the YYYY-MM-DD prefix from a scan filename, or '' on mismatch."""
    name = path.name
    if len(name) >= 10 and name[4] == "-" and name[7] == "-":
        return name[:10]
    return ""


def _read_scan_csv(path: Path) -> dict[str, Any]:
    """Load a scan CSV and return a UI-shaped dict."""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            rows.append(_row_to_ui(raw))
    return {
        "date": _date_from_path(path),
        "path": str(path.relative_to(_PROJECT_ROOT)).replace("\\", "/"),
        "row_count": len(rows),
        "rows": rows,
    }


def _row_to_ui(raw: dict[str, str]) -> dict[str, Any]:
    """Normalize a CSV row to typed fields and a parsed per_agent list.

    The morning scan writes ``per_agent_signals`` as a semicolon-separated
    string like ``"alpha_seeker=neutral(20); aswath_damodaran=bullish(75)"``.
    We parse it into a list of ``{agent, signal, confidence}`` objects so the
    UI can render badges without doing string surgery in TypeScript.
    """
    per_agent = []
    raw_signals = raw.get("per_agent_signals") or ""
    for match in _PER_AGENT_RE.finditer(raw_signals):
        agent, signal, conf = match.groups()
        per_agent.append(
            {
                "agent": agent,
                "signal": signal,
                "confidence": float(conf),
            }
        )

    return {
        "ticker": raw.get("ticker", ""),
        "sleeve": raw.get("sleeve", ""),
        "consensus": raw.get("consensus", "neutral"),
        "weighted_score": _to_float(raw.get("weighted_score"), 0.0),
        "avg_confidence": _to_float(raw.get("avg_confidence"), 0.0),
        "highlight": raw.get("highlight", "neutral"),
        "position_type": raw.get("position_type", "no_position"),
        "hold_period": raw.get("hold_period", "n_a"),
        "has_variant_perception": _to_bool(raw.get("has_variant_perception")),
        "variant_perception": raw.get("variant_perception", "") or "",
        "per_agent": per_agent,
    }


def _to_float(s: str | None, default: float) -> float:
    try:
        return float(s) if s not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _to_bool(s: str | None) -> bool:
    return (s or "").strip().lower() in {"true", "1", "yes"}
