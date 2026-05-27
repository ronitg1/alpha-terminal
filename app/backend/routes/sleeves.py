"""Sleeves Dashboard API.

Phase 1 endpoints (read-only):

* ``GET  /sleeves/config``        — returns the ``PORTFOLIO_SLEEVES`` config + ``CASH_RESERVE_PCT``.
* ``GET  /sleeves/scans``         — lists past morning-scan CSVs in ``outputs/``.
* ``GET  /sleeves/scans/latest``  — parsed rows from the most recent scan.
* ``GET  /sleeves/scans/{date}``  — parsed rows from a specific date.

Phase 2 (live scan):

* ``POST /sleeves/scan/run``      — kicks off a morning scan and streams
  progress via Server-Sent Events. Event types: ``start``, ``progress``,
  ``sleeve_complete``, ``complete``, ``error``.

Phase 3 endpoints (``GET/PUT /sleeves/watchlist``) ship next.

All scan CSVs are produced by ``src/run_morning_scan.py`` and live under
``outputs/YYYY-MM-DD_morning_scan.csv``. Each row carries an aggregated
weighted_score and the per-agent verdicts as a serialized string, which we
parse back into a structured ``per_agent`` list for the UI.
"""
from __future__ import annotations

import asyncio
import csv
import datetime
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.backend.models.events import (
    BaseEvent,
    CompleteEvent,
    ErrorEvent,
    ProgressUpdateEvent,
    StartEvent,
)
from src.config.portfolio_config import CASH_RESERVE_PCT, PORTFOLIO_SLEEVES
from src.config.watchlist import get_watchlist
from src.run_morning_scan import (
    TickerRow,
    aggregate_verdicts,
    run_sleeve,
    write_csv,
)
from src.utils.progress import progress

# Load .env so agents can see DEEPSEEK_API_KEY etc. when invoked via the API.
# Safe to call repeatedly; later loads don't override already-set values.
load_dotenv()

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


# ─── /sleeves/scan/run (live SSE) ───────────────────────────────────────────


class ScanRequest(BaseModel):
    """Body for POST /sleeves/scan/run."""

    sleeves: list[str] | None = Field(
        default=None,
        description="Sleeve names to run. None = all configured sleeves.",
    )
    tickers: list[str] | None = Field(
        default=None,
        description=(
            "Filter each selected sleeve to its intersection with this list. "
            "None = use each sleeve's full ticker list."
        ),
    )
    include_watchlist: bool = Field(
        default=False,
        description="If true, opportunistic sleeve uses src/config/watchlist.py tickers.",
    )
    end_date: str | None = Field(
        default=None, description="End date for data fetches (YYYY-MM-DD). Default: today."
    )


class SleeveCompleteEvent(BaseEvent):
    """Emitted after one sleeve's rows are ready."""

    type: str = "sleeve_complete"
    sleeve: str
    rows: list[dict[str, Any]]


def _tickerrow_to_ui(r: TickerRow) -> dict[str, Any]:
    """Mirror the CSV-derived row shape used by ``_row_to_ui``, but from the
    in-memory dataclass.

    Includes each agent's raw output dict (variant_perception, catalysts,
    kill_switch, IRA/FEOC fields, etc.) so the UI can render the drill
    drawer without re-fetching. The CSV-only path loses this — that's
    documented and the drawer falls back to showing the basic fields.
    """
    per_agent = [
        {
            "agent": k,
            "signal": v.signal,
            "confidence": v.confidence,
            # raw is the full agent output dict (rich fields). Empty {} for
            # agents that didn't carry one.
            "raw": v.raw if isinstance(v.raw, dict) else {},
        }
        for k, v in r.verdicts.items()
    ]
    return {
        "ticker": r.ticker,
        "sleeve": r.sleeve,
        "consensus": r.consensus,
        "weighted_score": r.weighted_score,
        "avg_confidence": r.avg_confidence,
        "highlight": r.highlight,
        "position_type": r.position_type,
        "hold_period": r.hold_period,
        "has_variant_perception": r.has_variant_perception,
        "variant_perception": r.variant_perception_text,
        "per_agent": per_agent,
    }


def _resolve_selected_sleeves(req: ScanRequest) -> dict[str, dict[str, Any]]:
    """Return ``{name: sleeve}`` for the sleeves we're going to run.

    Applies sleeve filter, ticker filter, and watchlist override in a single
    pass so the caller sees a uniform shape. Each sleeve dict is a fresh
    copy — we never mutate the global PORTFOLIO_SLEEVES.
    """
    base = {
        name: dict(sleeve)
        for name, sleeve in PORTFOLIO_SLEEVES.items()
        if (req.sleeves is None) or (name in req.sleeves)
    }
    if not base:
        raise HTTPException(status_code=400, detail=f"No matching sleeves for {req.sleeves}.")

    # Watchlist injection BEFORE ticker filtering, so a --tickers filter still
    # excludes watchlist tickers it doesn't list.
    if req.include_watchlist and "opportunistic" in base:
        wl = get_watchlist()
        base["opportunistic"] = {**base["opportunistic"], "tickers": wl}

    if req.tickers:
        wanted = {t.strip().upper() for t in req.tickers if t.strip()}
        for name, sleeve in base.items():
            base[name] = {
                **sleeve,
                "tickers": [t for t in sleeve["tickers"] if t.upper() in wanted],
            }

    return base


@router.post(
    "/scan/run",
    responses={
        200: {"description": "Streaming SSE response"},
        400: {"description": "Invalid request"},
        500: {"description": "Internal server error"},
    },
)
async def run_scan(req: ScanRequest, request: Request):
    """Execute a morning scan, streaming progress + per-sleeve completion via SSE.

    Events:

    * ``start``           — fired immediately.
    * ``progress``        — one per ``progress.update_status(agent, ticker, status)``
                            call. Agent + ticker are present whenever the underlying
                            agent reports them.
    * ``sleeve_complete`` — fired after each sleeve finishes; payload has the
                            sleeve name and its aggregated rows so the UI can
                            update progressively.
    * ``complete``        — final payload: all rows + CSV path.
    * ``error``           — fatal failure during scan setup or aggregation. Per-agent
                            failures are caught inside ``run_sleeve`` and never
                            propagate to this event.
    """
    try:
        selected = _resolve_selected_sleeves(req)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Bad request: {exc}")

    end_date = req.end_date or datetime.date.today().isoformat()

    async def _detect_disconnect() -> bool:
        try:
            while True:
                msg = await request.receive()
                if msg["type"] == "http.disconnect":
                    return True
        except Exception:
            return True

    async def event_generator():
        progress_queue: asyncio.Queue[BaseEvent] = asyncio.Queue()
        scan_task: asyncio.Task | None = None
        disconnect_task: asyncio.Task | None = None
        all_rows: list[TickerRow] = []

        # Progress handler matches the signature used by src/utils/progress.py:
        #   handler(agent_name, ticker, status, analysis, timestamp)
        # Called from worker thread when an agent invokes progress.update_status.
        # asyncio.Queue.put_nowait is safe enough here (CPython deque append is
        # atomic and we have one producer / one consumer). The same pattern is
        # used by hedge_fund.py — keeping it consistent.
        def progress_handler(agent_name, ticker, status, analysis, timestamp):
            progress_queue.put_nowait(
                ProgressUpdateEvent(
                    agent=agent_name,
                    ticker=ticker,
                    status=status,
                    timestamp=timestamp,
                    analysis=analysis,
                )
            )

        async def _run_all() -> list[TickerRow]:
            """Run every selected sleeve sequentially. Each sleeve runs in a
            worker thread so the event loop stays responsive for SSE."""
            out: list[TickerRow] = []
            for name, sleeve in selected.items():
                if not sleeve["tickers"]:
                    logger.info("Skipping sleeve '%s' — no tickers after filtering.", name)
                    continue
                rows: list[TickerRow] = await asyncio.to_thread(
                    run_sleeve, name, sleeve, end_date, show_reasoning=False
                )
                out.extend(rows)
                # Emit per-sleeve completion so the UI fills in as we go.
                progress_queue.put_nowait(
                    SleeveCompleteEvent(
                        sleeve=name,
                        rows=[_tickerrow_to_ui(r) for r in rows],
                    )
                )
            return out

        progress.register_handler(progress_handler)
        try:
            scan_task = asyncio.create_task(_run_all())
            disconnect_task = asyncio.create_task(_detect_disconnect())

            yield StartEvent().to_sse()

            while not scan_task.done():
                if disconnect_task.done():
                    logger.info("Client disconnected, cancelling scan")
                    scan_task.cancel()
                    return

                try:
                    event = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                    yield event.to_sse()
                except asyncio.TimeoutError:
                    continue

            # Drain any queued events the scan emitted between the last poll
            # and task completion (sleeve_complete commonly lands here).
            while not progress_queue.empty():
                yield progress_queue.get_nowait().to_sse()

            try:
                all_rows = await scan_task
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.exception("Scan failed")
                yield ErrorEvent(message=f"Scan failed: {exc}").to_sse()
                return

            # Persist + emit final payload.
            try:
                csv_path = write_csv(all_rows, Path("outputs"), end_date)
                csv_path_str = str(csv_path).replace("\\", "/")
            except Exception as exc:
                logger.exception("Failed to write CSV")
                csv_path_str = ""

            yield CompleteEvent(
                data={
                    "date": end_date,
                    "row_count": len(all_rows),
                    "rows": [_tickerrow_to_ui(r) for r in all_rows],
                    "csv_path": csv_path_str,
                }
            ).to_sse()

        except asyncio.CancelledError:
            return
        finally:
            progress.unregister_handler(progress_handler)
            if scan_task and not scan_task.done():
                scan_task.cancel()
            if disconnect_task and not disconnect_task.done():
                disconnect_task.cancel()

    return StreamingResponse(event_generator(), media_type="text/event-stream")
