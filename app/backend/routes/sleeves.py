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
import math
import re
import time
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
from app.backend.services.watchlist_service import (
    read_watchlist_with_comments,
    write_watchlist,
)
from app.backend.services import watchlists_service
from app.backend.services import portfolio_settings_service
import src.config.portfolio_config as _portfolio_config_module
from src.config.portfolio_config import CASH_RESERVE_PCT  # noqa: F401  (re-read fresh below)
from src.patterns.patterns import BULLISH_PATTERNS, PATTERN_DETECTORS


def _live_sleeves() -> dict:
    """Always read the current PORTFOLIO_SLEEVES through the module attribute
    so importlib.reload (from the sleeve-config service) takes effect mid-process.

    A bare ``from ... import PORTFOLIO_SLEEVES`` binds the name at import time
    and survives reloads — that's what we're sidestepping here.
    """
    return _portfolio_config_module.PORTFOLIO_SLEEVES


def _live_cash_reserve() -> float:
    return _portfolio_config_module.CASH_RESERVE_PCT


# Backwards-compatible alias so legacy ``PORTFOLIO_SLEEVES`` references resolve.
# Each access goes through ``_live_sleeves()`` via __getattr__-style indirection,
# but Python module-level names can't lazy-load. So we keep this as a plain
# alias and rely on _live_sleeves() at every call site that needs freshness.
PORTFOLIO_SLEEVES = _portfolio_config_module.PORTFOLIO_SLEEVES
from src.config.watchlist import get_watchlist
from src.tools.api import get_financial_metrics
from src.tools.massive import (
    MassiveClient,
    MassiveError,
    convert_company_news,
    convert_prices,
)
from src.tools.massive.options import split_calls_puts
from src.run_morning_scan import (
    TickerRow,
    aggregate_verdicts,
    run_sleeve,
    write_csv,
)
from src.utils.analysts import get_agents_list
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


# ─── /sleeves/analysts ──────────────────────────────────────────────────────


@router.get("/analysts")
async def get_analysts() -> dict[str, Any]:
    """Return analyst metadata (display_name, description, investing_style)
    for every registered analyst.

    Sourced from ``src.utils.analysts.ANALYST_CONFIG`` via the existing
    ``get_agents_list()`` helper — no new computation, just expose what's
    already there so the UI can render tooltips on agent badges.
    """
    return {"analysts": get_agents_list()}


# ─── /sleeves/ticker/{ticker} ───────────────────────────────────────────────

# Per-ticker payload cache: ``{symbol: (monotonic_inserted_at, payload)}``.
# Drill drawer opens fire this endpoint; a short TTL keeps repeat opens cheap
# without holding stale data long enough to mislead. Bumped to ~1h if FDS or
# Massive credit burn becomes an issue (see plan §Risks).
_TICKER_CACHE_TTL_SECONDS = 300
_ticker_cache: dict[str, tuple[float, dict[str, Any]]] = {}


@router.get("/ticker/{ticker}")
async def get_ticker_data(ticker: str) -> dict[str, Any]:
    """90-day price history, latest TTM fundamentals, and top-5 recent news.

    Backs the drill drawer's sparkline / fundamentals card / news list so the
    drawer renders without needing a fresh agent scan.

    Sources (per HANDOFF.md gotcha #4):
    - Prices + news come from Massive (Polygon) — the user's plan covers both.
    - Fundamentals route via ``get_financial_metrics``, which respects
      ``DATA_PROVIDER`` (FDS in this env, since the Massive plan lacks the
      Financials & Ratios expansion).

    Each source is independently try/except'd — a failure in one leaves the
    others intact and the drawer renders around the gap. Results are cached
    per ticker for 5 minutes.
    """
    symbol = (ticker or "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Ticker is required.")

    now = time.monotonic()
    cached = _ticker_cache.get(symbol)
    if cached and (now - cached[0]) < _TICKER_CACHE_TTL_SECONDS:
        return cached[1]

    today = datetime.date.today()
    end_date = today.isoformat()
    # Two-year lookback so the frontend's interactive timeframe selector
    # (1W → 2Y) can slice client-side without an additional fetch. 2y of
    # daily bars per ticker ≈ 500 rows × ~80 bytes = ~40KB JSON; well within
    # the 5-min server cache budget. News window stays at 90d (older flow
    # isn't useful for the drill view).
    start_date = (today - datetime.timedelta(days=730)).isoformat()
    news_start = (today - datetime.timedelta(days=90)).isoformat()

    price_history: list[dict[str, Any]] = []
    try:
        client = MassiveClient()
        aggs = await asyncio.to_thread(
            client.get_daily_aggregates, symbol, start_date, end_date
        )
        price_history = [p.model_dump() for p in convert_prices(aggs)]
    except MassiveError as exc:
        logger.warning("Massive prices failed for %s: %s", symbol, exc)
    except Exception:
        logger.exception("Unexpected price fetch failure for %s", symbol)

    fundamentals: dict[str, Any] | None = None
    try:
        metrics = await asyncio.to_thread(
            get_financial_metrics, symbol, end_date, "ttm", 1
        )
        if metrics:
            fundamentals = metrics[0].model_dump()
    except Exception:
        logger.exception("Fundamentals fetch failed for %s", symbol)

    recent_news: list[dict[str, Any]] = []
    try:
        client = MassiveClient()
        news_response = await asyncio.to_thread(
            client.get_company_news,
            symbol,
            start_date=news_start,
            end_date=end_date,
            limit=5,
        )
        recent_news = [
            n.model_dump()
            for n in convert_company_news(news_response, ticker=symbol)
        ]
    except MassiveError as exc:
        logger.warning("Massive news failed for %s: %s", symbol, exc)
    except Exception:
        logger.exception("Unexpected news fetch failure for %s", symbol)

    # Company reference data — name, description, industry. Used by the
    # ticker-detail card to render a "what does this company do" overview.
    details: dict[str, Any] | None = None
    try:
        client = MassiveClient()
        ref = await asyncio.to_thread(client.get_ticker_details, symbol)
        results = (ref or {}).get("results") or {}
        if results:
            details = {
                "name": results.get("name"),
                "description": results.get("description"),
                "sic_description": results.get("sic_description"),
                "homepage_url": results.get("homepage_url"),
                "primary_exchange": results.get("primary_exchange"),
                "list_date": results.get("list_date"),
                "total_employees": results.get("total_employees"),
                "share_class_shares_outstanding": results.get(
                    "share_class_shares_outstanding"
                ),
                # Polygon publishes market_cap on the reference endpoint for
                # most tickers. Surface it so the frontend can fall back to
                # it when the FDS fundamentals call returns nothing (e.g.,
                # small/foreign listings outside FDS coverage). Frontend
                # uses fundamentals.market_cap first, this second.
                "market_cap": results.get("market_cap"),
                # Currency the reference values are denominated in. Polygon
                # reports CAD for Canadian Solar etc. — useful to label.
                "currency_name": results.get("currency_name"),
            }
    except MassiveError as exc:
        logger.warning("Massive ticker reference failed for %s: %s", symbol, exc)
    except Exception:
        logger.exception("Unexpected ticker-reference failure for %s", symbol)

    payload: dict[str, Any] = {
        "ticker": symbol,
        "price_history": price_history,
        "fundamentals": fundamentals,
        "recent_news": recent_news,
        "details": details,
    }
    _ticker_cache[symbol] = (now, payload)
    return payload


# Finnhub fundamentals enrichment cache (5 min) — separate from _ticker_cache so
# the two sources fail independently.
_finnhub_cache: dict[str, tuple[float, dict[str, Any]]] = {}


@router.get("/ticker/{ticker}/finnhub")
async def get_ticker_finnhub(ticker: str) -> dict[str, Any]:
    """Finnhub-sourced enrichment for the Market tab's financials section.

    Returns growth/turnover metrics, the earnings beat/miss track record,
    analyst recommendation consensus, peers, and recent insider flow. Forward
    analyst estimates are premium-gated on the free tier and intentionally
    omitted. When no FINNHUB_API_KEY is configured, returns
    ``{"configured": false}`` so the UI can hide the section gracefully.
    """
    symbol = (ticker or "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Ticker is required.")

    from src.tools.finnhub import get_finnhub_client
    from src.tools.finnhub.converters import fundamentals_summary

    client = get_finnhub_client()
    if client is None:
        return {"configured": False, "ticker": symbol}

    now = time.monotonic()
    cached = _finnhub_cache.get(symbol)
    if cached and (now - cached[0]) < _TICKER_CACHE_TTL_SECONDS:
        return cached[1]

    summary = await asyncio.to_thread(fundamentals_summary, client, symbol)
    payload = {"configured": True, **summary}
    _finnhub_cache[symbol] = (now, payload)
    return payload


@router.get("/config")
async def get_config() -> dict[str, Any]:
    """Return sleeve definitions + cash-reserve floor.

    Lists are serialized verbatim from ``PORTFOLIO_SLEEVES``. The frontend
    treats this as the source of truth for sleeve membership, agent panels,
    and display weights.
    """
    sleeves = []
    for name, sleeve in _live_sleeves().items():
        sleeves.append(
            {
                "name": name,
                "allocation_pct": sleeve["allocation_pct"],
                "agents": list(sleeve["agents"]),
                "agent_weights": dict(sleeve["agent_weights"]),
                "tickers": list(sleeve["tickers"]),
            }
        )
    return {"sleeves": sleeves, "cash_reserve_pct": _live_cash_reserve()}


# ─── /sleeves/config/sleeve — CRUD ──────────────────────────────────────────


class SleeveDefinition(BaseModel):
    """Body for create/update. Allocation must keep the total at 100%
    across all sleeves (validated server-side)."""

    allocation_pct: float = Field(ge=0, le=100, description="0..100, summed across sleeves must = 100.")
    agents: list[str] = Field(min_length=1, description="Canonical analyst keys (e.g. 'alpha_seeker').")
    agent_weights: dict[str, float] = Field(description="Per-agent weight; must sum to 1.0 and cover every agent.")
    tickers: list[str] = Field(default_factory=list, description="Uppercase tickers. May be empty (opportunistic-style).")


def _serialize_sleeves(snapshot: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Helper — turn the in-memory snapshot into the wire shape used by /config."""
    return [
        {
            "name": name,
            "allocation_pct": sleeve["allocation_pct"],
            "agents": list(sleeve["agents"]),
            "agent_weights": dict(sleeve["agent_weights"]),
            "tickers": list(sleeve["tickers"]),
        }
        for name, sleeve in snapshot.items()
    ]


class BulkSleevesPayload(BaseModel):
    """Body for atomic bulk replace. Wraps the dict so OpenAPI documents it
    properly (instead of an opaque ``Dict[str, Any]``)."""

    sleeves: dict[str, SleeveDefinition] = Field(
        description="Full sleeves dict — name → definition. Total allocation must sum to 100%.",
    )


@router.put("/config")
async def replace_all_sleeves_endpoint(payload: BulkSleevesPayload) -> dict[str, Any]:
    """Atomic bulk replace of the entire PORTFOLIO_SLEEVES dict.

    Use when an edit spans multiple sleeves (e.g. shrinking one to make room
    for another). One round-trip — no transient out-of-balance states.
    """
    from app.backend.services import sleeve_config_service

    raw = {name: defn.model_dump() for name, defn in payload.sleeves.items()}
    updated = sleeve_config_service.replace_all_sleeves(raw)
    return {"sleeves": _serialize_sleeves(updated), "cash_reserve_pct": _live_cash_reserve()}


@router.post("/config/sleeve/{name}")
async def create_sleeve_endpoint(name: str, payload: SleeveDefinition) -> dict[str, Any]:
    """Create a new sleeve. Returns the updated full config."""
    from app.backend.services import sleeve_config_service

    updated = sleeve_config_service.create_sleeve(name, payload.model_dump())
    return {"sleeves": _serialize_sleeves(updated), "cash_reserve_pct": _live_cash_reserve()}


@router.put("/config/sleeve/{name}")
async def update_sleeve_endpoint(name: str, payload: SleeveDefinition) -> dict[str, Any]:
    """Replace an existing sleeve's definition. Name in the URL is the target."""
    from app.backend.services import sleeve_config_service

    updated = sleeve_config_service.update_sleeve(name, payload.model_dump())
    return {"sleeves": _serialize_sleeves(updated), "cash_reserve_pct": _live_cash_reserve()}


@router.delete("/config/sleeve/{name}")
async def delete_sleeve_endpoint(name: str) -> dict[str, Any]:
    """Delete a sleeve. Returns the updated full config."""
    from app.backend.services import sleeve_config_service

    updated = sleeve_config_service.delete_sleeve(name)
    return {"sleeves": _serialize_sleeves(updated), "cash_reserve_pct": _live_cash_reserve()}


class RenameSleevePayload(BaseModel):
    """Body for rename. ``new_name`` must be a valid sleeve identifier."""

    new_name: str = Field(description="New sleeve name (lowercase, alphanumeric + underscore, max 31 chars).")


@router.patch("/config/sleeve/{name}/rename")
async def rename_sleeve_endpoint(name: str, payload: RenameSleevePayload) -> dict[str, Any]:
    """Rename a sleeve. Returns the updated full config."""
    from app.backend.services import sleeve_config_service

    updated = sleeve_config_service.rename_sleeve(name, payload.new_name)
    return {"sleeves": _serialize_sleeves(updated), "cash_reserve_pct": _live_cash_reserve()}


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
    """Return the most recent scan, fully parsed (JSON sidecar preferred)."""
    files = _list_scan_files()
    if not files:
        raise HTTPException(
            status_code=404,
            detail=(
                "No scans found in outputs/. Click Run Scan, or run "
                "`poetry run python -m src.run_morning_scan` from the CLI."
            ),
        )
    path = files[0]
    return _read_scan_json(path) if path.suffix == ".json" else _read_scan_csv(path)


@router.get("/scans/{scan_date}")
async def get_scan_by_date(scan_date: str) -> dict[str, Any]:
    """Return the scan for a specific date (YYYY-MM-DD). JSON sidecar preferred."""
    try:
        date.fromisoformat(scan_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date '{scan_date}': {exc}")
    json_path = _OUTPUTS_DIR / f"{scan_date}_morning_scan.json"
    csv_path = _OUTPUTS_DIR / f"{scan_date}_morning_scan.csv"
    if json_path.exists():
        return _read_scan_json(json_path)
    if csv_path.exists():
        return _read_scan_csv(csv_path)
    raise HTTPException(status_code=404, detail=f"No scan for {scan_date}")


# ─── helpers ────────────────────────────────────────────────────────────────


def _list_scan_files() -> list[Path]:
    """Return all morning_scan files in outputs/, sorted newest first by name.

    Filenames are ``YYYY-MM-DD_morning_scan.{csv,json}`` so reverse-sort on
    name is equivalent to reverse-sort on date. Each date may have a CSV
    (always written) plus an optional JSON sidecar (written when the scan
    runs through /sleeves/scan/run). Dedupe by date stem, preferring JSON.
    """
    if not _OUTPUTS_DIR.exists():
        return []
    by_date: dict[str, Path] = {}
    # Sort so CSV is seen first, then JSON overwrites it — JSON wins.
    for path in sorted(_OUTPUTS_DIR.glob("*_morning_scan.csv")):
        by_date[_date_from_path(path)] = path
    for path in sorted(_OUTPUTS_DIR.glob("*_morning_scan.json")):
        by_date[_date_from_path(path)] = path
    return sorted(by_date.values(), key=lambda p: p.name, reverse=True)


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


def _read_scan_json(path: Path) -> dict[str, Any]:
    """Load a scan JSON sidecar (full UI shape including agent raw fields).

    The JSON is written by /sleeves/scan/run alongside the CSV. Schema matches
    what /scans/latest returns from a live scan, so the drill drawer's rich
    fields work on historical data too.
    """
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    # Ensure path is the on-disk path the caller asked about, not whatever
    # was serialized at write time (which may use a different cwd).
    data["path"] = str(path.relative_to(_PROJECT_ROOT)).replace("\\", "/")
    return data


def _write_scan_json(rows: list[TickerRow], outputs_dir: Path, scan_date: str) -> Path:
    """Companion to write_csv — persist the full UI shape (with agent raw)."""
    outputs_dir.mkdir(parents=True, exist_ok=True)
    path = outputs_dir / f"{scan_date}_morning_scan.json"
    payload = {
        "date": scan_date,
        "path": str(path).replace("\\", "/"),
        "row_count": len(rows),
        "rows": [_tickerrow_to_ui(r) for r in rows],
    }
    # Atomic write: temp file in same dir + os.replace.
    fd, tmp = __import__("tempfile").mkstemp(prefix=".scan.", suffix=".tmp", dir=str(outputs_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, indent=2, default=str)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


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
        for name, sleeve in _live_sleeves().items()
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

            # Persist CSV + JSON sidecar + emit final payload. CSV stays the
            # source of truth for backwards compat / CLI use; JSON carries
            # the full UI shape including each agent's raw output dict so
            # the drill drawer's rich fields work on historical scans too.
            outputs_dir = _PROJECT_ROOT / "outputs"
            try:
                csv_path = write_csv(all_rows, outputs_dir, end_date)
                csv_path_str = str(csv_path).replace("\\", "/")
            except Exception:
                logger.exception("Failed to write CSV")
                csv_path_str = ""
            try:
                _write_scan_json(all_rows, outputs_dir, end_date)
            except Exception:
                logger.exception("Failed to write JSON sidecar (CSV still written)")

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


@router.post("/scan/ticker/{ticker}")
async def run_ticker_scan(ticker: str, request: Request):
    """Run the agent panel on a SINGLE ticker and stream the result via SSE.

    This is the "run the morning scan for one name" action: it runs that
    ticker's sleeve agents and streams per-agent progress, then a ``complete``
    event carrying the freshly-scored row (with each agent's signal,
    confidence, and reasoning). It is *ephemeral* — nothing is written to the
    scan files, so re-scoring one name never clobbers the day's full scan.

    Events: ``start`` → ``progress`` (per agent) → ``complete`` {row} | ``error``.
    """
    symbol = (ticker or "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Ticker is required.")

    sleeves_cfg = _live_sleeves()
    sleeve_name = next(
        (name for name, sl in sleeves_cfg.items()
         if symbol in {t.upper() for t in sl.get("tickers", [])}),
        None,
    )
    if sleeve_name is None:
        raise HTTPException(
            status_code=404,
            detail=f"'{symbol}' is not in any sleeve — add it to a sleeve first.",
        )

    # Run the sleeve's agents against just this one ticker.
    sleeve = dict(sleeves_cfg[sleeve_name])
    sleeve["tickers"] = [symbol]
    end_date = datetime.date.today().isoformat()

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

        def progress_handler(agent_name, ticker_, status, analysis, timestamp):
            progress_queue.put_nowait(
                ProgressUpdateEvent(
                    agent=agent_name, ticker=ticker_, status=status,
                    timestamp=timestamp, analysis=analysis,
                )
            )

        progress.register_handler(progress_handler)
        scan_task: asyncio.Task | None = None
        disconnect_task: asyncio.Task | None = None
        try:
            scan_task = asyncio.create_task(
                asyncio.to_thread(run_sleeve, sleeve_name, sleeve, end_date, show_reasoning=False)
            )
            disconnect_task = asyncio.create_task(_detect_disconnect())
            yield StartEvent().to_sse()

            while not scan_task.done():
                if disconnect_task.done():
                    scan_task.cancel()
                    return
                try:
                    event = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                    yield event.to_sse()
                except asyncio.TimeoutError:
                    continue
            while not progress_queue.empty():
                yield progress_queue.get_nowait().to_sse()

            try:
                rows = await scan_task
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception("Single-ticker scan failed for %s", symbol)
                yield ErrorEvent(message=f"Scan failed: {exc}").to_sse()
                return

            row = _tickerrow_to_ui(rows[0]) if rows else None
            yield CompleteEvent(data={"ticker": symbol, "sleeve": sleeve_name, "row": row}).to_sse()
        except asyncio.CancelledError:
            return
        finally:
            progress.unregister_handler(progress_handler)
            if scan_task and not scan_task.done():
                scan_task.cancel()
            if disconnect_task and not disconnect_task.done():
                disconnect_task.cancel()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ─── /sleeves/options/* (screener + chain) ──────────────────────────────────

# Conviction-signal screener cache: 5 min. The underlying daily-bar inputs
# don't change intraday, so a fresher TTL would just burn API calls.
_SCREENER_CACHE_TTL_SECONDS = 300
_screener_cache: dict[str, tuple[float, dict[str, Any]]] = {}

# Option chain cache: 60s. Quotes move, so refresh aggressively — but spammy
# expansions of one ticker shouldn't pin Massive's per-minute limit.
_CHAIN_CACHE_TTL_SECONDS = 60
_chain_cache: dict[str, tuple[float, dict[str, Any]]] = {}

# Benchmark for the lagging-mega-tech screen. Hard-coded since the screener's
# definition is "lagging QQQ" — changing the benchmark would change the
# strategy, not a config knob.
_BENCHMARK_TICKER = "QQQ"


def _compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """Standard 14-day RSI from a closes list (oldest → newest).

    Returns ``None`` if there aren't enough bars. Uses the simple-average
    seed (Wilder smoothing not applied) — appropriate for a short window
    where the difference is in noise.
    """
    if len(closes) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-diff)
    # Use the trailing ``period`` bars for the average.
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _return_over(closes: list[float], days: int) -> float | None:
    """Pct change over the last ``days`` trading bars. Returns fractional
    (0.05 = +5%). ``None`` if not enough history."""
    if len(closes) <= days:
        return None
    end = closes[-1]
    start = closes[-(days + 1)]
    if start == 0:
        return None
    return (end - start) / start


def _fetch_closes(client: MassiveClient, ticker: str, start: str, end: str) -> list[float]:
    """Daily closes for ``ticker`` between dates. Empty on any failure —
    the screener row will just report ``None`` for affected signals."""
    try:
        aggs = client.get_daily_aggregates(ticker, start, end)
    except MassiveError as exc:
        logger.warning("Massive prices failed for %s: %s", ticker, exc)
        return []
    return [p.close for p in convert_prices(aggs)]


def _fetch_bars(client: MassiveClient, ticker: str, start: str, end: str):
    """Daily OHLCV bars for ``ticker``. Returns ``list[Price]`` — same model
    convert_prices emits. Empty on any failure.

    Returns full bars because the new technical-pattern scorers (breakout,
    volume spike, etc) need volume + high + low in addition to close.
    """
    try:
        aggs = client.get_daily_aggregates(ticker, start, end)
    except MassiveError as exc:
        logger.warning("Massive prices failed for %s: %s", ticker, exc)
        return []
    return convert_prices(aggs)


# ─── Screener strategies ────────────────────────────────────────────────────

# Each scorer returns a generic dict:
#   {
#     "conviction": int (0..3),
#     "signals":    list of {label, value_text, fired, tooltip},
#     "sort_key":   float (lower = ranks earlier within same conviction),
#     "last_price": float | None,
#   }
#
# The frontend renders the chips directly from `signals` so adding a new
# strategy here automatically lights up its chips in the UI — no template
# changes required.


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stddev(xs: list[float], mean: float) -> float:
    if len(xs) < 2:
        return 0.0
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


def _signal(label: str, value_text: str, fired: bool, tooltip: str) -> dict[str, Any]:
    return {"label": label, "value_text": value_text, "fired": fired, "tooltip": tooltip}


def _recommendation(
    *,
    direction: str,
    strike_offset_pct: float = 0.0,
    expiry_lean: str = "near",
    reasoning: str,
) -> dict[str, Any]:
    """Per-strategy contract recommendation packaged for the frontend.

    The chain viewer uses ``direction`` ('call' | 'put') to pick which side
    of the chain to highlight, ``strike_offset_pct`` to find the strike
    closest to ``spot × (1 + offset/100)``, and renders ``reasoning`` as the
    explanation banner. ``expiry_lean`` is a hint ('near' = weeklies, 'mid'
    = 2-4 weeks, 'far' = monthlies+) shown alongside the highlighted row.
    """
    return {
        "direction": direction,
        "strike_offset_pct": strike_offset_pct,
        "expiry_lean": expiry_lean,
        "reasoning": reasoning,
    }


def _fmt_pct(n: float | None) -> str:
    if n is None or not math.isfinite(n):
        return "—"
    sign = "+" if n >= 0 else ""
    return f"{sign}{n * 100:.1f}%"


def _fmt_num(n: float | None, digits: int = 1) -> str:
    if n is None or not math.isfinite(n):
        return "—"
    return f"{n:.{digits}f}"


def _closes(bars: list) -> list[float]:
    return [b.close for b in bars]


def _volumes(bars: list) -> list[int]:
    return [b.volume for b in bars]


def _highs(bars: list) -> list[float]:
    return [b.high for b in bars]


def _lows(bars: list) -> list[float]:
    return [b.low for b in bars]


# ─── Conviction % helpers ────────────────────────────────────────────────────
#
# Magnitude functions map raw signal values to a 1.0–1.5 intensity multiplier.
# _conviction_pct normalises against the theoretical max so 3/3 at extreme
# thresholds ≈ 100 % and 1/3 barely-fired ≈ 26 %.


def _mag_rsi(rsi: float | None, threshold: float) -> float:
    """Higher multiplier when RSI is further past the trigger threshold."""
    if rsi is None:
        return 1.0
    return min(1.5, 1.0 + abs(rsi - threshold) / 20.0)


def _mag_return(pct: float | None, threshold: float) -> float:
    """Higher multiplier when the return/gap is a larger multiple of the threshold."""
    if pct is None or threshold == 0:
        return 1.0
    return min(1.5, 1.0 + (abs(pct) - abs(threshold)) / abs(threshold))


def _mag_volume(ratio: float | None, trigger: float) -> float:
    """Higher multiplier for more extreme volume ratios."""
    if ratio is None or trigger == 0:
        return 1.0
    return min(1.5, 1.0 + (ratio - trigger) / trigger)


def _mag_zscore(z: float | None) -> float:
    """Higher multiplier for more extreme z-scores."""
    if z is None:
        return 1.0
    return min(1.5, 1.0 + (abs(z) - 1.5) / 2.0)


def _conviction_pct(
    signals: list[dict],
    weights: tuple[float, ...],
    magnitudes: list[float],
) -> float:
    """Weighted conviction score 0–100.

    Normalised against the theoretical maximum (all signals at mag 1.5):
      1 signal barely fired ≈ 26 %  |  2 signals ≈ 50 %  |  3 signals ≈ 67 %
      3 signals at max magnitude + consistency bonus ≈ 100 %
    """
    max_possible = sum(w * 1.5 for w in weights)
    if max_possible == 0:
        return 0.0
    fired_score = sum(
        w * max(1.0, m)
        for s, w, m in zip(signals, weights, magnitudes)
        if s["fired"]
    )
    base = (fired_score / max_possible) * 100.0
    if all(s["fired"] for s in signals) and magnitudes and min(magnitudes) >= 1.15:
        base = min(100.0, base + 8.0)
    return round(base, 1)


# Expiry tier table: (strategy, conviction bucket) → 2 recommended tiers.
# "call"/"put" strings in structures for direction-variable strategies are
# substituted by _expiry_tiers() based on the runtime direction.
# Expiry tiers use three canonical DTE values that represent calendar-day cycles:
#   14d = weekly cycle (2 weeks out — avoids 0DTE/near-expiry)
#   35d = monthly cycle (~5 weeks)
#   63d = quarterly cycle (~9 weeks, high conviction only)
# These are fixed calendar-day offsets from today, not "trading days from now."
_EXPIRY_TIERS: dict[str, dict[str, list[dict[str, Any]]]] = {
    "weakness": {
        "low": [
            {"dte": 14, "label": "14d · spread", "structure": "call debit spread",
             "rationale": "Defined-risk bounce — sell higher strike to offset premium cost."},
            {"dte": 35, "label": "35d · ATM", "structure": "ATM call",
             "rationale": "Monthly cycle gives room if the QQQ rotation takes time to materialize."},
        ],
        "med": [
            {"dte": 35, "label": "35d · ATM", "structure": "ATM call",
             "rationale": "Mean-reversion lean — oversold names snap back within a monthly cycle."},
            {"dte": 14, "label": "14d · spread", "structure": "call debit spread",
             "rationale": "Cheaper weekly entry; sell 2–3% higher strike to offset cost."},
        ],
        "high": [
            {"dte": 63, "label": "63d · conviction", "structure": "long call",
             "rationale": "Oversold + lagging QQQ — trend repair can run far; quarterly gives room."},
            {"dte": 35, "label": "35d · tactical", "structure": "ATM call",
             "rationale": "Monthly cycle for quicker theta resolution."},
        ],
    },
    "strength": {
        "low": [
            {"dte": 14, "label": "14d · spread", "structure": "put debit spread",
             "rationale": "Defined-risk fade — buy ATM put, sell lower strike."},
            {"dte": 35, "label": "35d · ATM", "structure": "ATM put",
             "rationale": "Monthly cycle gives more time for the mean reversion to develop."},
        ],
        "med": [
            {"dte": 35, "label": "35d · ATM", "structure": "ATM put",
             "rationale": "Overbought fade — leading names stall before snapping back; monthly cycle."},
            {"dte": 14, "label": "14d · spread", "structure": "put debit spread",
             "rationale": "Weekly spread reduces premium on high-IV names."},
        ],
        "high": [
            {"dte": 63, "label": "63d · conviction", "structure": "long put",
             "rationale": "Leading + deeply overbought — reversion can be sharp; quarterly room."},
            {"dte": 35, "label": "35d · tactical", "structure": "ATM put",
             "rationale": "Monthly cycle if RSI is past 70 and you want faster resolution."},
        ],
    },
    "momentum": {
        "low": [
            {"dte": 35, "label": "35d · spread", "structure": "call debit spread",
             "rationale": "Defined risk on continuation — cap upside to reduce premium."},
            {"dte": 35, "label": "35d · ATM", "structure": "ATM call",
             "rationale": "Monthly cycle gives room if the trend needs a few weeks to extend."},
        ],
        "med": [
            {"dte": 35, "label": "35d · OTM", "structure": "2% OTM call",
             "rationale": "Momentum payoff window is 3–5 weeks; slight OTM for leverage."},
            {"dte": 35, "label": "35d · spread", "structure": "call debit spread",
             "rationale": "Spread if IV is elevated on the name."},
        ],
        "high": [
            {"dte": 63, "label": "63d · position", "structure": "long call",
             "rationale": "Strong absolute trend — quarterly cycle gives the move room to compound."},
            {"dte": 35, "label": "35d · OTM", "structure": "2% OTM call",
             "rationale": "Monthly cycle if you prefer a 5-week catalyst horizon."},
        ],
    },
    "mean_reversion": {
        "low": [
            {"dte": 14, "label": "14d · spread", "structure": "call debit spread",
             "rationale": "Snap-back with defined risk — thesis resolves in ≤2 weeks."},
            {"dte": 35, "label": "35d · fallback", "structure": "ATM call",
             "rationale": "Monthly cycle buffer if consolidation extends beyond the initial snap."},
        ],
        "med": [
            {"dte": 14, "label": "14d · ATM", "structure": "ATM call",
             "rationale": "Snap-backs happen fast — ATM for max gamma on the first move."},
            {"dte": 35, "label": "35d · safety", "structure": "ATM call",
             "rationale": "Monthly cycle buffer if price needs time to turn."},
        ],
        "high": [
            {"dte": 35, "label": "35d · long", "structure": "long call",
             "rationale": "Extreme z-score + RSI extreme — reversion can be violent; monthly cycle."},
            {"dte": 14, "label": "14d · ATM", "structure": "ATM call",
             "rationale": "Fast gamma play on the initial snap when z > 2.5."},
        ],
    },
    "breakout": {
        "low": [
            {"dte": 35, "label": "35d · spread", "structure": "call debit spread",
             "rationale": "Defined risk on the breakout — cap cost if move stalls."},
            {"dte": 35, "label": "35d · ATM", "structure": "ATM call",
             "rationale": "Monthly cycle gives time for the 52w-high breakout to extend."},
        ],
        "med": [
            {"dte": 35, "label": "35d · OTM", "structure": "2% OTM call",
             "rationale": "52w-high breakout on volume — monthly cycle for momentum continuation."},
            {"dte": 35, "label": "35d · spread", "structure": "call debit spread",
             "rationale": "Spread to reduce cost; sell strike at prior resistance."},
        ],
        "high": [
            {"dte": 63, "label": "63d · conviction", "structure": "long call",
             "rationale": "Volume breakout above a year-high — quarterly cycle to ride the extension."},
            {"dte": 35, "label": "35d · OTM", "structure": "2% OTM call",
             "rationale": "Monthly leveraged play if you expect near-term acceleration."},
        ],
    },
    "breakdown": {
        "low": [
            {"dte": 35, "label": "35d · spread", "structure": "put debit spread",
             "rationale": "Defined risk on the break — cap cost if a bounce materializes."},
            {"dte": 35, "label": "35d · ATM", "structure": "ATM put",
             "rationale": "Monthly cycle gives time to outlast a dead-cat bounce."},
        ],
        "med": [
            {"dte": 35, "label": "35d · OTM", "structure": "2% OTM put",
             "rationale": "Breakdown below 52w low on volume — monthly cycle for continuation."},
            {"dte": 35, "label": "35d · spread", "structure": "put debit spread",
             "rationale": "Spread if the name has wide bid-ask on outright puts."},
        ],
        "high": [
            {"dte": 63, "label": "63d · conviction", "structure": "long put",
             "rationale": "High-conviction break — oversold can stay oversold; quarterly cycle."},
            {"dte": 35, "label": "35d · OTM", "structure": "2% OTM put",
             "rationale": "Monthly play if you expect a fast continuation flush."},
        ],
    },
    "volume_spike": {
        "low": [
            {"dte": 14, "label": "14d · fast", "structure": "ATM call",
             "rationale": "Unusual flow — follow it into the next 1–2 weekly cycles."},
            {"dte": 14, "label": "14d · spread", "structure": "call debit spread",
             "rationale": "Defined risk if direction persistence is uncertain."},
        ],
        "med": [
            {"dte": 14, "label": "14d · ATM", "structure": "ATM call",
             "rationale": "Flow confirmation — give the move 1–2 weeks to develop."},
            {"dte": 35, "label": "35d · follow", "structure": "ATM call",
             "rationale": "Monthly cycle if close-in-range is extreme (>90%) and you want more time."},
        ],
        "high": [
            {"dte": 35, "label": "35d · long", "structure": "long call",
             "rationale": "Extreme volume + close-at-wick — institutional conviction; monthly cycle."},
            {"dte": 14, "label": "14d · ATM", "structure": "ATM call",
             "rationale": "Weekly exit if the flow was a single-day event."},
        ],
    },
    "pullback": {
        "low": [
            {"dte": 35, "label": "35d · spread", "structure": "call debit spread",
             "rationale": "Buy-the-dip with defined risk — MA bounces can take 2–4 weeks."},
            {"dte": 35, "label": "35d · ATM", "structure": "ATM call",
             "rationale": "Monthly cycle gives more time if consolidation at the MA extends."},
        ],
        "med": [
            {"dte": 35, "label": "35d · ATM", "structure": "ATM call",
             "rationale": "Uptrend intact — 20/50d MA bounce is a high-probability setup; monthly."},
            {"dte": 35, "label": "35d · spread", "structure": "call debit spread",
             "rationale": "Spread to reduce cost; sell strike above recent resistance."},
        ],
        "high": [
            {"dte": 63, "label": "63d · diagonal", "structure": "diagonal call spread",
             "rationale": "Strong pullback signal — sell near-dated call to offset the quarterly leg."},
            {"dte": 35, "label": "35d · ATM", "structure": "ATM call",
             "rationale": "Monthly outright if you prefer simple directional exposure."},
        ],
    },
    "trend_bias": {
        "low": [
            {"dte": 63, "label": "63d · spread", "structure": "call debit spread",
             "rationale": "Strategic directional bet — defined risk on the MA cross thesis."},
            {"dte": 63, "label": "63d · ATM", "structure": "ATM call",
             "rationale": "Quarterly cycle gives time for the structural trend to develop fully."},
        ],
        "med": [
            {"dte": 63, "label": "63d · ATM", "structure": "ATM call",
             "rationale": "MA cross regime — quarterly cycle gives room to compound over 2 months."},
            {"dte": 63, "label": "63d · spread", "structure": "call debit spread",
             "rationale": "Reduce premium on the structural bet."},
        ],
        "high": [
            {"dte": 63, "label": "63d · LEAPS", "structure": "LEAPS call",
             "rationale": "Accelerating cross + price on trend side — quarterly conviction position."},
            {"dte": 63, "label": "63d · ATM", "structure": "ATM call",
             "rationale": "Outright at quarterly if you prefer a defined exit window."},
        ],
    },
    "vol_expansion": {
        "low": [
            {"dte": 14, "label": "14d · fast", "structure": "ATM call",
             "rationale": "Vol regime blowouts are short-lived — capitalize before IV mean-reverts."},
            {"dte": 14, "label": "14d · spread", "structure": "call debit spread",
             "rationale": "Defined risk in case the directional call is wrong."},
        ],
        "med": [
            {"dte": 14, "label": "14d · ATM", "structure": "ATM call",
             "rationale": "Vol expansion + big move — follow the regime shift, weekly cycle."},
            {"dte": 35, "label": "35d · ATM", "structure": "ATM call",
             "rationale": "Monthly cycle if realized vol is blowing out and may sustain."},
        ],
        "high": [
            {"dte": 35, "label": "35d · long", "structure": "long call",
             "rationale": "Extreme vol expansion + large catalyst — monthly cycle for regime change."},
            {"dte": 14, "label": "14d · ATM", "structure": "ATM call",
             "rationale": "Weekly cycle if you expect vol to mean-revert within 2 weeks."},
        ],
    },
    "unusual_options_activity": {
        "low": [
            {"dte": 35, "label": "35d · follow", "structure": "ATM call",
             "rationale": "Follow institutional flow; monthly cycle matches typical sweep positioning."},
            {"dte": 14, "label": "14d · fast", "structure": "ATM call",
             "rationale": "Weekly cycle if OTM strikes suggest a near-term catalyst."},
        ],
        "med": [
            {"dte": 35, "label": "35d · position", "structure": "ATM call",
             "rationale": "Institutional bets typically have a monthly thesis — match their cycle."},
            {"dte": 35, "label": "35d · follow", "structure": "ATM call",
             "rationale": "Monthly cycle for single-day sweep activity."},
        ],
        "high": [
            {"dte": 63, "label": "63d · conviction", "structure": "long call",
             "rationale": "Heavy vol/OI + OTM concentration — high-conviction smart-money; quarterly."},
            {"dte": 35, "label": "35d · position", "structure": "ATM call",
             "rationale": "Monthly institutional position horizon."},
        ],
    },
}


# Spread width between the two strikes of a vertical/diagonal, as a percent of
# spot. The short leg is sold this far OTM of the long (ATM) leg.
_SPREAD_WIDTH_PCT = 5.0


def _structure_to_legs(structure: str) -> list[dict[str, Any]]:
    """Map a tier ``structure`` string to its option legs for chain highlighting.

    Each leg is ``{side, direction, strike_offset_pct}`` where:
      - ``side``               : 'long' (bought) or 'short' (sold)
      - ``direction``          : 'call' | 'put'
      - ``strike_offset_pct``  : 0 = ATM, positive = strike above spot,
                                 negative = strike below spot. This matches the
                                 ScreenerRecommendation offset convention so the
                                 chain viewer resolves every leg with the same
                                 ``spot * (1 + offset/100)`` math.

    Single-leg structures (ATM/long/LEAPS/2% OTM) return one leg; debit verticals
    and diagonals return two so both strikes light up in the chain.
    """
    s = structure.lower()
    is_put = "put" in s
    direction = "put" if is_put else "call"
    otm = -1.0 if is_put else 1.0  # OTM is below spot for puts, above for calls

    if "debit spread" in s or "diagonal" in s:
        # Buy ATM, sell one spread-width further OTM (cap upside, cut cost).
        return [
            {"side": "long", "direction": direction, "strike_offset_pct": 0.0},
            {"side": "short", "direction": direction, "strike_offset_pct": otm * _SPREAD_WIDTH_PCT},
        ]
    if "2% otm" in s:
        return [{"side": "long", "direction": direction, "strike_offset_pct": otm * 2.0}]
    # ATM call/put, long call/put, LEAPS call/put → single ATM long leg.
    return [{"side": "long", "direction": direction, "strike_offset_pct": 0.0}]


def _expiry_tiers(
    strategy: str,
    conviction_pct: float,
    direction: str,
) -> list[dict[str, Any]]:
    """Return 2 expiry tier recs for a (strategy, conviction %, direction) triple.

    Returns an empty list when conviction_pct < 40 (no credible play).
    For put-direction strategies the call structure names are substituted.
    Each tier carries a ``legs`` list so the chain viewer can highlight every
    strike of a multi-leg structure (e.g. both legs of a debit spread).
    """
    if conviction_pct < 40:
        return []
    bucket = "high" if conviction_pct >= 80 else ("med" if conviction_pct >= 60 else "low")
    raw = _EXPIRY_TIERS.get(strategy, {}).get(bucket, [])
    result = []
    for tier in raw:
        t = tier.copy()
        if direction == "put":
            t["structure"] = (
                t["structure"]
                .replace("call debit spread", "put debit spread")
                .replace("2% OTM call", "2% OTM put")
                .replace("LEAPS call", "LEAPS put")
                .replace("diagonal call spread", "diagonal put spread")
                .replace("ATM call", "ATM put")
                .replace("long call", "long put")
            )
        t["legs"] = _structure_to_legs(t["structure"])
        result.append(t)
    return result


# ─── Scorers (bars-based) ────────────────────────────────────────────────────
#
# All scorers take ``(bars, qqq_bars, **kwargs)`` so the dispatcher can pass
# optional context (ticker, client) for strategies that need to fetch the
# options chain inline. Most scorers ignore the kwargs.


def _score_weakness(bars: list, qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Lagging QQQ + oversold. Bounce-trade calls or continuation puts."""
    closes = _closes(bars)
    qqq_closes = _closes(qqq_bars)
    last_price = closes[-1] if closes else None
    r20 = _return_over(closes, 20)
    r5 = _return_over(closes, 5)
    rsi = _compute_rsi(closes, 14)
    q20 = _return_over(qqq_closes, 20)
    q5 = _return_over(qqq_closes, 5)
    d20 = (r20 - q20) if (r20 is not None and q20 is not None) else None
    d5 = (r5 - q5) if (r5 is not None and q5 is not None) else None

    # Signal 4: price vs 20d SMA
    sma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
    below_sma20 = (last_price is not None and sma20 is not None and last_price < sma20 * 0.98)
    sma20_dist_mag = (sma20 / last_price) if (below_sma20 and last_price and last_price > 0) else 1.0

    # Signal 5: 5-day rate of change
    roc5 = _return_over(closes, 5)  # same as r5 — negative = falling
    roc5_fired = roc5 is not None and roc5 < -0.02

    signals = [
        _signal(
            "20d vs QQQ",
            _fmt_pct(d20),
            d20 is not None and d20 < -0.02,
            f"Ticker {_fmt_pct(r20)} vs QQQ {_fmt_pct(q20)} over 20 trading days. Fires when gap is worse than −2%.",
        ),
        _signal(
            "5d vs QQQ",
            _fmt_pct(d5),
            d5 is not None and d5 < -0.01,
            f"Ticker {_fmt_pct(r5)} vs QQQ {_fmt_pct(q5)} over 5 trading days. Fires when gap is worse than −1%.",
        ),
        _signal(
            "RSI",
            _fmt_num(rsi, 0),
            rsi is not None and rsi < 45,
            f"Relative Strength Index (14d). Fires when below 45 (approaching oversold). Current: {_fmt_num(rsi, 1)}.",
        ),
        _signal(
            "vs 20d SMA",
            _fmt_pct((last_price - sma20) / sma20 if (last_price and sma20) else None),
            below_sma20,
            "Price below 20d SMA confirms weak trend; deeper = stronger setup.",
        ),
        _signal(
            "5d ROC",
            _fmt_pct(roc5),
            roc5_fired,
            "5-day momentum negative; recent selling pressure.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _m = [
        _mag_return(d20, -0.02) if (d20 is not None and d20 < -0.02) else 1.0,
        _mag_return(d5, -0.01) if (d5 is not None and d5 < -0.01) else 1.0,
        _mag_rsi(rsi, 45.0) if (rsi is not None and rsi < 45) else 1.0,
        min(1.5, sma20_dist_mag) if below_sma20 else 1.0,
        min(1.5, 1.0 + abs(roc5 or 0) / 0.02) if roc5_fired else 1.0,
    ]
    c_pct = _conviction_pct(signals, (26.25, 18.75, 30.0, 15.0, 10.0), _m)
    sort_key = d20 if d20 is not None else 0.0
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("weakness", c_pct, "call"),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction="call",
            strike_offset_pct=0.0,
            expiry_lean="near",
            reasoning=(
                "Oversold name lagging QQQ — bounce trade. ATM call captures the "
                "snap-back with ~0.5 delta exposure. Near-term weekly keeps theta "
                "manageable; if the bounce hasn't started in 1–2 weeks, the thesis is wrong."
            ),
        ),
    }


def _score_strength(bars: list, qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Leading QQQ + overbought. Breakout calls or mean-reversion puts."""
    closes = _closes(bars)
    highs = _highs(bars)
    qqq_closes = _closes(qqq_bars)
    last_price = closes[-1] if closes else None
    r20 = _return_over(closes, 20)
    r5 = _return_over(closes, 5)
    rsi = _compute_rsi(closes, 14)
    q20 = _return_over(qqq_closes, 20)
    q5 = _return_over(qqq_closes, 5)
    d20 = (r20 - q20) if (r20 is not None and q20 is not None) else None
    d5 = (r5 - q5) if (r5 is not None and q5 is not None) else None

    # Signal 4: price vs 20d SMA (overbought fade)
    sma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
    above_sma20 = (last_price is not None and sma20 is not None and last_price > sma20 * 1.02)

    # Signal 5: distance from 52-week high
    yr_highs = highs[-252:] if len(highs) >= 252 else highs
    high_52w = max(yr_highs) if yr_highs else None
    near_52w_high = (
        last_price is not None and high_52w is not None
        and high_52w > 0
        and (high_52w - last_price) / high_52w <= 0.03
    )

    signals = [
        _signal(
            "20d vs QQQ",
            _fmt_pct(d20),
            d20 is not None and d20 > 0.02,
            f"Ticker {_fmt_pct(r20)} vs QQQ {_fmt_pct(q20)} over 20 trading days. Fires when gap is better than +2%.",
        ),
        _signal(
            "5d vs QQQ",
            _fmt_pct(d5),
            d5 is not None and d5 > 0.01,
            f"Ticker {_fmt_pct(r5)} vs QQQ {_fmt_pct(q5)} over 5 trading days. Fires when gap is better than +1%.",
        ),
        _signal(
            "RSI",
            _fmt_num(rsi, 0),
            rsi is not None and rsi > 55,
            f"Relative Strength Index (14d). Fires when above 55 (approaching overbought). Current: {_fmt_num(rsi, 1)}.",
        ),
        _signal(
            "vs 20d SMA",
            _fmt_pct((last_price - sma20) / sma20 if (last_price and sma20) else None),
            above_sma20,
            "Price above 20d SMA by >2% — extended above short-term trend; fade risk rises.",
        ),
        _signal(
            "52w high",
            _fmt_pct((last_price - high_52w) / high_52w if (last_price and high_52w) else None),
            near_52w_high,
            "Near 52-week high increases fade risk.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _m = [
        _mag_return(d20, 0.02) if (d20 is not None and d20 > 0.02) else 1.0,
        _mag_return(d5, 0.01) if (d5 is not None and d5 > 0.01) else 1.0,
        _mag_rsi(rsi, 55.0) if (rsi is not None and rsi > 55) else 1.0,
        1.2 if above_sma20 else 1.0,
        1.2 if near_52w_high else 1.0,
    ]
    c_pct = _conviction_pct(signals, (26.25, 18.75, 30.0, 15.0, 10.0), _m)
    sort_key = -d20 if d20 is not None else 0.0
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("strength", c_pct, "put"),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction="put",
            strike_offset_pct=0.0,
            expiry_lean="near",
            reasoning=(
                "Overbought name leading QQQ — mean-reversion fade. ATM put "
                "captures the pullback. Use a near-term weekly so you're not "
                "fighting theta if the trend keeps running."
            ),
        ),
    }


def _score_momentum(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Pure absolute trend follow. No benchmark — just up-and-to-the-right."""
    closes = _closes(bars)
    vols = _volumes(bars)
    last_price = closes[-1] if closes else None
    r20 = _return_over(closes, 20)
    r5 = _return_over(closes, 5)
    rsi = _compute_rsi(closes, 14)

    # Signal 4: 20d SMA slope (trend acceleration)
    sma20_now = _mean(closes[-20:]) if len(closes) >= 20 else None
    sma20_10d_ago = _mean(closes[-30:-10]) if len(closes) >= 30 else None
    sma20_slope: float | None = None
    if sma20_now is not None and sma20_10d_ago is not None and sma20_10d_ago > 0:
        sma20_slope = (sma20_now - sma20_10d_ago) / sma20_10d_ago * 100.0
    sma20_slope_fired = sma20_slope is not None and sma20_slope > 0.5

    # Signal 5: volume trend (avg 5d vs avg 20d)
    avg_vol_5d = _mean([float(v) for v in vols[-5:]]) if len(vols) >= 5 else None
    avg_vol_20d = _mean([float(v) for v in vols[-20:]]) if len(vols) >= 20 else None
    vol_trend_fired = (
        avg_vol_5d is not None and avg_vol_20d is not None
        and avg_vol_20d > 0 and avg_vol_5d > avg_vol_20d * 1.1
    )

    signals = [
        _signal(
            "20d return",
            _fmt_pct(r20),
            r20 is not None and r20 > 0.05,
            f"Absolute 20-day return. Fires when > +5%. Current: {_fmt_pct(r20)}.",
        ),
        _signal(
            "5d return",
            _fmt_pct(r5),
            r5 is not None and r5 > 0.02,
            f"Absolute 5-day return. Fires when > +2%. Current: {_fmt_pct(r5)}.",
        ),
        _signal(
            "RSI",
            _fmt_num(rsi, 0),
            rsi is not None and rsi > 60,
            f"RSI (14d). Fires when > 60 (strong momentum, not yet exhausted). Current: {_fmt_num(rsi, 1)}.",
        ),
        _signal(
            "SMA slope",
            f"{_fmt_num(sma20_slope, 2)}%" if sma20_slope is not None else "—",
            sma20_slope_fired,
            "20d SMA slope over last 10 days. Fires when > 0.5% — trend is accelerating, not flattening.",
        ),
        _signal(
            "volume trend",
            f"{_fmt_num((avg_vol_5d or 0) / (avg_vol_20d or 1), 2)}×" if avg_vol_20d else "—",
            vol_trend_fired,
            "Avg volume last 5d vs avg volume last 20d. Fires when 5d avg > 20d avg × 1.1 — rising participation.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _m = [
        _mag_return(r20, 0.05) if (r20 is not None and r20 > 0.05) else 1.0,
        _mag_return(r5, 0.02) if (r5 is not None and r5 > 0.02) else 1.0,
        _mag_rsi(rsi, 60.0) if (rsi is not None and rsi > 60) else 1.0,
        min(1.5, 1.0 + (sma20_slope or 0) / 0.5 * 0.5) if sma20_slope_fired else 1.0,
        min(1.5, 1.0 + ((avg_vol_5d or 0) / (avg_vol_20d or 1) - 1.1) / 0.5) if vol_trend_fired else 1.0,
    ]
    c_pct = _conviction_pct(signals, (30.0, 22.5, 22.5, 15.0, 10.0), _m)
    sort_key = -r20 if r20 is not None else 0.0
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("momentum", c_pct, "call"),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction="call",
            strike_offset_pct=2.0,
            expiry_lean="mid",
            reasoning=(
                "Trending up with momentum — ride the move. 2% OTM call gives "
                "leverage on continuation while keeping premium reasonable. Use a "
                "2–4 week expiry so theta isn't punishing if the move stalls a few days."
            ),
        ),
    }


def _score_mean_reversion(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Stretched far from 20-day average. Bet on a snap-back in either direction."""
    closes = _closes(bars)
    vols = _volumes(bars)
    last_price = closes[-1] if closes else None
    rsi = _compute_rsi(closes, 14)

    z = None
    pct_from_ma = None
    if len(closes) >= 20:
        window = closes[-20:]
        mean = _mean(window)
        sd = _stddev(window, mean)
        if sd > 0 and last_price is not None:
            z = (last_price - mean) / sd
            pct_from_ma = (last_price - mean) / mean

    r5 = _return_over(closes, 5)

    # Signal 4: 5d ROC in reversion direction (confirms the stretch is real)
    # For above-mean (z>0) reversion: we want negative recent ROC (already falling back)
    # For below-mean (z<0) reversion: we want positive recent ROC (already bouncing)
    roc5_rev_fired = False
    if r5 is not None and z is not None:
        if z > 0 and r5 < -0.03:
            roc5_rev_fired = True  # above mean, already reversing down
        elif z < 0 and r5 > 0.03:
            roc5_rev_fired = True  # below mean, already bouncing up

    # Signal 5: volume contraction during the stretch (exhaustion, not distribution)
    avg_vol_5d = _mean([float(v) for v in vols[-5:]]) if len(vols) >= 5 else None
    avg_vol_20d = _mean([float(v) for v in vols[-20:]]) if len(vols) >= 20 else None
    vol_contraction_fired = (
        avg_vol_5d is not None and avg_vol_20d is not None
        and avg_vol_20d > 0 and avg_vol_5d < avg_vol_20d * 0.8
    )

    signals = [
        _signal(
            "Z-score (20d)",
            _fmt_num(z, 2),
            z is not None and abs(z) > 1.5,
            f"How many 20-day standard deviations the price is from its 20-day mean. Fires when |z| > 1.5 (statistically stretched). "
            f"Current: z={_fmt_num(z, 2)} ({_fmt_pct(pct_from_ma)} from 20d MA).",
        ),
        _signal(
            "5d move",
            _fmt_pct(r5),
            r5 is not None and abs(r5) > 0.05,
            f"Absolute 5-day return. Fires when |move| > 5% (large recent swing). Current: {_fmt_pct(r5)}.",
        ),
        _signal(
            "RSI extreme",
            _fmt_num(rsi, 0),
            rsi is not None and (rsi > 70 or rsi < 30),
            f"RSI (14d). Fires when > 70 (overbought, snap-back via puts) or < 30 (oversold, snap-back via calls). Current: {_fmt_num(rsi, 1)}.",
        ),
        _signal(
            "5d ROC",
            _fmt_pct(r5),
            roc5_rev_fired,
            "5-day rate of change in the reversion direction. Fires when |5d ROC| > 3% — confirms the stretch is already unwinding.",
        ),
        _signal(
            "vol contraction",
            f"{_fmt_num((avg_vol_5d or 0) / (avg_vol_20d or 1), 2)}×" if avg_vol_20d else "—",
            vol_contraction_fired,
            "Low volume during pullback suggests exhaustion, not distribution.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _m = [
        _mag_zscore(z) if (z is not None and abs(z) > 1.5) else 1.0,
        _mag_return(r5, 0.05) if (r5 is not None and abs(r5) > 0.05) else 1.0,
        _mag_rsi(rsi, 70.0 if (z or 0) > 0 else 30.0) if (rsi is not None and (rsi > 70 or rsi < 30)) else 1.0,
        min(1.5, 1.0 + abs(r5 or 0) / 0.03) if roc5_rev_fired else 1.0,
        1.2 if vol_contraction_fired else 1.0,
    ]
    c_pct = _conviction_pct(signals, (33.75, 18.75, 22.5, 15.0, 10.0), _m)
    sort_key = -abs(z) if z is not None else 0.0
    # Direction inverts based on which side of the mean the price has stretched
    # to: above-mean → expect pullback (puts), below-mean → expect bounce (calls).
    if z is not None and z > 0:
        direction = "put"
        why = "Price stretched above the 20-day mean — fade with a put expecting reversion."
    else:
        direction = "call"
        why = "Price stretched below the 20-day mean — bounce trade with a call."
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("mean_reversion", c_pct, direction),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction=direction,
            strike_offset_pct=0.0,
            expiry_lean="near",
            reasoning=(
                f"{why} ATM strike for ~0.5 delta — symmetric exposure to the "
                "snap-back. Near-term weekly: if reversion doesn't start fast, the setup decays."
            ),
        ),
    }


# ─── New: technical-pattern scorers ─────────────────────────────────────────


def _score_breakout(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Near 52-week high + volume surge + RSI momentum. Bullish continuation
    setup — calls on the breakout. Reliable in trending markets, less so in chop."""
    closes = _closes(bars)
    highs = _highs(bars)
    lows = _lows(bars)
    vols = _volumes(bars)
    last_price = closes[-1] if closes else None
    rsi = _compute_rsi(closes, 14)

    # 52-week high (up to ~252 trading days)
    yr = highs[-252:] if len(highs) >= 252 else highs
    high_52w = max(yr) if yr else None
    pct_to_high = ((last_price - high_52w) / high_52w) if (last_price and high_52w) else None
    # negative = below high; near-high means pct_to_high in [-0.05, 0]
    near_high = pct_to_high is not None and -0.05 <= pct_to_high <= 0.005

    # Volume surge: today vs trailing 20d average (excluding today)
    vol_ratio = None
    if len(vols) >= 21:
        baseline = sum(vols[-21:-1]) / 20
        if baseline > 0:
            vol_ratio = vols[-1] / baseline

    # Signal 4: price above 20d SMA (breakout above short-term trend)
    sma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
    above_sma20 = last_price is not None and sma20 is not None and last_price > sma20

    # Signal 5: range expansion — today's H-L range vs avg true range last 10d
    atr_10d: float | None = None
    if len(highs) >= 11 and len(lows) >= 11 and len(closes) >= 11:
        true_ranges = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(-10, 0)
        ]
        atr_10d = _mean(true_ranges)
    today_range = (highs[-1] - lows[-1]) if (highs and lows) else None
    range_expansion_fired = (
        today_range is not None and atr_10d is not None
        and atr_10d > 0 and today_range > atr_10d * 1.3
    )

    signals = [
        _signal(
            "near 52w high",
            _fmt_pct(pct_to_high),
            near_high,
            f"Distance from the trailing 52-week high. Fires when within 5% of the high (and not already above by >0.5%). "
            f"52w high: {_fmt_num(high_52w, 2)}, current: {_fmt_num(last_price, 2)}.",
        ),
        _signal(
            "volume surge",
            f"{_fmt_num(vol_ratio, 2)}×" if vol_ratio is not None else "—",
            vol_ratio is not None and vol_ratio > 1.5,
            f"Today's volume vs trailing 20-day average. Fires when > 1.5× (participation confirms the move). "
            f"Current: {_fmt_num(vol_ratio, 2)}×.",
        ),
        _signal(
            "RSI",
            _fmt_num(rsi, 0),
            rsi is not None and rsi > 60,
            f"RSI (14d). Fires when > 60 — momentum behind the breakout, not yet overbought. Current: {_fmt_num(rsi, 1)}.",
        ),
        _signal(
            "above 20d SMA",
            "yes" if above_sma20 else "no",
            above_sma20,
            "Price above 20d SMA — breakout has reclaimed short-term trend support.",
        ),
        _signal(
            "range expansion",
            _fmt_pct((today_range / atr_10d - 1.0) if (today_range and atr_10d) else None),
            range_expansion_fired,
            "Today's H-L range > 10d avg true range × 1.3 — expanded range confirms breakout conviction.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _near_high_mag = max(1.0, 1.5 - abs(pct_to_high or -0.05) / 0.1) if near_high else 1.0
    _m = [
        _near_high_mag,
        _mag_volume(vol_ratio, 1.5) if (vol_ratio is not None and vol_ratio > 1.5) else 1.0,
        _mag_rsi(rsi, 60.0) if (rsi is not None and rsi > 60) else 1.0,
        1.2 if above_sma20 else 1.0,
        min(1.5, 1.0 + (today_range / atr_10d - 1.3) / 0.5) if range_expansion_fired else 1.0,
    ]
    c_pct = _conviction_pct(signals, (30.0, 26.25, 18.75, 15.0, 10.0), _m)
    sort_key = -pct_to_high if pct_to_high is not None else 0.0
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("breakout", c_pct, "call"),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction="call",
            strike_offset_pct=2.0,
            expiry_lean="mid",
            reasoning=(
                "Pressing the 52-week high on volume — momentum breakout. "
                "2% OTM call above the breakout level: cheap leverage that pays "
                "if the breakout sticks. 2–4 week expiry gives the trend room "
                "without bleeding theta on a failed breakout."
            ),
        ),
    }


def _score_breakdown(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Near 52-week low + volume surge + downside momentum. Bearish mirror
    of Breakout — puts on the continuation."""
    closes = _closes(bars)
    highs = _highs(bars)
    lows = _lows(bars)
    vols = _volumes(bars)
    last_price = closes[-1] if closes else None
    rsi = _compute_rsi(closes, 14)

    yr_lows = lows[-252:] if len(lows) >= 252 else lows
    low_52w = min(yr_lows) if yr_lows else None
    pct_above_low = ((last_price - low_52w) / low_52w) if (last_price and low_52w) else None
    near_low = pct_above_low is not None and -0.005 <= pct_above_low <= 0.05

    vol_ratio = None
    if len(vols) >= 21:
        baseline = sum(vols[-21:-1]) / 20
        if baseline > 0:
            vol_ratio = vols[-1] / baseline

    # Signal 4: price below 20d SMA
    sma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
    below_sma20 = last_price is not None and sma20 is not None and last_price < sma20

    # Signal 5: range expansion on a bearish candle (close in lower half of range)
    atr_10d: float | None = None
    if len(highs) >= 11 and len(lows) >= 11 and len(closes) >= 11:
        true_ranges = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(-10, 0)
        ]
        atr_10d = _mean(true_ranges)
    today_range = (highs[-1] - lows[-1]) if (highs and lows) else None
    # Bearish range expansion: expanded range AND close in lower half
    today_close_in_range = (
        ((closes[-1] - lows[-1]) / today_range) if (today_range and today_range > 0) else 0.5
    )
    range_expansion_fired = (
        today_range is not None and atr_10d is not None
        and atr_10d > 0 and today_range > atr_10d * 1.3
        and today_close_in_range <= 0.5
    )

    signals = [
        _signal(
            "near 52w low",
            _fmt_pct(pct_above_low),
            near_low,
            f"Distance above the trailing 52-week low. Fires when within 5% of the low. "
            f"52w low: {_fmt_num(low_52w, 2)}, current: {_fmt_num(last_price, 2)}.",
        ),
        _signal(
            "volume surge",
            f"{_fmt_num(vol_ratio, 2)}×" if vol_ratio is not None else "—",
            vol_ratio is not None and vol_ratio > 1.5,
            f"Today's volume vs trailing 20-day average. Fires when > 1.5× (capitulation flow). "
            f"Current: {_fmt_num(vol_ratio, 2)}×.",
        ),
        _signal(
            "RSI",
            _fmt_num(rsi, 0),
            rsi is not None and rsi < 40,
            f"RSI (14d). Fires when < 40 — sustained downside momentum. Current: {_fmt_num(rsi, 1)}.",
        ),
        _signal(
            "below 20d SMA",
            "yes" if below_sma20 else "no",
            below_sma20,
            "Price below 20d SMA — breakdown has lost short-term trend support.",
        ),
        _signal(
            "range expansion",
            _fmt_pct((today_range / atr_10d - 1.0) if (today_range and atr_10d) else None),
            range_expansion_fired,
            "Today's H-L range > 10d ATR × 1.3, close in lower half — expanded bearish candle confirms capitulation.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _near_low_mag = max(1.0, 1.5 - abs(pct_above_low or 0.05) / 0.1) if near_low else 1.0
    _m = [
        _near_low_mag,
        _mag_volume(vol_ratio, 1.5) if (vol_ratio is not None and vol_ratio > 1.5) else 1.0,
        _mag_rsi(rsi, 40.0) if (rsi is not None and rsi < 40) else 1.0,
        1.2 if below_sma20 else 1.0,
        min(1.5, 1.0 + (today_range / atr_10d - 1.3) / 0.5) if range_expansion_fired else 1.0,
    ]
    c_pct = _conviction_pct(signals, (30.0, 26.25, 18.75, 15.0, 10.0), _m)
    sort_key = pct_above_low if pct_above_low is not None else 0.0
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("breakdown", c_pct, "put"),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction="put",
            strike_offset_pct=-2.0,
            expiry_lean="mid",
            reasoning=(
                "Breaking 52-week support on volume — capitulation in motion. "
                "2% OTM put below current price: leveraged downside if the "
                "breakdown continues. 2–4 week expiry to outlast a dead-cat bounce."
            ),
        ),
    }


def _score_volume_spike(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Unusual volume + big move + close at the wick extreme. Direction-agnostic:
    surfaces "something is happening" names. User picks calls or puts based on
    whether the close is at the day's high (bullish) or low (bearish)."""
    closes = _closes(bars)
    highs = _highs(bars)
    lows = _lows(bars)
    vols = _volumes(bars)

    if not bars:
        return {"conviction": 0, "signals": [], "sort_key": 0.0, "last_price": None}

    last_price = closes[-1]
    # Today's move vs yesterday's close.
    today_return = ((closes[-1] - closes[-2]) / closes[-2]) if len(closes) >= 2 and closes[-2] else None

    # Volume ratio
    vol_ratio = None
    if len(vols) >= 21:
        baseline = sum(vols[-21:-1]) / 20
        if baseline > 0:
            vol_ratio = vols[-1] / baseline

    # Close-in-range: 0 = closed at day's low, 1 = closed at day's high.
    today_hi = highs[-1]
    today_lo = lows[-1]
    rng = today_hi - today_lo
    close_in_range = ((closes[-1] - today_lo) / rng) if rng > 0 else 0.5
    # Conviction in either direction: top 25% bullish, bottom 25% bearish.
    wick_extreme = close_in_range >= 0.75 or close_in_range <= 0.25

    # Signal 4: close position — bullish if close in upper 60%, bearish if lower 40%
    close_position_bullish = close_in_range >= 0.60
    close_position_bearish = close_in_range <= 0.40
    close_position_fired = close_position_bullish or close_position_bearish

    # Signal 5: price impact — abs return on spike day > 1.5%
    price_impact_fired = today_return is not None and abs(today_return) > 0.015

    signals = [
        _signal(
            "volume",
            f"{_fmt_num(vol_ratio, 2)}×" if vol_ratio is not None else "—",
            vol_ratio is not None and vol_ratio > 2.0,
            f"Today's volume vs trailing 20-day average. Fires when > 2× (real flow, not noise). "
            f"Current: {_fmt_num(vol_ratio, 2)}×.",
        ),
        _signal(
            "today move",
            _fmt_pct(today_return),
            today_return is not None and abs(today_return) > 0.03,
            f"Today's return vs prior close. Fires when |move| > 3%. Current: {_fmt_pct(today_return)}.",
        ),
        _signal(
            "close-at-wick",
            f"{close_in_range:.0%} of range",
            wick_extreme,
            "Where today's close sits inside today's high–low range. Fires when in the top 25% "
            "(close-on-high → bullish conviction) or bottom 25% (close-on-low → bearish). "
            f"Current: {close_in_range:.0%}.",
        ),
        _signal(
            "close position",
            f"{close_in_range:.0%} of range",
            close_position_fired,
            "Close in upper 60% of day's range (bullish) or lower 40% (bearish). Broader test of directional conviction.",
        ),
        _signal(
            "price impact",
            _fmt_pct(today_return),
            price_impact_fired,
            "Absolute return on spike day > 1.5% — volume is moving the price, not just noise.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _m = [
        _mag_volume(vol_ratio, 2.0) if (vol_ratio is not None and vol_ratio > 2.0) else 1.0,
        min(1.5, 1.0 + (abs(today_return or 0) - 0.03) / 0.03 * 0.5) if (today_return is not None and abs(today_return) > 0.03) else 1.0,
        min(1.5, 1.0 + max(0.0, abs(close_in_range - 0.5) - 0.25) / 0.25) if wick_extreme else 1.0,
        min(1.5, 1.0 + max(0.0, abs(close_in_range - 0.5) - 0.1) / 0.4) if close_position_fired else 1.0,
        min(1.5, 1.0 + (abs(today_return or 0) - 0.015) / 0.015) if price_impact_fired else 1.0,
    ]
    c_pct = _conviction_pct(signals, (22.5, 26.25, 26.25, 15.0, 10.0), _m)
    direction_sign = 1 if close_in_range >= 0.5 else -1
    score = (vol_ratio or 0) * abs(today_return or 0) * direction_sign
    sort_key = -score
    # Direction follows the close: top of day's range → bullish flow → calls;
    # bottom of range → bearish flow → puts.
    if close_in_range >= 0.5:
        rec_dir, rec_why = "call", "Closed near the high of the day on unusual volume — bullish flow."
    else:
        rec_dir, rec_why = "put", "Closed near the low of the day on unusual volume — bearish flow."
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("volume_spike", c_pct, rec_dir),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction=rec_dir,
            strike_offset_pct=0.0,
            expiry_lean="near",
            reasoning=(
                f"{rec_why} ATM strike captures direction with maximum gamma. "
                "Near-term weekly: ride the day's flow into the next 1–5 sessions."
            ),
        ),
    }


def _score_pullback(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Price near 20/50d MA + above 200d MA + mild RSI dip. The "buy the dip"
    retail setup — calls on the bounce off the moving average."""
    closes = _closes(bars)
    vols = _volumes(bars)
    last_price = closes[-1] if closes else None
    rsi = _compute_rsi(closes, 14)

    sma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
    sma50 = _mean(closes[-50:]) if len(closes) >= 50 else None
    sma200 = _mean(closes[-200:]) if len(closes) >= 200 else None

    near_ma_pct = None
    if last_price is not None:
        dists = []
        if sma20 is not None:
            dists.append(abs(last_price - sma20) / sma20)
        if sma50 is not None:
            dists.append(abs(last_price - sma50) / sma50)
        if dists:
            near_ma_pct = min(dists)
    near_ma = near_ma_pct is not None and near_ma_pct < 0.03

    above_200 = sma200 is not None and last_price is not None and last_price > sma200

    # Signal 4: above 50d SMA (pullback in uptrend should stay above it)
    above_sma50 = sma50 is not None and last_price is not None and last_price > sma50

    # Signal 5: volume contraction during pullback (healthy dip, not breakdown)
    avg_vol_5d = _mean([float(v) for v in vols[-5:]]) if len(vols) >= 5 else None
    avg_vol_20d = _mean([float(v) for v in vols[-20:]]) if len(vols) >= 20 else None
    vol_contraction_fired = (
        avg_vol_5d is not None and avg_vol_20d is not None
        and avg_vol_20d > 0 and avg_vol_5d < avg_vol_20d * 0.85
    )

    signals = [
        _signal(
            "near 20/50d MA",
            _fmt_pct(near_ma_pct),
            near_ma,
            f"Distance to the closer of the 20d or 50d moving average. Fires when within 3% — price is testing support. "
            f"Current: {_fmt_pct(near_ma_pct)} away.",
        ),
        _signal(
            "uptrend (>200d MA)",
            "yes" if above_200 else "no",
            above_200,
            f"Price > 200-day MA confirms the longer-term uptrend, so the pullback is a dip in a bull, not a downtrend. "
            f"200d MA: {_fmt_num(sma200, 2)}, current: {_fmt_num(last_price, 2)}.",
        ),
        _signal(
            "RSI dip",
            _fmt_num(rsi, 0),
            rsi is not None and 35 <= rsi <= 55,
            f"RSI (14d) in the 35–55 band. Fires when momentum has cooled but isn't crashing. "
            f"Current: {_fmt_num(rsi, 1)}.",
        ),
        _signal(
            "above 50d SMA",
            "yes" if above_sma50 else "no",
            above_sma50,
            f"Pullback in uptrend should stay above 50d SMA. 50d MA: {_fmt_num(sma50, 2)}, current: {_fmt_num(last_price, 2)}.",
        ),
        _signal(
            "vol contraction",
            f"{_fmt_num((avg_vol_5d or 0) / (avg_vol_20d or 1), 2)}×" if avg_vol_20d else "—",
            vol_contraction_fired,
            "Low volume during pullback suggests exhaustion, not distribution.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _near_ma_mag = max(1.0, 1.5 - (near_ma_pct or 0.03) / 0.06) if near_ma else 1.0
    _above200_mag = 1.2 if above_200 else 1.0
    _rsi_pb_mag = max(1.0, 1.0 + abs(45.0 - (rsi or 45)) / 10.0 * 0.5) if (rsi is not None and 35 <= rsi <= 55) else 1.0
    _m = [
        _near_ma_mag, _above200_mag, _rsi_pb_mag,
        1.2 if above_sma50 else 1.0,
        1.2 if vol_contraction_fired else 1.0,
    ]
    c_pct = _conviction_pct(signals, (30.0, 18.75, 26.25, 15.0, 10.0), _m)
    sort_key = near_ma_pct if near_ma_pct is not None else 1.0
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("pullback", c_pct, "call"),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction="call",
            strike_offset_pct=0.0,
            expiry_lean="near",
            reasoning=(
                "Buy-the-dip in an uptrend — price has retraced to support at the "
                "20/50d MA. ATM call captures the bounce with ~0.5 delta. "
                "Near-term weekly: the bounce off support usually happens within 3–5 sessions."
            ),
        ),
    }


def _score_trend_bias(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """50d/200d MA cross context + accelerating gap. Slow, strategic signal —
    pairs naturally with longer-dated options. Direction follows whichever side
    the 50d is on relative to the 200d.

    All three signals look the same in either direction; the user reads the
    sign of the chip values to know which way to lean.
    """
    closes = _closes(bars)
    last_price = closes[-1] if closes else None

    if len(closes) < 210:
        return {
            "conviction": 0,
            "signals": [
                _signal("50/200d cross", "—", False, "Need ≥210 bars of history to compute the 200d MA."),
                _signal("price vs 50d MA", "—", False, "Need ≥50 bars of history."),
                _signal("gap widening", "—", False, "Need ≥10 bars of history past the cross."),
                _signal("MA separation", "—", False, "Need ≥210 bars of history."),
                _signal("RSI bias", "—", False, "Need ≥210 bars of history."),
            ],
            "sort_key": 0.0,
            "last_price": last_price,
            "recommendation": _recommendation(
                direction="call",
                strike_offset_pct=0.0,
                expiry_lean="far",
                reasoning="Not enough history to compute a Trend Bias recommendation.",
            ),
        }

    sma50_now = _mean(closes[-50:])
    sma200_now = _mean(closes[-200:])
    cross_gap = sma50_now - sma200_now
    cross_state = "Golden" if cross_gap > 0 else "Death"

    sma50_then = _mean(closes[-60:-10])
    sma200_then = _mean(closes[-210:-10])
    cross_gap_then = sma50_then - sma200_then

    # Widening: gap has grown (in the direction of the current cross) over the last 10 days.
    widening = (cross_gap - cross_gap_then) * (1 if cross_gap > 0 else -1) > 0

    price_vs_50 = (last_price - sma50_now) / sma50_now if sma50_now > 0 else 0.0
    price_trend_aligned = (price_vs_50 > 0 and cross_gap > 0) or (price_vs_50 < 0 and cross_gap < 0)

    # Signal 4: MA separation — (sma50 - sma200) / sma200
    ma_sep = (sma50_now - sma200_now) / sma200_now if sma200_now and sma200_now > 0 else 0.0
    # Bullish bias: ma_sep > 0.02 (golden cross with >2% separation)
    # Bearish bias: ma_sep < -0.02 (death cross with >2% separation)
    ma_sep_fired = abs(ma_sep) > 0.02

    # Signal 5: RSI bias — above 55 for bullish, below 45 for bearish
    rsi = _compute_rsi(closes, 14)
    rsi_bias_bullish = cross_gap > 0 and rsi is not None and rsi > 55
    rsi_bias_bearish = cross_gap < 0 and rsi is not None and rsi < 45
    rsi_bias_fired = rsi_bias_bullish or rsi_bias_bearish

    signals = [
        _signal(
            "50/200d cross",
            cross_state,
            True,  # always informational — fired = "we have a stance"
            f"50d MA {_fmt_num(sma50_now, 2)} vs 200d MA {_fmt_num(sma200_now, 2)}. "
            f"50d above = Golden Cross (uptrend); below = Death Cross (downtrend). "
            f"Current gap: {_fmt_num(cross_gap, 2)}.",
        ),
        _signal(
            "price vs 50d",
            _fmt_pct(price_vs_50),
            price_trend_aligned,
            f"Price {_fmt_pct(price_vs_50)} from 50d MA. Fires when price is on the same side of the 50d MA as "
            f"the trend (above in Golden, below in Death) — riding the trend, not fading it.",
        ),
        _signal(
            "trend accelerating",
            "yes" if widening else "no",
            widening,
            f"50d–200d gap has widened in the trend's direction over the last 10 days. "
            f"Fires when the trend is gaining strength, not stalling.",
        ),
        _signal(
            "MA separation",
            _fmt_pct(ma_sep),
            ma_sep_fired,
            f"(50d MA - 200d MA) / 200d MA. Fires when separation > 2% in either direction — meaningful structural gap.",
        ),
        _signal(
            "RSI bias",
            _fmt_num(rsi, 0),
            rsi_bias_fired,
            "RSI > 55 in a Golden Cross (bullish momentum) or RSI < 45 in a Death Cross (bearish momentum). Confirms trend.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _gap_mag = min(1.5, 1.0 + abs(cross_gap) / max(sma200_now or 1, 1) * 3.0)
    _aligned_mag = 1.2 if price_trend_aligned else 1.0
    _widen_mag = 1.2 if widening else 1.0
    _m = [
        _gap_mag, _aligned_mag, _widen_mag,
        min(1.5, 1.0 + (abs(ma_sep) - 0.02) / 0.02) if ma_sep_fired else 1.0,
        _mag_rsi(rsi, 55.0 if cross_gap > 0 else 45.0) if rsi_bias_fired else 1.0,
    ]
    c_pct = _conviction_pct(signals, (30.0, 26.25, 18.75, 15.0, 10.0), _m)
    sort_key = -abs(cross_gap) if cross_gap is not None else 0.0
    # Direction follows the cross state.
    if cross_gap > 0:
        rec_dir, cross_label = "call", "Golden Cross"
    else:
        rec_dir, cross_label = "put", "Death Cross"
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("trend_bias", c_pct, rec_dir),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction=rec_dir,
            strike_offset_pct=0.0,
            expiry_lean="far",
            reasoning=(
                f"{cross_label} regime — long-term trend signal. ATM strike with "
                "a 1–2 month expiry: this is a strategic position, not a tactical "
                "one. Give it time to play out."
            ),
        ),
    }


def _realized_vol(closes: list[float], window: int) -> float | None:
    """Annualized realized vol from log returns over the trailing ``window`` bars.

    Returns ``None`` when there isn't enough history. Annualized via √252.
    """
    if len(closes) <= window:
        return None
    rets: list[float] = []
    for i in range(len(closes) - window, len(closes)):
        if i == 0 or closes[i - 1] <= 0:
            continue
        rets.append(math.log(closes[i] / closes[i - 1]))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def _score_vol_expansion(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
    """Realized-vol regime change. Surfaces names where short-term vol has
    blown out relative to the trailing baseline — options are likely rich
    and the underlying is moving enough to make either premium-selling or
    long-vol plays interesting.

    We use realized vol as an IV proxy because the screener doesn't have
    historical IV rank wired (would need a separate vol-history pipeline).
    Realized-vol expansion is a strong leading indicator of IV expansion in
    practice.
    """
    closes = _closes(bars)
    last_price = closes[-1] if closes else None
    rv5 = _realized_vol(closes, 5)
    rv30 = _realized_vol(closes, 30)
    today_return = ((closes[-1] - closes[-2]) / closes[-2]) if len(closes) >= 2 and closes[-2] else None
    vol_ratio = (rv5 / rv30) if (rv5 is not None and rv30 is not None and rv30 > 0) else None

    # Signal 4: ATR expansion — 5d avg ATR vs 20d avg ATR
    highs = _highs(bars)
    lows = _lows(bars)
    atr_5d: float | None = None
    atr_20d: float | None = None
    if len(highs) >= 21 and len(lows) >= 21 and len(closes) >= 21:
        tr_series = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(-20, 0)
        ]
        atr_5d = _mean(tr_series[-5:])
        atr_20d = _mean(tr_series)
    atr_expansion_fired = (
        atr_5d is not None and atr_20d is not None
        and atr_20d > 0 and atr_5d / atr_20d > 1.2
    )
    atr_ratio = (atr_5d / atr_20d) if (atr_5d and atr_20d and atr_20d > 0) else None

    # Signal 5: Bollinger Band width (20d, 2σ)
    bb_width: float | None = None
    bb_recent_avg: float | None = None
    if len(closes) >= 40:
        _bb_mid_now = _mean(closes[-20:])
        _bb_sd_now = _stddev(closes[-20:], _bb_mid_now)
        if _bb_mid_now > 0:
            bb_width = (4.0 * _bb_sd_now) / _bb_mid_now  # (upper-lower)/mid
        # Use 20-bar rolling average of BB width from bars -40..-20 as baseline
        _bb_mid_prev = _mean(closes[-40:-20])
        _bb_sd_prev = _stddev(closes[-40:-20], _bb_mid_prev)
        if _bb_mid_prev > 0:
            bb_recent_avg = (4.0 * _bb_sd_prev) / _bb_mid_prev
    bb_width_fired = (
        bb_width is not None and bb_recent_avg is not None
        and bb_recent_avg > 0 and bb_width > bb_recent_avg
    )

    signals = [
        _signal(
            "vol expansion",
            f"{_fmt_num(vol_ratio, 2)}×" if vol_ratio is not None else "—",
            vol_ratio is not None and vol_ratio > 1.5,
            f"5-day realized vol vs trailing 30-day. Fires when > 1.5× (regime change in motion). "
            f"5d realized vol {_fmt_num((rv5 or 0) * 100, 1)}% annualized, 30d {_fmt_num((rv30 or 0) * 100, 1)}%.",
        ),
        _signal(
            "5d realized vol",
            f"{_fmt_num((rv5 or 0) * 100, 0)}%",
            rv5 is not None and rv5 > 0.40,
            f"Annualized 5-day realized vol. Fires when > 40% (premium is rich in absolute terms — good for selling).",
        ),
        _signal(
            "today move",
            _fmt_pct(today_return),
            today_return is not None and abs(today_return) > 0.02,
            f"Today's return vs prior close. Fires when |move| > 2% (the trigger that's driving the vol regime change).",
        ),
        _signal(
            "ATR expansion",
            f"{_fmt_num(atr_ratio, 2)}×" if atr_ratio is not None else "—",
            atr_expansion_fired,
            "5d avg true range vs 20d avg true range. Fires when ratio > 1.2 — price bars are expanding, vol regime is shifting.",
        ),
        _signal(
            "BB width",
            _fmt_pct(bb_width),
            bb_width_fired,
            "20d Bollinger Band width ((upper-lower)/mid). Fires when current width exceeds the prior 20d average — bands are expanding.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _m = [
        _mag_volume(vol_ratio, 1.5) if (vol_ratio is not None and vol_ratio > 1.5) else 1.0,
        min(1.5, 1.0 + ((rv5 or 0) - 0.40) / 0.40 * 0.5) if (rv5 is not None and rv5 > 0.40) else 1.0,
        min(1.5, 1.0 + (abs(today_return or 0) - 0.02) / 0.02 * 0.5) if (today_return is not None and abs(today_return) > 0.02) else 1.0,
        min(1.5, 1.0 + (atr_ratio - 1.2) / 0.3) if (atr_ratio and atr_expansion_fired) else 1.0,
        min(1.5, 1.0 + (bb_width - bb_recent_avg) / (bb_recent_avg or 0.01)) if (bb_width and bb_recent_avg and bb_width_fired) else 1.0,
    ]
    c_pct = _conviction_pct(signals, (33.75, 22.5, 18.75, 15.0, 10.0), _m)
    sort_key = -rv5 if rv5 is not None else 0.0
    # Direction follows today's trigger move — wherever the regime is breaking.
    if today_return is not None and today_return >= 0:
        rec_dir, rec_why = "call", "Vol regime expanding to the upside — follow with calls."
    else:
        rec_dir, rec_why = "put", "Vol regime expanding to the downside — follow with puts."
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("vol_expansion", c_pct, rec_dir),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction=rec_dir,
            strike_offset_pct=0.0,
            expiry_lean="near",
            reasoning=(
                f"{rec_why} ATM strike for maximum gamma into the next move. "
                "Near-term weekly: realized vol blowouts usually mean-revert within "
                "1–2 weeks, so don't pay for more time than you need."
            ),
        ),
    }


def _score_unusual_options_activity(
    bars: list,
    _qqq_bars: list,
    *,
    ticker: str | None = None,
    client: MassiveClient | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Unusual options activity — scans the underlying's chain looking for
    individual contracts with volume that significantly exceeds open interest
    (vol/OI > 2 = new positioning, often called 'sweep' or 'flow').

    Cost: one chain HTTP call per ticker. The screener caches its full result
    so a refresh only pays once per (sleeve, strategy, min_price) per 5 min.

    Surfaces tickers where smart-money may be establishing a directional
    position. The chain viewer below the card shows the actual contracts so
    the user can spot which strikes are being bought.
    """
    closes = _closes(bars)
    last_price = closes[-1] if closes else None

    # Degenerate / harness inputs: return an empty scorecard rather than
    # making an HTTP call we can't satisfy.
    if ticker is None or client is None or last_price is None:
        return {
            "conviction": 0,
            "conviction_pct": 0.0,
            "expiry_tiers": [],
            "signals": [
                _signal("max vol/OI", "—", False, "No chain context available."),
                _signal("max contract vol", "—", False, "No chain context available."),
                _signal("OTM concentration", "—", False, "No chain context available."),
                _signal("vol/OI ratio", "—", False, "No chain context available."),
                _signal("IV rank", "—", False, "No chain context available."),
            ],
            "sort_key": 0.0,
            "last_price": last_price,
            "recommendation": _recommendation(
                direction="call",
                strike_offset_pct=0.0,
                expiry_lean="near",
                reasoning="No chain data available — recommendation unavailable.",
            ),
        }

    # Pull the chain across the next 30 days at a wider strike band (±15%)
    # so we catch OTM activity — speculative flow lives OTM.
    today = datetime.date.today()
    horizon_end = (today + datetime.timedelta(days=30)).isoformat()
    low_strike = last_price * 0.85
    high_strike = last_price * 1.15

    try:
        raw = client.get_options_chain(
            ticker,
            expiration_date_gte=today.isoformat(),
            expiration_date_lte=horizon_end,
            strike_price_gte=low_strike,
            strike_price_lte=high_strike,
            limit=250,
        )
    except MassiveError as exc:
        logger.warning("UOA chain fetch failed for %s: %s", ticker, exc)
        return {
            "conviction": 0,
            "conviction_pct": 0.0,
            "expiry_tiers": [],
            "signals": [
                _signal("max vol/OI", "—", False, f"Chain fetch failed: {exc}"),
                _signal("max contract vol", "—", False, ""),
                _signal("OTM concentration", "—", False, ""),
                _signal("vol/OI ratio", "—", False, ""),
                _signal("IV rank", "—", False, ""),
            ],
            "sort_key": 0.0,
            "last_price": last_price,
            "recommendation": _recommendation(
                direction="call",
                strike_offset_pct=0.0,
                expiry_lean="near",
                reasoning=f"Chain fetch failed — recommendation unavailable. {exc}",
            ),
        }

    rows = raw.get("results") or []
    if not rows:
        return {
            "conviction": 0,
            "conviction_pct": 0.0,
            "expiry_tiers": [],
            "signals": [
                _signal("max vol/OI", "—", False, "No contracts in window."),
                _signal("max contract vol", "—", False, ""),
                _signal("OTM concentration", "—", False, ""),
                _signal("vol/OI ratio", "—", False, ""),
                _signal("IV rank", "—", False, ""),
            ],
            "sort_key": 0.0,
            "last_price": last_price,
            "recommendation": _recommendation(
                direction="call",
                strike_offset_pct=0.0,
                expiry_lean="near",
                reasoning="No contracts in the scan window — recommendation unavailable.",
            ),
        }

    max_vol_oi = 0.0
    max_contract_vol = 0
    total_vol = 0
    otm_vol = 0
    otm_call_vol = 0
    otm_put_vol = 0
    for r in rows:
        day = r.get("day") or {}
        vol = day.get("volume") or 0
        oi = r.get("open_interest") or 0
        details = r.get("details") or {}
        strike = details.get("strike_price")
        contract_type = details.get("contract_type")
        if vol > max_contract_vol:
            max_contract_vol = vol
        if oi > 0 and vol > 0:
            ratio = vol / oi
            if ratio > max_vol_oi:
                max_vol_oi = ratio
        total_vol += vol
        if strike is not None and contract_type is not None:
            is_otm_call = contract_type == "call" and strike > last_price
            is_otm_put = contract_type == "put" and strike < last_price
            if is_otm_call:
                otm_vol += vol
                otm_call_vol += vol
            elif is_otm_put:
                otm_vol += vol
                otm_put_vol += vol

    otm_pct = (otm_vol / total_vol) if total_vol > 0 else None

    # Signal 4: vol/OI ratio across all contracts (>0.3 = unusual relative activity)
    total_oi = sum(r.get("open_interest") or 0 for r in rows)
    agg_vol_oi_ratio = (total_vol / total_oi) if total_oi > 0 else None
    vol_oi_ratio_fired = agg_vol_oi_ratio is not None and agg_vol_oi_ratio > 0.3

    # Signal 5: IV rank proxy — current avg IV vs trailing estimate from chain
    # Use the avg mid-IV from contracts that have it; if unavailable, never fires.
    iv_samples: list[float] = []
    for r in rows:
        greeks = r.get("greeks") or {}
        implied_vol = greeks.get("iv") or r.get("implied_volatility")
        if implied_vol and isinstance(implied_vol, (int, float)) and implied_vol > 0:
            iv_samples.append(float(implied_vol))
    current_iv: float | None = (_mean(iv_samples) if iv_samples else None)
    iv_rank_fired = False  # placeholder that never fires when chain IV is unavailable
    iv_rank_text = "—"
    if current_iv is not None:
        # Basic signal: fire when IV > 50% annualized (unusually high)
        iv_rank_fired = current_iv > 0.50
        iv_rank_text = f"{current_iv * 100:.0f}%"

    signals = [
        _signal(
            "max vol/OI",
            f"{_fmt_num(max_vol_oi, 2)}×",
            max_vol_oi > 2.0,
            f"Highest volume-to-open-interest ratio on any contract in the chain. Fires when > 2× — "
            f"contracts are trading at double their outstanding count, a sign new positions are being opened.",
        ),
        _signal(
            "max contract vol",
            f"{max_contract_vol:,}" if max_contract_vol < 10000 else f"{max_contract_vol / 1000:.1f}k",
            max_contract_vol > 500,
            f"Single most-traded contract today. Fires when > 500 contracts — real flow, not noise. "
            f"Open the chain to see which strikes are seeing the action.",
        ),
        _signal(
            "OTM concentration",
            _fmt_pct(otm_pct),
            otm_pct is not None and otm_pct > 0.6,
            f"Share of today's volume in out-of-the-money strikes. Fires when > 60% — speculative directional bets, "
            f"not boring at-the-money rolls. Current: {_fmt_pct(otm_pct)} of {total_vol:,} contracts.",
        ),
        _signal(
            "vol/OI ratio",
            f"{_fmt_num(agg_vol_oi_ratio, 2)}×" if agg_vol_oi_ratio is not None else "—",
            vol_oi_ratio_fired,
            "Total volume / total open interest across the chain. Fires when > 0.3 — unusual activity relative to float.",
        ),
        _signal(
            "IV rank",
            iv_rank_text,
            iv_rank_fired,
            "Current average implied volatility from chain data. Fires when IV > 50% annualized — options are rich, confirming unusual activity.",
        ),
    ]
    conviction = sum(1 for s in signals if s["fired"])
    _m = [
        min(1.5, 1.0 + (max_vol_oi - 2.0) / 4.0) if max_vol_oi > 2.0 else 1.0,
        1.2 if max_contract_vol > 500 else 1.0,
        1.2 if (otm_pct is not None and otm_pct > 0.6) else 1.0,
        min(1.5, 1.0 + (agg_vol_oi_ratio - 0.3) / 0.3) if vol_oi_ratio_fired else 1.0,
        min(1.5, 1.0 + (current_iv - 0.50) / 0.25) if (current_iv and iv_rank_fired) else 1.0,
    ]
    c_pct = _conviction_pct(signals, (30.0, 22.5, 22.5, 15.0, 10.0), _m)
    sort_key = -max_vol_oi
    # Recommendation: follow the OTM volume skew. More OTM call volume = smart
    # money is betting up. More OTM put volume = betting down.
    if otm_call_vol > otm_put_vol * 1.2:
        rec_dir, rec_why = "call", f"OTM call volume ({otm_call_vol:,}) significantly exceeds OTM put volume ({otm_put_vol:,}) — flow is bullish."
    elif otm_put_vol > otm_call_vol * 1.2:
        rec_dir, rec_why = "put", f"OTM put volume ({otm_put_vol:,}) significantly exceeds OTM call volume ({otm_call_vol:,}) — flow is bearish."
    else:
        rec_dir, rec_why = "call", f"OTM call/put volume mixed ({otm_call_vol:,} calls vs {otm_put_vol:,} puts). Default to calls; check the chain to confirm direction."
    return {
        "conviction": conviction,
        "conviction_pct": c_pct,
        "expiry_tiers": _expiry_tiers("unusual_options_activity", c_pct, rec_dir),
        "signals": signals,
        "sort_key": sort_key,
        "last_price": last_price,
        "recommendation": _recommendation(
            direction=rec_dir,
            strike_offset_pct=0.0,
            expiry_lean="near",
            reasoning=(
                f"{rec_why} Highlighted ATM strike anchors your position; open the "
                "chain and look for the specific OTM strikes with the highest vol/OI — "
                "that's where the conviction lies."
            ),
        ),
    }


def _bars_to_candles(bars: list) -> list[dict]:
    """Convert Price bar objects to candle dicts the pattern engine expects."""
    return [
        {
            "date": b.time,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        }
        for b in bars
    ]


def _make_pattern_scorer(pattern_name: str, detector_fn: Any, is_bullish: bool):
    """Return a strategy scorer closure for a single chart pattern.

    Runs the detector on the most recent 120 bars and checks whether a
    confirmed breakout completed within the last 10 bars. Three binary
    signals drive the 0–3 conviction score.
    """
    _direction = "call" if is_bullish else "put"
    _bias = "bullish" if is_bullish else "bearish"
    _strike_offset = 0.02 if is_bullish else -0.02
    _reasoning = (
        f"{pattern_name} breakout detected — {_bias} continuation setup. "
        f"Strike slightly {'above' if is_bullish else 'below'} spot to capture "
        "the post-breakout move. Medium-term expiry (3–5 weeks) gives the "
        "pattern room to develop without excessive theta drag."
    )

    def _scorer(bars: list, _qqq_bars: list, **_kwargs: Any) -> dict[str, Any]:
        last_price: float | None = bars[-1].close if bars else None

        candles = _bars_to_candles(bars[-120:])
        try:
            detections = detector_fn(candles)
        except Exception:
            detections = []

        bar_dates = [b.time for b in bars[-120:]]
        recent: list[dict] = []
        for det in detections:
            try:
                idx = bar_dates.index(det["end_date"])
            except ValueError:
                continue
            bars_ago = len(bar_dates) - 1 - idx
            if bars_ago <= 10:
                recent.append({**det, "_bars_ago": bars_ago})

        recent.sort(key=lambda x: -x["confidence"])
        best = recent[0] if recent else None
        best_conf = best["confidence"] if best else 0.0
        best_bars_ago = best["_bars_ago"] if best else 999

        detected = best is not None
        high_conf = best_conf >= 60
        fresh = detected and best_bars_ago <= 5

        signals = [
            _signal(
                "Pattern Detected",
                f"{best_conf:.0f}%" if detected else "—",
                detected,
                (
                    f"{pattern_name} confirmed within the last 10 bars at "
                    f"{best_conf:.0f}% confidence. Fires whenever a completed "
                    "breakout is present in the recent window."
                ),
            ),
            _signal(
                "Strong Signal",
                f"{best_conf:.0f}%" if detected else "—",
                high_conf,
                (
                    f"Confidence ≥ 60 (current: {best_conf:.0f}%). High-confidence "
                    "detections have well-formed geometry and volume confirmation."
                ),
            ),
            _signal(
                "Recent Breakout",
                f"{best_bars_ago}d ago" if detected else "—",
                fresh,
                (
                    f"Breakout within the last 5 bars (currently {best_bars_ago} ago). "
                    "Fresher breakouts capture more of the post-pattern momentum."
                ),
            ),
        ]
        conviction = sum(1 for s in signals if s["fired"])

        return {
            "conviction": conviction,
            "signals": signals,
            "sort_key": -best_conf,
            "last_price": last_price,
            "recommendation": _recommendation(
                direction=_direction,
                strike_offset_pct=_strike_offset,
                expiry_lean="medium",
                reasoning=_reasoning,
            ),
        }

    return _scorer


_STRATEGY_REGISTRY: dict[str, dict[str, Any]] = {
    "weakness": {
        "label": "Weakness",
        "subtitle": "lagging QQQ + oversold",
        "description": "Names trailing QQQ that are oversold. Natural plays: bounce calls or continuation puts.",
        "scorer": _score_weakness,
    },
    "strength": {
        "label": "Strength",
        "subtitle": "leading QQQ + overbought",
        "description": "Names beating QQQ that are overbought. Natural plays: breakout calls or mean-reversion puts.",
        "scorer": _score_strength,
    },
    "momentum": {
        "label": "Momentum",
        "subtitle": "strong absolute trend",
        "description": "Pure trend-follow, no benchmark. Up >5% over 20d and >2% over 5d with RSI above 60. Plays: ride the trend with calls.",
        "scorer": _score_momentum,
    },
    "mean_reversion": {
        "label": "Mean Reversion",
        "subtitle": "stretched from 20d mean",
        "description": "Price >1.5 σ from its 20-day mean + RSI extreme. Bet the move overshoots and snaps back. Direction depends on which side it's stretched.",
        "scorer": _score_mean_reversion,
    },
    "breakout": {
        "label": "Breakout",
        "subtitle": "near 52w high + volume",
        "description": "Near 52-week high + volume surge + RSI > 60. Classic momentum continuation. Plays: calls.",
        "scorer": _score_breakout,
    },
    "breakdown": {
        "label": "Breakdown",
        "subtitle": "near 52w low + volume",
        "description": "Near 52-week low + volume surge + downside momentum. Bearish mirror of Breakout. Plays: puts.",
        "scorer": _score_breakdown,
    },
    "volume_spike": {
        "label": "Volume Spike",
        "subtitle": "unusual volume + big move",
        "description": "Today's volume > 2× trailing average + |move| > 3% + close at the wick extreme. Direction-agnostic — pick calls or puts based on the move's direction.",
        "scorer": _score_volume_spike,
    },
    "pullback": {
        "label": "Pullback",
        "subtitle": "dip in an uptrend",
        "description": "Price within 3% of 20d or 50d MA, still above 200d MA, RSI in 35–55 (mild dip). The 'buy the dip' setup. Plays: calls on the bounce.",
        "scorer": _score_pullback,
    },
    "trend_bias": {
        "label": "Trend Bias",
        "subtitle": "50/200d MA cross context",
        "description": "Golden/Death cross + price riding the trend + accelerating gap. Slower, strategic signal — good for longer-dated calls (Golden) or puts (Death).",
        "scorer": _score_trend_bias,
    },
    "vol_expansion": {
        "label": "Vol Expansion",
        "subtitle": "realized vol regime change",
        "description": "5-day realized vol vs 30-day baseline. Surfaces names where the vol regime is changing. High realized vol = options premium is rich (sell premium); paired with a fresh big move = continuation candidate.",
        "scorer": _score_vol_expansion,
    },
    "unusual_options_activity": {
        "label": "Unusual Options Activity",
        "subtitle": "vol/OI extremes in the chain",
        "description": "Scans the underlying's option chain for individual contracts with volume far above open interest — a sign of new directional positioning. Plays: follow the flow (calls if OTM call activity, puts if OTM puts).",
        "scorer": _score_unusual_options_activity,
    },
}

_PATTERN_DESCRIPTIONS: dict[str, str] = {
    "Bullish Flag": (
        "Sharp pole up followed by a tight, slightly downward-sloping consolidation. "
        "Breakout above the channel signals continuation. Plays: calls."
    ),
    "Bearish Flag": (
        "Sharp pole down followed by a tight, slightly upward-sloping consolidation. "
        "Breakdown below the channel signals continuation. Plays: puts."
    ),
    "Bull Pennant": (
        "Explosive move up (pole) into a symmetrical triangle consolidation. "
        "Volume contracts during the pennant; breakout on a volume surge. Plays: calls."
    ),
    "Double Bottom": (
        "Two roughly equal lows with a moderate bounce between them. "
        "Neckline breakout confirms the reversal from downtrend to uptrend. Plays: calls."
    ),
    "Double Top": (
        "Two roughly equal highs with a moderate pullback between them. "
        "Neckline breakdown confirms the reversal from uptrend to downtrend. Plays: puts."
    ),
    "Head and Shoulders": (
        "Three-peak top (left shoulder, higher head, right shoulder) with a neckline. "
        "Breakdown below the neckline is bearish. Plays: puts."
    ),
    "Inverse Head and Shoulders": (
        "Three-trough bottom (left shoulder, lower head, right shoulder). "
        "Breakout above the neckline is bullish. Plays: calls."
    ),
    "Ascending Triangle": (
        "Flat resistance + rising support — buyers pressing harder each swing. "
        "Breakout above resistance on volume confirms continuation. Plays: calls."
    ),
    "Descending Triangle": (
        "Flat support + descending resistance — sellers pressing harder each swing. "
        "Breakdown below support on volume confirms continuation. Plays: puts."
    ),
    "Cup and Handle": (
        "Rounded U-shaped base (cup) followed by a small consolidation dip (handle). "
        "Breakout above the rim is a classic bull signal. Plays: calls."
    ),
    "Rising Wedge": (
        "Price compressed into a rising channel with converging trendlines. "
        "Bearish resolution — prices typically break down. Plays: puts."
    ),
    "Falling Wedge": (
        "Price compressed into a falling channel with converging trendlines. "
        "Bullish resolution — prices typically break upward. Plays: calls."
    ),
}


def _pattern_key(name: str) -> str:
    """Normalize a pattern display name to a registry key."""
    return "pattern_" + name.lower().replace(" ", "_").replace("&", "and").replace("-", "_")


_VALID_STRATEGIES = set(_STRATEGY_REGISTRY.keys())


# Legacy single-strategy scorer kept for the BSM options-strategy backtest,
# which calls it with closes-only lists. Internally builds bar-shaped objects
# so the bars-based scorer works without an HTTP roundtrip.
class _ClosesOnlyBar:
    __slots__ = ("close", "high", "low", "volume")

    def __init__(self, close: float):
        self.close = close
        self.high = close
        self.low = close
        self.volume = 0


def _score_ticker(closes: list[float], qqq_closes: list[float]) -> dict[str, Any]:
    bars = [_ClosesOnlyBar(c) for c in closes]
    qqq_bars = [_ClosesOnlyBar(c) for c in qqq_closes]
    return _score_weakness(bars, qqq_bars)


# Ordered registry for UI rendering. Adding a strategy here + its scorer
# above is the only change needed to surface a new pill.
_STRATEGY_ORDER = [
    "weakness",
    "strength",
    "momentum",
    "mean_reversion",
    "breakout",
    "breakdown",
    "volume_spike",
    "pullback",
    "trend_bias",
    "vol_expansion",
    "unusual_options_activity",
]


@router.get("/options/strategies")
async def list_options_strategies() -> dict[str, Any]:
    """Catalog of available screener strategies, in display order."""
    return {
        "strategies": [
            {
                "key": k,
                "label": _STRATEGY_REGISTRY[k]["label"],
                "subtitle": _STRATEGY_REGISTRY[k]["subtitle"],
                "description": _STRATEGY_REGISTRY[k]["description"],
            }
            for k in _STRATEGY_ORDER
            if k in _STRATEGY_REGISTRY
        ]
    }


# Default min-price filter: skip names under $10 since their option chains
# typically have brutal bid-ask spreads. Configurable per request.
_DEFAULT_MIN_PRICE = 10.0


@router.get("/options/screener")
async def get_options_screener(
    sleeve: str = "mega_tech",
    strategy: str = "weakness",
    min_price: float = _DEFAULT_MIN_PRICE,
) -> dict[str, Any]:
    """Rank a sleeve's tickers by a 0–3 conviction score under a strategy.

    Strategies are registered in ``_STRATEGY_REGISTRY``; each defines its own
    three signals + sort order. See ``GET /sleeves/options/strategies`` for
    the live catalog.

    ``min_price`` filters out tickers whose last close is under that dollar
    amount before scoring — keeps the list focused on names with tradeable
    option chains. Default $10. Set to 0 to disable.

    Sort: conviction desc, then by per-strategy extremity. Cached per
    (sleeve, strategy, min_price) for 5 minutes.
    """
    sleeves_cfg = _live_sleeves()
    if sleeve not in sleeves_cfg:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown sleeve '{sleeve}'. Known: {list(sleeves_cfg.keys())}",
        )
    if strategy not in _VALID_STRATEGIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy '{strategy}'. Known: {sorted(_VALID_STRATEGIES)}",
        )

    cache_key = f"{sleeve}|{strategy}|{min_price}"
    now = time.monotonic()
    cached = _screener_cache.get(cache_key)
    if cached and (now - cached[0]) < _SCREENER_CACHE_TTL_SECONDS:
        return cached[1]

    today = datetime.date.today()
    end_date = today.isoformat()
    # 400 calendar days back: ~280 trading bars, plenty for 52-week and
    # 200d-MA lookbacks. Polygon caps at 5000 bars/call so this is comfortable.
    start_date = (today - datetime.timedelta(days=400)).isoformat()

    tickers = list(sleeves_cfg[sleeve]["tickers"])
    scorer = _STRATEGY_REGISTRY[strategy]["scorer"]

    def _compute() -> dict[str, Any]:
        client = MassiveClient()
        qqq_bars = _fetch_bars(client, _BENCHMARK_TICKER, start_date, end_date)
        if not qqq_bars:
            raise MassiveError(0, f"failed to fetch {_BENCHMARK_TICKER} benchmark prices", "")

        candidates: list[dict[str, Any]] = []
        for ticker in tickers:
            bars = _fetch_bars(client, ticker, start_date, end_date)
            if not bars:
                continue
            # Price filter — skip cheap names where the option chain is unusable.
            last_price = bars[-1].close
            if min_price > 0 and last_price < min_price:
                continue
            # Pass ticker + client so strategies that need the options chain
            # (e.g. unusual_options_activity) can fetch it. Most strategies
            # ignore the extras via **_kwargs.
            scored = scorer(bars, qqq_bars, ticker=ticker, client=client)
            candidates.append({"ticker": ticker, **scored})

        candidates.sort(key=lambda c: (-c["conviction"], c["sort_key"]))
        return {
            "sleeve": sleeve,
            "strategy": strategy,
            "benchmark": _BENCHMARK_TICKER,
            "min_price": min_price,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "candidates": candidates,
        }

    try:
        payload = await asyncio.to_thread(_compute)
    except MassiveError as exc:
        raise HTTPException(status_code=502, detail=f"Massive: {exc}")

    _screener_cache[cache_key] = (now, payload)
    return payload


@router.get("/options/chain/{ticker}")
async def get_options_chain(
    ticker: str,
    *,
    atm_window_pct: float = 2.0,
    expiration: str | None = None,
    horizon_days: int = 60,
) -> dict[str, Any]:
    """Calls + puts near spot for ``ticker``.

    Returns:
    * ``expiration`` — the expiry actually rendered (the one requested, or
      nearest available if ``expiration`` was None).
    * ``available_expirations`` — every expiry in the next ``horizon_days``
      that has at least one strike inside the ATM window. Frontends use this
      to populate an expiry dropdown.
    * ``calls`` / ``puts`` — flattened contracts at the selected expiry only.

    Pulls one snapshot, partitions client-side. Caches per
    (ticker, atm_window_pct, expiration, horizon_days) for 60 seconds.
    """
    symbol = (ticker or "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Ticker is required.")
    if horizon_days < 1 or horizon_days > 365:
        raise HTTPException(status_code=400, detail="horizon_days must be 1..365.")
    if expiration:
        try:
            datetime.date.fromisoformat(expiration)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Bad expiration: {exc}")

    cache_key = f"{symbol}|{atm_window_pct}|{expiration or ''}|{horizon_days}"
    now = time.monotonic()
    cached = _chain_cache.get(cache_key)
    if cached and (now - cached[0]) < _CHAIN_CACHE_TTL_SECONDS:
        return cached[1]

    today = datetime.date.today()

    def _compute() -> dict[str, Any]:
        client = MassiveClient()

        spot_closes = _fetch_closes(
            client,
            symbol,
            (today - datetime.timedelta(days=10)).isoformat(),
            today.isoformat(),
        )
        if not spot_closes:
            raise MassiveError(0, f"failed to resolve spot for {symbol}", "")
        spot = spot_closes[-1]

        low = spot * (1 - atm_window_pct / 100.0)
        high = spot * (1 + atm_window_pct / 100.0)

        # Pull all near-the-money contracts across the horizon in one call.
        # Polygon caps at limit=250 — for highly liquid mega-tech names with
        # weekly expiries over a long horizon, this can clip. Keeping 60d
        # default is a comfortable balance for the UI use case.
        chain = client.get_options_chain(
            symbol,
            expiration_date_gte=today.isoformat(),
            expiration_date_lte=(today + datetime.timedelta(days=horizon_days)).isoformat(),
            strike_price_gte=low,
            strike_price_lte=high,
            limit=250,
        )
        rows = chain.get("results") or []
        expiries = sorted({(r.get("details") or {}).get("expiration_date") for r in rows if r.get("details")})
        expiries = [e for e in expiries if e]

        # Filter out 0DTE and near-expiry contracts: require at least 7 calendar
        # days from today so the chain viewer never shows same-day or next-day
        # expiries that have near-zero time value and brutal bid-ask spreads.
        from datetime import timedelta as _timedelta
        _min_exp = (today + _timedelta(days=7)).strftime("%Y-%m-%d")
        filtered_expiries = [e for e in expiries if e >= _min_exp]
        # Fall back to raw list if the filter empties it (e.g. far-OTM scan).
        if filtered_expiries:
            expiries = filtered_expiries

        # Resolve the expiration to render: explicit request → nearest available.
        selected = expiration if expiration in expiries else (expiries[0] if expiries else None)
        filtered = (
            [r for r in rows if (r.get("details") or {}).get("expiration_date") == selected]
            if selected else []
        )

        calls, puts = split_calls_puts(filtered)

        return {
            "ticker": symbol,
            "spot": spot,
            "expiration": selected,
            "available_expirations": expiries,
            "atm_window_pct": atm_window_pct,
            "horizon_days": horizon_days,
            "strike_low": low,
            "strike_high": high,
            "calls": calls,
            "puts": puts,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    try:
        payload = await asyncio.to_thread(_compute)
    except MassiveError as exc:
        raise HTTPException(status_code=502, detail=f"Massive: {exc}")

    _chain_cache[cache_key] = (now, payload)
    return payload


# ─── /sleeves/options/reason ────────────────────────────────────────────────
# On-demand DeepSeek V3 thesis for a single screener candidate.  Cached per
# (ticker, date, signal-fingerprint) so the same card can be clicked multiple
# times without repeating the LLM call.

_reason_cache: dict[str, tuple[float, dict[str, str]]] = {}
_REASON_CACHE_TTL_SECONDS = 86_400  # 1 day — stale after market close anyway


class _ReasonRequest(BaseModel):
    ticker: str
    conviction_pct: float
    signals: list[dict[str, Any]]
    recommendation: dict[str, Any]
    strategy: str | None = None


@router.post("/options/reason")
async def get_options_reason(req: _ReasonRequest) -> dict[str, str]:
    """Generate a DeepSeek V3 thesis for a screener candidate with multi-expiry chain analysis.

    Fetches the option chain at 14d, 35d, and 63d target expirations, extracts ATM call
    premium, IV, and bid-ask spread for each, then asks the LLM to recommend the best
    expiry based on the thesis timeline and pricing quality.

    Response: {"thesis": "...", "recommended_expiry": "35d expiry — reason"}

    Costs ~$0.002 per call (one chain fetch + LLM). Cached per
    (ticker, strategy, today) for the full trading day so repeat clicks are free.
    """
    import hashlib

    today_str = datetime.date.today().isoformat()
    strategy_key = req.strategy or "unknown"
    cache_key = f"{req.ticker}:{strategy_key}:{today_str}"

    now = time.monotonic()
    cached = _reason_cache.get(cache_key)
    if cached and (now - cached[0]) < _REASON_CACHE_TTL_SECONDS:
        return cached[1]

    fired = [s for s in req.signals if s.get("fired")]
    signal_lines = "\n".join(
        f"  - {s['label']}: {s.get('value_text', '')} — {s.get('tooltip', '')}"
        for s in fired
    ) or "  (no signals fired)"
    direction = req.recommendation.get("direction", "call").upper()
    reasoning = req.recommendation.get("reasoning", "")

    # ── Multi-expiry chain analysis ──────────────────────────────────────────
    # Fetch ATM call data at 14d, 35d, and 63d target expirations from the chain.
    # Each target is a calendar-day offset from today; we find the nearest available
    # expiry in the chain for each bucket. Failures are skipped gracefully.

    def _fetch_expiry_pricing(
        symbol: str, target_dte: int
    ) -> dict[str, Any] | None:
        """Return ATM call premium, IV, and spread for the nearest available expiry
        to ``target_dte`` days from today. Returns None on any failure."""
        try:
            client = MassiveClient()
            today = datetime.date.today()
            target_date = today + datetime.timedelta(days=target_dte)
            # Fetch chain in a ±7d window around the target date.
            window_start = (target_date - datetime.timedelta(days=7)).isoformat()
            window_end = (target_date + datetime.timedelta(days=7)).isoformat()
            # Use 3% ATM window to find the nearest-the-money call.
            spot_closes = _fetch_closes(
                client, symbol,
                (today - datetime.timedelta(days=10)).isoformat(),
                today.isoformat(),
            )
            if not spot_closes:
                return None
            spot = spot_closes[-1]
            low = spot * 0.97
            high = spot * 1.03
            raw = client.get_options_chain(
                symbol,
                expiration_date_gte=window_start,
                expiration_date_lte=window_end,
                strike_price_gte=low,
                strike_price_lte=high,
                limit=50,
            )
            rows = raw.get("results") or []
            # Filter to calls only.
            call_rows = [
                r for r in rows
                if (r.get("details") or {}).get("contract_type") == "call"
            ]
            if not call_rows:
                return None
            # Pick the contract closest to spot.
            def _dist(r: dict) -> float:
                s = (r.get("details") or {}).get("strike_price") or 999999
                return abs(s - spot)
            best = min(call_rows, key=_dist)
            details = best.get("details") or {}
            day = best.get("day") or {}
            greeks = best.get("greeks") or {}
            bid = day.get("bid") or day.get("last") or 0.0
            ask = day.get("ask") or day.get("last") or 0.0
            last = day.get("last") or day.get("close") or 0.0
            premium = (bid + ask) / 2.0 if (bid and ask) else last
            spread = abs(ask - bid) if (bid and ask) else None
            iv = greeks.get("iv") or best.get("implied_volatility")
            expiry_date = details.get("expiration_date", "")
            actual_dte = (
                (datetime.date.fromisoformat(expiry_date) - today).days
                if expiry_date else target_dte
            )
            return {
                "target_dte": target_dte,
                "actual_dte": actual_dte,
                "expiry": expiry_date,
                "premium": round(premium, 2) if premium else None,
                "iv": round(iv * 100, 1) if (iv and isinstance(iv, (int, float))) else None,
                "spread": round(spread, 2) if spread else None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("Chain fetch failed for %s %dd: %s", symbol, target_dte, exc)
            return None

    symbol = req.ticker.strip().upper()
    target_dtes = [14, 35, 63]
    expiry_data: list[dict[str, Any]] = []
    for dte in target_dtes:
        result = await asyncio.to_thread(_fetch_expiry_pricing, symbol, dte)
        if result:
            expiry_data.append(result)

    # Build pricing summary string for the LLM prompt.
    pricing_lines: list[str] = []
    for ed in expiry_data:
        premium_str = f"~${ed['premium']:.2f}" if ed["premium"] is not None else "N/A"
        iv_str = f"{ed['iv']}%" if ed["iv"] is not None else "N/A"
        spread_str = f"${ed['spread']:.2f}" if ed["spread"] is not None else "N/A"
        label = f"{ed['target_dte']}d expiry ({ed.get('expiry', '')})"
        pricing_lines.append(f"{label}: ATM call {premium_str} (IV: {iv_str}, spread: {spread_str})")
    pricing_summary = "\n".join(pricing_lines) if pricing_lines else "(chain data unavailable)"

    # ── LLM call ────────────────────────────────────────────────────────────
    system_msg = (
        "You are a derivatives trader at a fundamental quant fund. "
        "You write concise trade rationales — no fluff, no disclaimers. "
        "Two sentences max for the thesis. Cite specific signal values. Mention the direction and why now."
    )
    user_msg = (
        f"Ticker: {req.ticker}\n"
        f"Conviction: {req.conviction_pct:.0f}%\n"
        f"Direction: {direction}\n"
        f"Fired signals:\n{signal_lines}\n"
        f"Strategy rationale: {reasoning}\n\n"
        f"Option pricing across expiries:\n{pricing_summary}\n\n"
        "Write a 2-sentence trade thesis. Be specific and direct.\n\n"
        "Then, based on the option pricing above and the thesis timeline, recommend the BEST expiry "
        "and structure. Consider: premium cost vs. time for thesis to play out, IV levels, and "
        "bid-ask spread quality. Output your recommendation as exactly:\n"
        "RECOMMENDED: [Xd expiry] — [one sentence why]"
    )

    def _call() -> tuple[str, str]:
        """Returns (thesis, recommended_expiry) strings."""
        import os as _os
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage, SystemMessage

        fallback_rec = f"{target_dtes[1]}d expiry — default recommendation; chain data unavailable."
        try:
            llm = ChatOpenAI(
                model="deepseek-chat",
                openai_api_key=_os.environ.get("DEEPSEEK_API_KEY", ""),
                openai_api_base="https://api.deepseek.com/v1",
                temperature=0.3,
                max_tokens=220,
            )
            response = llm.invoke([SystemMessage(content=system_msg), HumanMessage(content=user_msg)])
            raw_text = str(response.content).strip()

            # Parse the RECOMMENDED: line out of the response.
            rec_match = re.search(r"RECOMMENDED:\s*(.+?)(?:\n|$)", raw_text, re.IGNORECASE)
            if rec_match:
                recommended = rec_match.group(1).strip()
                # Thesis is everything before the RECOMMENDED line.
                thesis_text = raw_text[: rec_match.start()].strip()
                if not thesis_text:
                    thesis_text = raw_text
            else:
                thesis_text = raw_text
                recommended = fallback_rec

            return thesis_text, recommended
        except Exception as exc:  # noqa: BLE001
            logger.warning("Options reason LLM call failed: %s", exc)
            fallback_thesis = (
                f"Signal constellation ({req.conviction_pct:.0f}% conviction) favours a "
                f"{direction.lower()} position. Review the chain for optimal strike and expiry."
            )
            return fallback_thesis, fallback_rec

    thesis, recommended_expiry = await asyncio.to_thread(_call)
    result_payload: dict[str, str] = {
        "thesis": thesis,
        "recommended_expiry": recommended_expiry,
    }
    _reason_cache[cache_key] = (now, result_payload)
    return result_payload


# ─── /sleeves/quotes ─────────────────────────────────────────────────────────
# Lightweight batch price endpoint for the left-nav sidebar.  Returns the
# last close, 1-day change %, and a 20-bar sparkline for each ticker without
# pulling full news/fundamentals (which is what /ticker/{ticker} fetches).

_quote_cache: dict[str, tuple[float, dict]] = {}
_QUOTE_CACHE_TTL = 60.0  # seconds

# Company names are static, so cache them for the life of the process. Sentinel
# "" means "looked up, none found" so we don't retry a miss every refresh.
_name_cache: dict[str, str] = {}


async def _warm_company_names(symbols: list[str]) -> None:
    """Best-effort, time-boxed company-name warm — fills ``_name_cache``.

    Names are a sidebar nicety, NOT essential like the price. This is kept
    completely OFF the price hot-path: it runs separately, bounded to a few
    concurrent Finnhub calls, and capped by an overall timeout so it can never
    hang the /quotes request. Whatever resolves within the budget is cached
    (permanently — names are static); the rest fill in on a later refresh.

    Uses Finnhub (fast, independent of the price provider). We deliberately do
    NOT use Polygon's reference endpoint here: it's slow, occasionally 500s, and
    a fan-out across the whole sidebar would stall everything.
    """
    missing = [s for s in symbols if s not in _name_cache]
    if not missing:
        return
    from src.tools.finnhub import get_finnhub_client

    client = get_finnhub_client()
    if client is None:
        return  # no key → leave names blank, retry-free

    sem = asyncio.Semaphore(4)

    async def one(sym: str) -> None:
        async with sem:
            try:
                prof = await asyncio.to_thread(client.company_profile, sym)
                _name_cache[sym] = (prof.get("name") or "").strip()
            except Exception:  # noqa: BLE001 — best-effort; uncached names retry later
                pass

    try:
        await asyncio.wait_for(asyncio.gather(*[one(s) for s in missing]), timeout=6.0)
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        pass  # partial names are fine; the price response is unaffected


@router.get("/quotes")
async def get_quotes(tickers: str = "") -> dict[str, Any]:
    """Batch last-close prices for sidebar display.

    Query: ?tickers=AAPL,MSFT,NVDA (comma-separated, max 150)
    Returns per-ticker: { last, prev_close, pct_change, spark: [closes] }

    The cap is high enough to cover every sidebar ticker at once (all sleeves +
    watchlists + the 10 sector ETFs); fetches run concurrently and quotes are
    cached 60s, so a full sidebar refresh is cheap. The previous 50-cap silently
    dropped the trailing tickers (the sector ETFs), leaving them blank.
    """
    symbols = [t.strip().upper() for t in tickers.split(",") if t.strip()][:150]
    if not symbols:
        return {"quotes": {}}

    today = datetime.date.today()
    start = (today - datetime.timedelta(days=35)).isoformat()  # enough for 20 trading days

    # Warm company names first (best-effort, time-boxed, off the price path).
    await _warm_company_names(symbols)

    # Bound concurrency so a full sidebar fetch can't exhaust the thread pool
    # (and starve other endpoints). Cached tickers skip the semaphore entirely.
    sem = asyncio.Semaphore(12)

    async def fetch_one(symbol: str) -> tuple[str, dict]:
        now = time.monotonic()
        cached = _quote_cache.get(symbol)
        if cached and (now - cached[0]) < _QUOTE_CACHE_TTL:
            # Patch in a name that may have warmed since this was cached.
            if not cached[1].get("name") and _name_cache.get(symbol):
                cached[1]["name"] = _name_cache[symbol]
            return symbol, cached[1]

        # Price only — reliable. Name is read from cache (never fetched here).
        # Short client timeout so a degraded provider fails fast instead of
        # holding a thread for the default 30s per attempt.
        def _compute() -> dict:
            client = MassiveClient(timeout=6)
            closes = _fetch_closes(client, symbol, start, today.isoformat())
            name = _name_cache.get(symbol, "")
            if not closes:
                return {"last": None, "prev_close": None, "pct_change": None, "spark": [], "name": name}
            spark = closes[-20:]
            last = spark[-1]
            prev = spark[-2] if len(spark) >= 2 else None
            pct = round((last - prev) / prev * 100, 2) if prev else None
            return {"last": round(last, 2), "prev_close": round(prev, 2) if prev else None, "pct_change": pct, "spark": [round(c, 2) for c in spark], "name": name}

        async with sem:
            try:
                result = await asyncio.to_thread(_compute)
            except Exception:
                result = {"last": None, "prev_close": None, "pct_change": None, "spark": [], "name": _name_cache.get(symbol, "")}

        _quote_cache[symbol] = (time.monotonic(), result)
        return symbol, result

    # Time-box the whole batch: return within the budget with whatever resolved
    # so the sidebar loads fast even when the data provider is slow/down. Any
    # ticker that didn't finish shows a null price ("—") and fills in on the
    # next 60s refresh once the provider recovers.
    tasks = [asyncio.create_task(fetch_one(s)) for s in symbols]
    done, pending = await asyncio.wait(tasks, timeout=12.0)
    for t in pending:
        t.cancel()

    quotes: dict[str, dict] = {}
    for t in done:
        try:
            sym, res = t.result()
            quotes[sym] = res
        except Exception:  # noqa: BLE001
            pass
    # Fill any ticker that timed out / errored with a null placeholder.
    for s in symbols:
        quotes.setdefault(
            s,
            {"last": None, "prev_close": None, "pct_change": None, "spark": [], "name": _name_cache.get(s, "")},
        )
    return {"quotes": quotes}


# ─── /sleeves/chat/stream ────────────────────────────────────────────────────
# Context-aware AI chat over DeepSeek V3 streamed as SSE.
# The frontend passes a context snapshot (current ticker, section, recent
# screener/scan results) alongside the full message thread.  The backend
# injects these as a system-message prefix and streams tokens back.

_CHAT_SYSTEM = """\
You are a financial research assistant for an AI-powered alpha-generation engine.
You have direct access to the user's portfolio data, live scan results, options
screener output, and pattern analysis.  Answer concisely and cite specific data
points (tickers, conviction %, signals) when they are available.

Rules:
- Be direct and specific. Avoid vague hedge words.
- If data shows conflicting signals, acknowledge the tension.
- You are not a licensed advisor — do not give personalised investment advice.
  Describe what the data shows, not what the user should do.
- Keep answers under 4 short paragraphs unless the user asks for more.
"""


class _ChatMessage(BaseModel):
    role: str  # 'user' | 'assistant'
    content: str


class _ChatContext(BaseModel):
    section: str = "market"
    selectedTicker: str | None = None
    screenerSnapshot: dict[str, Any] | None = None
    patternSnapshot: dict[str, Any] | None = None
    scanSnapshot: dict[str, Any] | None = None


class _ChatRequest(BaseModel):
    messages: list[_ChatMessage]
    context: _ChatContext = _ChatContext()


def _build_chat_context_text(ctx: _ChatContext) -> str:
    """Format the frontend context snapshot into plain text for the LLM."""
    parts: list[str] = []
    parts.append(f"Current page: {ctx.section}")
    if ctx.selectedTicker:
        parts.append(f"Active ticker: {ctx.selectedTicker}")

    if ctx.screenerSnapshot:
        cands = ctx.screenerSnapshot.get("candidates") or []
        if cands:
            top = cands[:8]
            lines = [f"  - {c.get('ticker')} {c.get('conviction_pct', '?'):.0f}% conviction, {c.get('recommendation', {}).get('direction', '?')}"
                     for c in top if isinstance(c, dict)]
            strategy = ctx.screenerSnapshot.get("strategy", "")
            parts.append(f"Options screener ({strategy}, {len(cands)} candidates — top 8):\n" + "\n".join(lines))

    if ctx.patternSnapshot:
        results = ctx.patternSnapshot.get("results") or []
        if results:
            top = results[:8]
            lines = [f"  - {r.get('ticker')} {r.get('pattern')} {r.get('confidence', 0)*100:.0f}%" for r in top if isinstance(r, dict)]
            parts.append(f"Pattern scan (top 8):\n" + "\n".join(lines))

    if ctx.scanSnapshot:
        rows = ctx.scanSnapshot.get("rows") or []
        bull = sum(1 for r in rows if isinstance(r, dict) and r.get("consensus") == "bullish")
        bear = sum(1 for r in rows if isinstance(r, dict) and r.get("consensus") == "bearish")
        if rows:
            parts.append(f"Morning scan: {len(rows)} tickers — {bull} bullish, {bear} bearish")
            if ctx.selectedTicker:
                row = next((r for r in rows if isinstance(r, dict) and r.get("ticker") == ctx.selectedTicker), None)
                if row:
                    parts.append(
                        f"{ctx.selectedTicker} scan: consensus={row.get('consensus')} "
                        f"score={row.get('weighted_score')} conf={row.get('avg_confidence')}"
                    )

    return "\n".join(parts) if parts else ""


# Words that signal the user is asking about a catalyst / recent move, which is
# when fetching live news is worth the latency. Mechanical/valuation questions
# don't trip these, so we skip the news fetch for them.
_NEWS_TRIGGER_WORDS = {
    "news", "happening", "happened", "why", "moved", "moving", "move", "drop",
    "dropped", "fell", "fall", "falling", "rally", "rallied", "surge", "surged",
    "spike", "spiked", "jumped", "jump", "tanked", "crash", "crashed", "catalyst",
    "headline", "headlines", "today", "recent", "recently", "latest", "announce",
    "announced", "announcement", "report", "reported", "earnings", "guidance",
    "upgrade", "downgrade", "lawsuit", "sec", "fda", "deal", "acquisition",
}


def _question_wants_news(text: str) -> bool:
    """True when the question reads like a catalyst/news ask."""
    words = {w.strip(".,!?'\"():;").lower() for w in text.split()}
    return bool(words & _NEWS_TRIGGER_WORDS)


def _chat_saved_analysis_block(ticker: str) -> str:
    """Per-agent analysis for ``ticker`` from the most recent saved scan."""
    scan = _latest_scan_summary()
    if not scan:
        return ""
    row = next(
        (r for r in (scan.get("rows") or []) if isinstance(r, dict) and r.get("ticker") == ticker),
        None,
    )
    if not row:
        return ""
    lines = [f"Saved scan analysis for {ticker} (scan {scan.get('date')}):"]
    lines.append(
        f"  consensus={row.get('consensus')} weighted_score={row.get('weighted_score')} "
        f"avg_confidence={row.get('avg_confidence')}"
    )
    if row.get("variant_perception"):
        lines.append(f"  variant perception: {row.get('variant_perception')}")
    for a in (row.get("per_agent") or [])[:6]:
        if isinstance(a, dict):
            lines.append(f"  - {a.get('agent')}: {a.get('signal')} ({a.get('confidence')}%)")
    return "\n".join(lines)


def _chat_fundamentals_block(ticker: str) -> str:
    """Compact Finnhub fundamentals (growth, beat/miss, consensus, insider)."""
    from src.tools.finnhub import get_finnhub_client
    from src.tools.finnhub.converters import fundamentals_summary

    client = get_finnhub_client()
    if client is None:
        return ""
    try:
        s = fundamentals_summary(client, ticker)
    except Exception:  # noqa: BLE001 — context is best-effort
        return ""
    lines: list[str] = []
    m = s.get("metrics") or {}
    bits = []
    for key, lbl in [
        ("revenue_growth_ttm", "rev growth TTM"),
        ("eps_growth_ttm", "EPS growth TTM"),
        ("net_margin_ttm", "net margin"),
        ("roe_ttm", "ROE"),
        ("pe_ttm", "P/E"),
    ]:
        if key in m:
            bits.append(f"{lbl} {m[key]:.1f}")
    if bits:
        lines.append("  " + ", ".join(bits))
    earn = s.get("earnings") or []
    if earn:
        beats = sum(1 for e in earn if e.get("beat"))
        first = earn[0]
        lines.append(
            f"  earnings beat/miss: {beats}/{len(earn)} recent quarters beat; "
            f"last {first.get('period')} actual {first.get('actual')} vs est {first.get('estimate')}"
        )
    rec = s.get("recommendation")
    if rec:
        lines.append(
            f"  analyst consensus: {rec.get('strong_buy', 0) + rec.get('buy', 0)} buy / "
            f"{rec.get('hold', 0)} hold / {rec.get('sell', 0) + rec.get('strong_sell', 0)} sell"
        )
    flow = s.get("insider_flow")
    if flow and flow.get("n"):
        lines.append(
            f"  insider flow: net {flow.get('net_shares')} sh "
            f"({flow.get('buys')} buys / {flow.get('sells')} sells)"
        )
    if not lines:
        return ""
    return f"Fundamentals for {ticker} (Finnhub):\n" + "\n".join(lines)


def _chat_news_block(ticker: str) -> str:
    """Recent headlines for ``ticker`` — Finnhub primary, Polygon fallback."""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=7)
    items: list[tuple[str, str]] = []

    from src.tools.finnhub import get_finnhub_client

    client = get_finnhub_client()
    if client is not None:
        try:
            raw = client.company_news(
                ticker, start_date=start.isoformat(), end_date=end.isoformat()
            )
            items = [(r.get("headline"), r.get("source", "")) for r in raw[:6] if r.get("headline")]
        except Exception:  # noqa: BLE001
            items = []
    if not items:
        try:
            from src.tools.api import get_company_news

            raw_p = get_company_news(
                ticker, end_date=end.isoformat(), start_date=start.isoformat(), limit=6
            )
            items = [(n.title, n.source) for n in raw_p[:6]]
        except Exception:  # noqa: BLE001
            items = []
    if not items:
        return ""
    lines = [f"Recent news for {ticker} (last 7 days):"]
    lines += [f"  - {title} ({src})" for title, src in items]
    return "\n".join(lines)


def _build_ticker_chat_blocks(ticker: str, want_news: bool) -> list[str]:
    """Assemble the per-ticker context blocks for the chat system prompt.

    Saved analysis + fundamentals are always included (cheap, grounding); live
    news is fetched only when the question looks catalyst-related.
    """
    blocks = [
        _chat_saved_analysis_block(ticker),
        _chat_fundamentals_block(ticker),
    ]
    if want_news:
        blocks.append(_chat_news_block(ticker))
    return [b for b in blocks if b]


@router.post("/chat/stream")
async def chat_stream(req: _ChatRequest) -> StreamingResponse:
    """Stream a DeepSeek V3 chat response with frontend context.

    SSE format:
      data: {"token": "..."}\n\n   — partial content chunk
      data: [DONE]\n\n             — stream complete
      data: {"error": "..."}\n\n   — error occurred

    Hard-caps: max 20 messages history, 50KB total context.
    """
    import os as _os

    # Trim to last 20 turns to cap token cost.
    messages = req.messages[-20:]
    ctx_text = _build_chat_context_text(req.context)

    system_content = _CHAT_SYSTEM
    if ctx_text:
        system_content += f"\n\n## Current context\n{ctx_text}"

    # Ground answers about a specific stock in its saved agent analysis and
    # fundamentals; pull live news only when the question is catalyst-related.
    if req.context.selectedTicker:
        ticker = req.context.selectedTicker.strip().upper()
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        want_news = _question_wants_news(last_user)
        blocks = await asyncio.to_thread(_build_ticker_chat_blocks, ticker, want_news)
        if blocks:
            system_content += "\n\n## Stock detail\n" + "\n\n".join(blocks)

    async def event_gen():
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

            llm = ChatOpenAI(
                model="deepseek-chat",
                openai_api_key=_os.environ.get("DEEPSEEK_API_KEY", ""),
                openai_api_base="https://api.deepseek.com/v1",
                temperature=0.4,
                max_tokens=600,
                streaming=True,
            )

            lc_messages = [SystemMessage(content=system_content)]
            for m in messages:
                if m.role == "user":
                    lc_messages.append(HumanMessage(content=m.content))
                else:
                    lc_messages.append(AIMessage(content=m.content))

            async for chunk in llm.astream(lc_messages):
                if chunk.content:
                    yield f"data: {json.dumps({'token': chunk.content})}\n\n"

            yield "data: [DONE]\n\n"

        except Exception as exc:
            logger.warning("Chat stream error: %s", exc)
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── /sleeves/backtest/options-strategy (BSM-proxy SSE) ─────────────────────

# Why a BSM proxy and not real historical chains: constructing valid Polygon
# option tickers per backtest day is brittle (strike rounding, weekly-expiry
# existence, listing gaps), and the resulting backtest would be lumpy with
# missing data. Black-Scholes against the underlying's realized vol gives a
# deterministic premium that's good enough to rank straddle / calls / puts
# directional bets across conviction tiers. The endpoint surfaces this
# assumption in the SSE 'start' event so the UI can label it.

from src.backtesting.options_historical import (  # noqa: E402
    NoAggregateData,
    NoSuchContract,
    bsm_premium_series,
    bsm_straddle_series,
    get_premium_series,
    pick_close,
)
from src.backtesting.options_proxy import (  # noqa: E402  (sits with the related code)
    RISK_FREE_RATE,
    bsm_price,
    realized_vol,
    straddle_price,
)
from src.backtesting.sleeve_attribution import (  # noqa: E402
    Trade,
    compute_agent_attribution,
    compute_sleeve_metrics,
    extract_trades_from_day_results,
    warn_underperforming_agents,
)


class OptionsBacktestRequest(BaseModel):
    """Body for POST /sleeves/backtest/options-strategy.

    Generalized to support any of the 11 screener strategies. Backward-
    compatible defaults: ``strategy='weakness'``, ``tickers=None`` (whole
    sleeve), ``direction='straddle'`` (price both legs uniformly).
    """

    start_date: str = Field(description="Backtest start (YYYY-MM-DD).")
    end_date: str = Field(description="Backtest end (YYYY-MM-DD), inclusive.")
    sleeve: str = Field(default="mega_tech", description="Which sleeve's tickers to screen.")
    tickers: list[str] | None = Field(
        default=None,
        description=(
            "Restrict to a subset of the sleeve's tickers. None = use the whole "
            "sleeve. Tickers must already exist in the chosen sleeve."
        ),
    )
    strategy: str = Field(
        default="weakness",
        description=(
            "Screener strategy to backtest. Any registered strategy except "
            "unusual_options_activity (which needs historical option chain data we "
            "don't have on this plan)."
        ),
    )
    conviction_min: int = Field(
        default=2,
        ge=0,
        le=3,
        description=(
            "DEPRECATED — legacy integer conviction count (0-3). Used only as a "
            "fallback when min_conviction_pct is not supplied. Prefer "
            "min_conviction_pct."
        ),
    )
    min_conviction_pct: float | None = Field(
        default=40.0,
        ge=0.0,
        le=100.0,
        description=(
            "Only open trades when the candidate's conviction percentage "
            "(0-100, magnitude-weighted) is >= this. The primary conviction "
            "gate. None falls back to the legacy conviction_min count."
        ),
    )
    direction: str = Field(
        default="straddle",
        description=(
            "'auto' | 'straddle' | 'calls' | 'puts'. 'auto' uses each candidate's "
            "strategy-recommended direction (call vs put). Others force one leg."
        ),
    )
    hold_days: int = Field(
        default=30,
        ge=1,
        le=60,
        description=(
            "Max trading days to hold — the backstop exit when no other trigger "
            "fires. Realistic options trades rarely run to a fixed short hold; "
            "this is the outer bound, with profit-target / stop / DTE exits "
            "closing most trades sooner."
        ),
    )
    profit_target_pct: float | None = Field(
        default=0.50,
        ge=0.0,
        description=(
            "Take-profit on the option premium, as a positive fraction "
            "(0.50 = close when premium is up 50%). None = no target. Checked "
            "on each day's close/mark."
        ),
    )
    dte_exit: int | None = Field(
        default=21,
        ge=0,
        description=(
            "Close when the contract reaches this many days-to-expiry, to step "
            "out before the gamma/theta cliff. None = ignore DTE. In BSM proxy "
            "mode the expiry is synthesized from the contract's target DTE, so "
            "this is an approximation there."
        ),
    )
    pricing: str = Field(
        default="real",
        description=(
            "'real' = fetch historical option chain + per-contract OHLC from "
            "Polygon (requires Options plan). 'bsm' = Black-Scholes proxy using "
            "trailing realized vol — deterministic, no API calls. 'real' falls "
            "back to BSM per-trade when a contract or bar is missing, and flags "
            "those trades as synthetic in the output."
        ),
    )
    stop_loss_pct: float | None = Field(
        default=0.50,
        ge=0.0,
        le=0.99,
        description=(
            "Per-contract drawdown stop, expressed as a positive fraction "
            "(e.g. 0.50 = exit at -50% of entry premium). None = no stop, "
            "ride to another trigger or the hold_days backstop. Checked on "
            "each trading day's close (real fills) or walk-forward BSM premium "
            "(proxy mode). Straddles stop on combined premium, not per leg."
        ),
    )
    slippage_pct: float | None = Field(
        default=0.05,
        ge=0.0,
        le=0.5,
        description=(
            "Round-trip bid/ask + execution cost, as a fraction of premium. "
            "Modeled as a half-spread haircut per side: you buy at "
            "entry x (1 + slippage/2) and sell at exit x (1 - slippage/2). "
            "0.05 = a 5% spread, a realistic mid-cap default. None or 0 = "
            "frictionless fills (optimistic — inflates win rate, especially "
            "in BSM mode where the premium path is already smooth)."
        ),
    )


# Strategies that the BSM-proxy backtest can simulate. UOA needs per-day
# historical option chain volume — not available without a dedicated data
# pull that's outside the current Massive plan's reasonable cost envelope.
_BACKTESTABLE_STRATEGIES = _VALID_STRATEGIES - {"unusual_options_activity"}


class TradeRecord(BaseModel):
    """One simulated trade emitted via SSE."""

    ticker: str
    strategy: str
    direction: str
    open_date: str
    close_date: str
    conviction: int
    conviction_pct: float = 0.0
    strike: float
    sigma: float
    entry_spot: float
    exit_spot: float
    # entry_premium / exit_premium are the *filled* prices — i.e. after the
    # slippage haircut when slippage_pct is set, so pnl reflects real costs.
    entry_premium: float
    exit_premium: float
    pnl: float
    return_pct: float
    # Real-fill metadata. ``synthetic=True`` when this trade fell back to
    # BSM (either pricing='bsm' or a real-fill miss). ``contract_ticker``
    # and ``contract_expiry`` are populated only for real fills.
    synthetic: bool = True
    contract_ticker: str | None = None
    contract_expiry: str | None = None
    # Exit bookkeeping. ``stopped_out=True`` means the trade exited on the
    # stop-loss specifically. ``exit_reason`` is one of:
    #   'target' — profit target hit
    #   'stop'   — stop-loss hit
    #   'dte'    — closed at the days-to-expiry threshold
    #   'expiry' — held to expiration (settled at intrinsic)
    #   'time'   — hold_days backstop reached, nothing else fired
    stopped_out: bool = False
    exit_reason: str = "time"


def _resolve_option_exit(
    *,
    series: list[tuple[datetime.date, float]],
    entry_premium: float,
    expiry: datetime.date | None,
    profit_target_pct: float | None,
    stop_loss_pct: float | None,
    dte_exit: int | None,
) -> tuple[float, datetime.date, str]:
    """Walk a per-day premium series and return the first exit that triggers.

    ``series`` is ordered ``[(date, premium), ...]`` with index 0 = entry day.
    Each subsequent day is checked in priority order; the first hit wins:

      1. stop-loss   (ret <= -stop_loss_pct)
      2. profit target (ret >= profit_target_pct)
      3. DTE exit     (days-to-expiry <= dte_exit)
      4. expiry       (date at/after the contract expiration → settle there)

    Stop is checked before target on purpose: with only one mark per day we
    cannot know intraday ordering, so we assume the worse of the two. This
    biases reported returns slightly downward — honest for risk sizing.

    Falls back to the last point in the series with reason ``'time'`` (the
    hold_days backstop) when nothing triggers.
    """
    if entry_premium <= 0 or len(series) < 2:
        last_d, last_p = series[-1]
        return last_p, last_d, "time"
    for idx in range(1, len(series)):
        dd, prem = series[idx]
        ret = (prem - entry_premium) / entry_premium
        if stop_loss_pct and ret <= -stop_loss_pct:
            return prem, dd, "stop"
        if profit_target_pct and ret >= profit_target_pct:
            return prem, dd, "target"
        if dte_exit is not None and expiry is not None and (expiry - dd).days <= dte_exit:
            return prem, dd, "dte"
        if expiry is not None and dd >= expiry:
            return prem, dd, "expiry"
    last_d, last_p = series[-1]
    return last_p, last_d, "time"


@router.post("/backtest/options-strategy")
async def backtest_options_strategy(req: OptionsBacktestRequest, request: Request):
    """Backtest the lagging-mega-tech options strategy with Black-Scholes pricing.

    For each trading day in [start, end]:
      1. Apply the Phase-E conviction screener using prices up to that day.
      2. For each candidate with ``conviction >= conviction_min``, open a
         simulated ATM position in ``direction`` and hold ``hold_days``.
      3. Price entry + exit via BSM against trailing 30-day realized vol.
      4. P&L = exit premium - entry premium (per share).

    Stream events:
      start            — assumptions banner.
      progress         — per-day "Processing YYYY-MM-DD" message.
      trade            — emitted each time a trade closes.
      complete         — summary stats (total trades, win rate, mean / total P&L,
                         breakdown per direction and conviction tier).
      error            — fatal failure.
    """
    sleeves_cfg = _live_sleeves()
    if req.sleeve not in sleeves_cfg:
        raise HTTPException(status_code=400, detail=f"Unknown sleeve '{req.sleeve}'.")
    if req.strategy not in _BACKTESTABLE_STRATEGIES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Strategy '{req.strategy}' not backtestable. Available: "
                f"{sorted(_BACKTESTABLE_STRATEGIES)}."
            ),
        )
    if req.direction not in {"auto", "straddle", "calls", "puts"}:
        raise HTTPException(
            status_code=400,
            detail="direction must be one of: auto, straddle, calls, puts.",
        )

    try:
        start_d = datetime.date.fromisoformat(req.start_date)
        end_d = datetime.date.fromisoformat(req.end_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Bad date: {exc}")
    if end_d < start_d:
        raise HTTPException(status_code=400, detail="end_date < start_date")

    # Resolve ticker scope: whole sleeve, or user-supplied subset.
    full_ticker_set = list(sleeves_cfg[req.sleeve]["tickers"])
    if req.tickers:
        wanted = {t.strip().upper() for t in req.tickers if t.strip()}
        tickers = [t for t in full_ticker_set if t.upper() in wanted]
        if not tickers:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"None of the requested tickers {sorted(wanted)} are in sleeve "
                    f"'{req.sleeve}'. Sleeve members: {full_ticker_set}."
                ),
            )
    else:
        tickers = full_ticker_set

    scorer = _STRATEGY_REGISTRY[req.strategy]["scorer"]

    async def _detect_disconnect() -> bool:
        try:
            while True:
                msg = await request.receive()
                if msg["type"] == "http.disconnect":
                    return True
        except Exception:
            return True

    async def event_generator():
        disconnect_task = asyncio.create_task(_detect_disconnect())

        # Pre-fetch full bars (OHLCV) — needed for breakout/breakdown/volume_spike
        # scorers that read highs/lows/volume in addition to closes. Window
        # padded for the 52-week / 200-day lookbacks the technical strategies use.
        client = MassiveClient()
        pad_start = (start_d - datetime.timedelta(days=400)).isoformat()
        fetch_end = end_d.isoformat()

        # bars_by_ticker maps ticker → ordered list of Price bars.
        bars_by_ticker: dict[str, list] = {}

        def _fetch_one(t: str) -> list:
            try:
                aggs = client.get_daily_aggregates(t, pad_start, fetch_end)
            except MassiveError as exc:
                logger.warning("Backtest prefetch failed for %s: %s", t, exc)
                return []
            return convert_prices(aggs)

        try:
            if req.pricing == "real":
                pricing_label = "real Polygon historical fills"
                assumption_text = (
                    "Premiums fetched from Polygon's historical option-contract "
                    "aggregates (daily close per contract). Falls back to BSM "
                    "per-trade if the contract or bar is missing (flagged "
                    "synthetic in the trades table). Daily-close fills don't "
                    "model bid/ask spread — Polygon Advanced is needed for NBBO."
                )
            else:
                pricing_label = "Black-Scholes proxy"
                assumption_text = (
                    "Premiums priced via BSM against trailing 30-day realized vol "
                    "of the underlying. No real historical chain quotes used."
                )
            if req.stop_loss_pct:
                assumption_text += (
                    f" Stop-loss: exit early when premium ≤ entry × "
                    f"(1 − {req.stop_loss_pct:.0%})."
                )
            yield StartEvent(data={
                "message": (
                    f"Options backtest — strategy={req.strategy}, "
                    f"direction={req.direction}, pricing={pricing_label}"
                    + (f", stop=-{req.stop_loss_pct:.0%}" if req.stop_loss_pct else "")
                ),
                "assumption": assumption_text,
                "sleeve": req.sleeve,
                "tickers": tickers,
                "strategy": req.strategy,
                "start_date": req.start_date,
                "end_date": req.end_date,
                "direction": req.direction,
                "conviction_min": req.conviction_min,
                "hold_days": req.hold_days,
                "pricing": req.pricing,
                "stop_loss_pct": req.stop_loss_pct,
            }).to_sse()

            for t in ["QQQ", *tickers]:
                if disconnect_task.done():
                    return
                bars = await asyncio.to_thread(_fetch_one, t)
                if not bars:
                    yield ProgressUpdateEvent(
                        agent="backtest", ticker=t, status="no price history; skipping"
                    ).to_sse()
                bars_by_ticker[t] = bars

            qqq_bars_all = bars_by_ticker.get("QQQ", [])
            qqq_dates = [datetime.date.fromisoformat(p.time) for p in qqq_bars_all]
            if not qqq_dates:
                yield ErrorEvent(message="QQQ benchmark data unavailable; aborting.").to_sse()
                return

            # Cache parsed date per bar so we don't re-parse on every slice.
            bar_dates: dict[str, list[datetime.date]] = {
                t: [datetime.date.fromisoformat(p.time) for p in bars]
                for t, bars in bars_by_ticker.items()
            }

            # Iterate trading days in window. QQQ's date set is the canonical
            # trading-day calendar.
            window_days = [d for d in qqq_dates if start_d <= d <= end_d]

            trades: list[TradeRecord] = []
            for i, d in enumerate(window_days):
                if disconnect_task.done():
                    return
                if i % 5 == 0:
                    yield ProgressUpdateEvent(
                        agent="backtest",
                        ticker=None,
                        status=f"Processing {d.isoformat()} ({i + 1}/{len(window_days)})",
                    ).to_sse()

                # QQQ bars sliced up to d (inclusive).
                qqq_slice = [
                    p for p, dd in zip(qqq_bars_all, qqq_dates) if dd <= d
                ]
                if len(qqq_slice) < 21:
                    continue

                # Score each ticker as of d.
                for ticker in tickers:
                    bars = bars_by_ticker.get(ticker, [])
                    dates_t = bar_dates.get(ticker, [])
                    bars_to_d = [b for b, dd in zip(bars, dates_t) if dd <= d]
                    if len(bars_to_d) < 31:
                        continue

                    scored = scorer(bars_to_d, qqq_slice)
                    # Primary gate is the conviction percentage (magnitude-
                    # weighted). Fall back to the legacy integer count only when
                    # min_conviction_pct is explicitly cleared.
                    scored_conv_pct = float(scored.get("conviction_pct", 0.0))
                    if req.min_conviction_pct is not None:
                        if scored_conv_pct < req.min_conviction_pct:
                            continue
                    elif scored["conviction"] < req.conviction_min:
                        continue

                    closes_to_d = [b.close for b in bars_to_d]
                    sigma = realized_vol(closes_to_d, window=30)
                    if sigma is None or sigma <= 0:
                        continue
                    spot = closes_to_d[-1]

                    # Exit date: hold_days trading days forward, clipped to
                    # window. If not enough forward bars, skip — can't close.
                    forward_dates = [dd for dd in dates_t if dd > d][: req.hold_days]
                    if len(forward_dates) < req.hold_days:
                        continue
                    exit_date = forward_dates[-1]
                    exit_spot = next(b.close for b, dd in zip(bars, dates_t) if dd == exit_date)

                    # Direction resolution: explicit override OR auto from
                    # the strategy's recommendation.
                    if req.direction == "auto":
                        rec = scored.get("recommendation") or {}
                        rec_dir = rec.get("direction", "call")
                        trade_dir = "calls" if rec_dir == "call" else "puts"
                    else:
                        trade_dir = req.direction

                    strike = round(spot)  # $1 strikes — BSM proxy default
                    target_expiry_days = min(60, max(14, int(req.hold_days * 2.5)))

                    # Forward spot trajectory between entry and exit (inclusive
                    # of both ends). Used by BSM walk-forward when stop-loss is
                    # on, and by intrinsic exit when it's off.
                    forward_spots: list[float] = [spot]
                    for fd in forward_dates:
                        forward_spots.append(
                            next(b.close for b, dd in zip(bars, dates_t) if dd == fd)
                        )
                    # forward_dates is hold_days entries (day +1 .. day +hold_days).
                    # Pair them with their spots for stop-loss scanning. Day 0 is
                    # entry day; we never stop on entry day.
                    spot_dates: list[datetime.date] = [d, *forward_dates]

                    entry_premium: float
                    exit_premium: float
                    actual_exit_date: datetime.date = exit_date
                    synthetic = True
                    stopped_out = False
                    exit_reason = "time"
                    contract_ticker: str | None = None
                    contract_expiry: str | None = None

                    use_real = req.pricing == "real"
                    if use_real:
                        try:
                            if trade_dir == "calls":
                                meta, bars_series = await asyncio.to_thread(
                                    get_premium_series,
                                    client,
                                    underlying=ticker, entry_date=d, exit_date=exit_date,
                                    target_strike=float(spot),
                                    target_expiry_days=target_expiry_days,
                                    option_type="call",
                                )
                                strike = meta["strike"]
                                contract_ticker = meta["ticker"]
                                contract_expiry = meta["expiration_date"]
                                combined = bars_series
                            elif trade_dir == "puts":
                                meta, bars_series = await asyncio.to_thread(
                                    get_premium_series,
                                    client,
                                    underlying=ticker, entry_date=d, exit_date=exit_date,
                                    target_strike=float(spot),
                                    target_expiry_days=target_expiry_days,
                                    option_type="put",
                                )
                                strike = meta["strike"]
                                contract_ticker = meta["ticker"]
                                contract_expiry = meta["expiration_date"]
                                combined = bars_series
                            else:  # straddle: both legs at the same strike/expiry
                                c_meta, c_bars = await asyncio.to_thread(
                                    get_premium_series,
                                    client,
                                    underlying=ticker, entry_date=d, exit_date=exit_date,
                                    target_strike=float(spot),
                                    target_expiry_days=target_expiry_days,
                                    option_type="call",
                                )
                                p_meta, p_bars = await asyncio.to_thread(
                                    get_premium_series,
                                    client,
                                    underlying=ticker, entry_date=d, exit_date=exit_date,
                                    target_strike=c_meta["strike"],
                                    target_expiry_days=target_expiry_days,
                                    option_type="put",
                                )
                                # Combine only on days where both legs trade.
                                combined = {
                                    dd: c_bars[dd] + p_bars[dd]
                                    for dd in c_bars
                                    if dd in p_bars
                                }
                                strike = c_meta["strike"]
                                contract_ticker = f"{c_meta['ticker']} + {p_meta['ticker']}"
                                contract_expiry = c_meta["expiration_date"]

                            entry_premium_opt = pick_close(combined, d, max_back=2)
                            if entry_premium_opt is None:
                                raise NoAggregateData(
                                    f"Missing entry bar for {contract_ticker} on {d}"
                                )
                            entry_premium = entry_premium_opt

                            # Build the ordered per-day premium series over the
                            # holding window, then resolve the exit against all
                            # triggers (target / stop / DTE / expiry / backstop).
                            real_series: list[tuple[datetime.date, float]] = []
                            for dd in spot_dates:
                                p = pick_close(combined, dd, max_back=2)
                                if p is not None:
                                    real_series.append((dd, p))
                            if not real_series or real_series[0][0] != d:
                                real_series.insert(0, (d, entry_premium))
                            expiry_date = (
                                datetime.date.fromisoformat(contract_expiry)
                                if contract_expiry
                                else None
                            )
                            exit_premium, actual_exit_date, exit_reason = _resolve_option_exit(
                                series=real_series,
                                entry_premium=entry_premium,
                                expiry=expiry_date,
                                profit_target_pct=req.profit_target_pct,
                                stop_loss_pct=req.stop_loss_pct,
                                dte_exit=req.dte_exit,
                            )
                            stopped_out = exit_reason == "stop"
                            synthetic = False
                        except (NoSuchContract, NoAggregateData, MassiveError) as exc:
                            logger.info(
                                "Real-fill miss for %s %s on %s — falling back to BSM (%s)",
                                ticker, trade_dir, d.isoformat(), exc,
                            )
                            # Reset metadata; the BSM path below populates fresh.
                            contract_ticker = None
                            contract_expiry = None
                            use_real = False  # drop into BSM block

                    if not use_real:
                        # BSM walk-forward: produce a per-day premium series so
                        # stop-loss can scan it. Day 0 = entry; day i = i trading
                        # days held. TTM decays so the final entry is intrinsic.
                        strike = round(spot)
                        if trade_dir == "straddle":
                            premium_series = bsm_straddle_series(
                                spot_series=forward_spots, strike=strike,
                                hold_days=req.hold_days, sigma=sigma,
                                risk_free=RISK_FREE_RATE,
                            )
                        else:
                            premium_series = bsm_premium_series(
                                spot_series=forward_spots, strike=strike,
                                hold_days=req.hold_days, sigma=sigma,
                                option_type="call" if trade_dir == "calls" else "put",
                                risk_free=RISK_FREE_RATE,
                            )
                        entry_premium = premium_series[0]

                        # No real contract in proxy mode — synthesize the expiry
                        # from the target DTE so the DTE/expiry exits resolve.
                        # (This makes the DTE exit an approximation in BSM mode.)
                        bsm_expiry = d + datetime.timedelta(days=target_expiry_days)
                        # Surface the synthesized contract so the UI can show the
                        # exact strike+expiry even for BSM-priced trades.
                        contract_expiry = bsm_expiry.isoformat()
                        bsm_series = list(zip(spot_dates, premium_series))
                        exit_premium, actual_exit_date, exit_reason = _resolve_option_exit(
                            series=bsm_series,
                            entry_premium=entry_premium,
                            expiry=bsm_expiry,
                            profit_target_pct=req.profit_target_pct,
                            stop_loss_pct=req.stop_loss_pct,
                            dte_exit=req.dte_exit,
                        )
                        stopped_out = exit_reason == "stop"

                    # Transaction-cost model: cross half the bid/ask spread on
                    # each side. You buy higher and sell lower than the mark, so
                    # marginal trades that look like wins on frictionless marks
                    # become losers — this is what makes the win rate realistic.
                    slip = req.slippage_pct or 0.0
                    entry_premium = entry_premium * (1.0 + slip / 2.0)
                    exit_premium = exit_premium * (1.0 - slip / 2.0)

                    pnl = exit_premium - entry_premium
                    return_pct = pnl / entry_premium if entry_premium > 0 else 0.0
                    # The exit_spot recorded on the trade reflects the actual
                    # close date (may differ from the planned exit_date when
                    # stopped out).
                    actual_exit_spot = next(
                        (b.close for b, dd in zip(bars, dates_t) if dd == actual_exit_date),
                        exit_spot,
                    )

                    record = TradeRecord(
                        ticker=ticker,
                        strategy=req.strategy,
                        direction=trade_dir,
                        open_date=d.isoformat(),
                        close_date=actual_exit_date.isoformat(),
                        conviction=scored["conviction"],
                        conviction_pct=scored_conv_pct,
                        strike=float(strike),
                        sigma=sigma,
                        entry_spot=spot,
                        exit_spot=actual_exit_spot,
                        entry_premium=entry_premium,
                        exit_premium=exit_premium,
                        pnl=pnl,
                        return_pct=return_pct,
                        synthetic=synthetic,
                        contract_ticker=contract_ticker,
                        contract_expiry=contract_expiry,
                        stopped_out=stopped_out,
                        exit_reason=exit_reason,
                    )
                    trades.append(record)
                    yield SleeveCompleteEvent(  # reuse event class for streaming trades
                        sleeve="trade",
                        rows=[record.model_dump()],
                    ).to_sse()

                # Tiny yield so SSE flushes between days.
                await asyncio.sleep(0)

            # Summary stats.
            n = len(trades)
            wins = sum(1 for t in trades if t.pnl > 0)
            total_pnl = sum(t.pnl for t in trades)
            avg_return = sum(t.return_pct for t in trades) / n if n else 0.0
            win_rate = wins / n if n else 0.0

            # Bucket performance by conviction-percentage band rather than the
            # legacy 0-3 count, matching the %-based gate.
            def _conv_band(pct: float) -> str:
                if pct >= 80:
                    return "80-100%"
                if pct >= 60:
                    return "60-80%"
                if pct >= 40:
                    return "40-60%"
                return "<40%"

            by_conviction: dict[str, list[TradeRecord]] = {}
            for t in trades:
                by_conviction.setdefault(_conv_band(t.conviction_pct), []).append(t)
            # Emit bands in descending order so the UI table reads high→low.
            _band_order = ["80-100%", "60-80%", "40-60%", "<40%"]
            conviction_summary = {
                band: {
                    "n_trades": len(v),
                    "win_rate": sum(1 for x in v if x.pnl > 0) / len(v) if v else 0.0,
                    "avg_return_pct": sum(x.return_pct for x in v) / len(v) if v else 0.0,
                    "total_pnl": sum(x.pnl for x in v),
                }
                for band in _band_order
                if (v := by_conviction.get(band))
            }

            n_synthetic = sum(1 for t in trades if t.synthetic)
            n_stopped = sum(1 for t in trades if t.stopped_out)
            stopped_trades = [t for t in trades if t.stopped_out]
            avg_loss_when_stopped = (
                sum(t.return_pct for t in stopped_trades) / len(stopped_trades)
                if stopped_trades
                else None
            )
            # How did trades close? Counts keyed by exit_reason so the UI can
            # show the mix (target / stop / dte / expiry / time backstop).
            by_exit_reason: dict[str, int] = {}
            for t in trades:
                by_exit_reason[t.exit_reason] = by_exit_reason.get(t.exit_reason, 0) + 1
            yield CompleteEvent(data={
                "n_trades": n,
                "n_wins": wins,
                "win_rate": win_rate,
                "total_pnl_per_share": total_pnl,
                "avg_return_pct": avg_return,
                "by_conviction": conviction_summary,
                "by_exit_reason": by_exit_reason,
                "trades": [t.model_dump() for t in trades],
                "pricing": req.pricing,
                "n_synthetic": n_synthetic,
                "n_stopped": n_stopped,
                "stop_loss_pct": req.stop_loss_pct,
                "profit_target_pct": req.profit_target_pct,
                "dte_exit": req.dte_exit,
                "hold_days": req.hold_days,
                "slippage_pct": req.slippage_pct,
                "min_conviction_pct": req.min_conviction_pct,
                "avg_return_when_stopped": avg_loss_when_stopped,
            }).to_sse()

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.exception("Options-strategy backtest failed")
            yield ErrorEvent(message=f"Backtest failed: {exc}").to_sse()
        finally:
            if not disconnect_task.done():
                disconnect_task.cancel()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ─── /sleeves/backtest/run (sleeves backtest SSE) ───────────────────────────

import os  # noqa: E402  (used for env-driven api_keys construction below)
import uuid  # noqa: E402

from app.backend.models.schemas import GraphEdge, GraphNode  # noqa: E402
from app.backend.services.backtest_service import BacktestService  # noqa: E402
from app.backend.services.graph import create_graph  # noqa: E402
from app.backend.services.portfolio import create_portfolio  # noqa: E402


class SleevesBacktestRequest(BaseModel):
    """Body for POST /sleeves/backtest/run.

    Defaults are kept tight (one short-window backtest on a small ticker
    set) because each trading day fires the real LLM agent panel — a wide
    request burns LLM credits in volume.
    """

    start_date: str = Field(description="Backtest start (YYYY-MM-DD).")
    end_date: str = Field(description="Backtest end (YYYY-MM-DD), inclusive.")
    sleeves: list[str] | None = Field(
        default=None, description="Sleeves to run. None = all configured sleeves."
    )
    tickers: list[str] | None = Field(
        default=None,
        description="Restrict to these tickers. None = each sleeve's full ticker list.",
    )
    initial_capital: float = Field(default=100_000.0, ge=1_000.0)
    margin_requirement: float = Field(default=0.0, ge=0.0)
    model_name: str = Field(
        default="deepseek-chat", description="LLM model name passed to the agents."
    )
    model_provider: str = Field(
        default="DeepSeek", description="LLM provider — agents pick a config via this."
    )


class _RequestShim:
    """Minimal stand-in for the upstream HedgeFundRequest object that
    BacktestService and its agents reach into.

    Only ``.api_keys`` is read in practice; the other attributes are
    referenced by some agents but tolerated as missing/empty. We populate
    api_keys from the .env-loaded process env so DATA_PROVIDER=fds keeps
    working through the FDS API key.
    """

    def __init__(self, model_name: str, model_provider: str):
        self.api_keys: dict[str, str] = {
            "FINANCIAL_DATASETS_API_KEY": os.environ.get("FINANCIAL_DATASETS_API_KEY", ""),
            "MASSIVE_API_KEY": os.environ.get("MASSIVE_API_KEY", ""),
            "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", ""),
            "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
        }
        self.model_name = model_name
        self.model_provider = model_provider
        # Some agents look here for per-agent model overrides; none = use global.
        self.agent_models: list[Any] = []


def _build_sleeve_graph(agents: list[str]) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Synthesize a minimal React-Flow-shaped graph for ``agents``.

    The shape matches what ``create_graph`` expects:
    - one node per analyst, id = ``"{agent_key}_{6char-suffix}"`` so
      ``extract_base_agent_key`` cleanly recovers the canonical key.
    - one portfolio_manager node (which gets its own paired risk_manager
      auto-created by create_graph).
    - edges: every analyst → portfolio_manager. ``create_graph`` rewrites
      the analyst→PM edge to route through the auto-created risk_manager,
      so we just declare intent.
    """
    # Stable per-call suffix keeps ids unique without leaking across requests.
    suffix = uuid.uuid4().hex[:6]
    pm_id = f"portfolio_manager_{suffix}"

    nodes: list[GraphNode] = [GraphNode(id=pm_id)]
    edges: list[GraphEdge] = []

    seen: set[str] = set()
    for agent in agents:
        if agent in seen:
            continue
        seen.add(agent)
        node_id = f"{agent}_{suffix}"
        nodes.append(GraphNode(id=node_id))
        edges.append(GraphEdge(id=f"{node_id}->pm", source=node_id, target=pm_id))

    return nodes, edges


@router.post("/backtest/run")
async def backtest_sleeves(req: SleevesBacktestRequest, request: Request):
    """Run a sleeves backtest: agents fire each trading day, decisions feed
    a simulated portfolio, equity curve streams via SSE.

    Wraps the upstream ``BacktestService`` with a sleeve-derived agent
    graph. After the daily loop completes, this endpoint also computes
    sleeve / agent attribution from the per-day decision stream and emits
    it in the final 'complete' event.

    Cost warning: each trading day fires the union of all selected
    sleeves' agent panels on every ticker. A 6-month backtest with 4
    sleeves × 10 tickers can be many thousands of LLM calls. Use the
    smallest plausible scope when smoke-testing.
    """
    selected_sleeves = _resolve_selected_sleeves(
        ScanRequest(sleeves=req.sleeves, tickers=req.tickers, end_date=req.end_date)
    )

    # Unified ticker list (dedup across sleeves) + union of agent panels.
    tickers: list[str] = []
    seen_t: set[str] = set()
    for sleeve in selected_sleeves.values():
        for t in sleeve["tickers"]:
            if t not in seen_t:
                seen_t.add(t)
                tickers.append(t)
    if not tickers:
        raise HTTPException(status_code=400, detail="No tickers after sleeve/ticker filtering.")

    # Pre-flight: prove every ticker has at least one bar in the window.
    # The upstream BacktestService silently skips any day where ANY ticker
    # has missing data, producing a successful-but-empty backtest the user
    # can't distinguish from "no trade signals." Fail loudly instead.
    try:
        pf_client = MassiveClient()
        pf_start = (datetime.date.fromisoformat(req.start_date) - datetime.timedelta(days=10)).isoformat()
        pf_end = req.end_date
        missing: list[str] = []
        for ticker in tickers:
            try:
                aggs = pf_client.get_daily_aggregates(ticker, pf_start, pf_end)
                if not (aggs.get("results") or []):
                    missing.append(ticker)
            except MassiveError:
                missing.append(ticker)
        if missing and len(missing) == len(tickers):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No price data for any of {tickers} in {req.start_date}..{req.end_date}. "
                    "Check the date range — pre-IPO names or far-future dates have no data."
                ),
            )
        if missing:
            # Drop missing tickers and continue; warn via SSE later.
            tickers = [t for t in tickers if t not in missing]
            logger.warning("Backtest dropping tickers with no data in window: %s", missing)
            # Re-validate ticker_to_sleeve consistency.
            ticker_to_sleeve = {t: s for t, s in ticker_to_sleeve.items() if t in tickers} if False else ticker_to_sleeve  # keep both branches stable
    except HTTPException:
        raise
    except Exception as exc:
        # Pre-flight failure (e.g. network) shouldn't kill the request — log
        # and continue; BacktestService will hit the same issue and the user
        # will see an empty result we can flag at the end.
        logger.warning("Backtest pre-flight failed (non-fatal): %s", exc)
        missing = []

    agent_union: list[str] = []
    seen_a: set[str] = set()
    for sleeve in selected_sleeves.values():
        for a in sleeve["agents"]:
            if a not in seen_a:
                seen_a.add(a)
                agent_union.append(a)

    # ticker → sleeve mapping for attribution (first sleeve wins on overlap).
    ticker_to_sleeve: dict[str, str] = {}
    for name, sleeve in selected_sleeves.items():
        for t in sleeve["tickers"]:
            ticker_to_sleeve.setdefault(t, name)

    nodes, edges = _build_sleeve_graph(agent_union)
    try:
        graph = create_graph(nodes, edges).compile()
    except Exception as exc:
        logger.exception("Failed to compile sleeve graph")
        raise HTTPException(status_code=500, detail=f"Graph compile failed: {exc}")

    portfolio = create_portfolio(
        initial_cash=req.initial_capital,
        margin_requirement=req.margin_requirement,
        tickers=tickers,
        portfolio_positions=[],
    )
    shim = _RequestShim(req.model_name, req.model_provider)

    service = BacktestService(
        graph=graph,
        portfolio=portfolio,
        tickers=tickers,
        start_date=req.start_date,
        end_date=req.end_date,
        initial_capital=req.initial_capital,
        model_name=req.model_name,
        model_provider=req.model_provider,
        request=shim,
    )

    async def _detect_disconnect() -> bool:
        try:
            while True:
                msg = await request.receive()
                if msg["type"] == "http.disconnect":
                    return True
        except Exception:
            return True

    async def event_generator():
        progress_queue: asyncio.Queue = asyncio.Queue()
        backtest_task: asyncio.Task | None = None
        disconnect_task: asyncio.Task | None = None

        def agent_progress_handler(agent_name, ticker, status, analysis, timestamp):
            # Forward agent-level progress (one per agent.update_status call)
            # into the SSE stream so the UI live-log can show "warren_buffett
            # · NVDA · Done" type entries during the backtest.
            progress_queue.put_nowait(
                ProgressUpdateEvent(
                    agent=agent_name,
                    ticker=ticker,
                    status=status,
                    analysis=analysis,
                    timestamp=timestamp,
                )
            )

        def backtest_callback(update):
            # BacktestService emits {"type": "progress" | "backtest_result", ...}.
            # Translate into ProgressUpdateEvent for UI compatibility — the
            # day result is JSON-serialized into `analysis` so the frontend
            # can build the equity curve incrementally.
            if update.get("type") == "progress":
                progress_queue.put_nowait(
                    ProgressUpdateEvent(
                        agent="backtest",
                        ticker=None,
                        status=(
                            f"Processing {update['current_date']} "
                            f"({update['current_step']}/{update['total_dates']})"
                        ),
                    )
                )
            elif update.get("type") == "backtest_result":
                day = update["data"]
                progress_queue.put_nowait(
                    ProgressUpdateEvent(
                        agent="backtest",
                        ticker=None,
                        status=f"Completed {day['date']} - Portfolio: ${day['portfolio_value']:,.2f}",
                        analysis=json.dumps(day, default=str),
                    )
                )

        progress.register_handler(agent_progress_handler)
        try:
            yield StartEvent(data={
                "tickers": tickers,
                "agents": agent_union,
                "start_date": req.start_date,
                "end_date": req.end_date,
                "initial_capital": req.initial_capital,
                "missing_tickers": missing,  # may be empty
            }).to_sse()
            if missing:
                yield ProgressUpdateEvent(
                    agent="backtest",
                    ticker=None,
                    status=(
                        f"Skipping {len(missing)} ticker(s) with no price data in window: "
                        f"{', '.join(missing)}"
                    ),
                ).to_sse()

            backtest_task = asyncio.create_task(
                service.run_backtest_async(progress_callback=backtest_callback)
            )
            disconnect_task = asyncio.create_task(_detect_disconnect())

            while not backtest_task.done():
                if disconnect_task.done():
                    logger.info("Client disconnected; cancelling backtest")
                    backtest_task.cancel()
                    return
                try:
                    evt = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                    yield evt.to_sse()
                except asyncio.TimeoutError:
                    continue

            # Drain leftover queue events.
            while not progress_queue.empty():
                yield progress_queue.get_nowait().to_sse()

            try:
                result = await backtest_task
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.exception("Sleeves backtest failed")
                yield ErrorEvent(message=f"Backtest failed: {exc}").to_sse()
                return

            # ── D4: attribution from the per-day result stream ────────────
            day_results = result.get("results", [])
            trades: list[Trade] = extract_trades_from_day_results(
                day_results,
                ticker_to_sleeve=ticker_to_sleeve,
            )
            sleeve_metrics = compute_sleeve_metrics(trades)
            agent_attr = compute_agent_attribution(trades, dict(_live_sleeves()))
            warnings = warn_underperforming_agents(trades)

            # Compute headline summary so the frontend doesn't have to
            # recompute from day_results. Total return relative to initial
            # capital is the most user-meaningful number.
            initial_capital = float(req.initial_capital)
            final_value = (
                float(day_results[-1].get("portfolio_value", initial_capital))
                if day_results
                else initial_capital
            )
            total_return_pct = (
                (final_value - initial_capital) / initial_capital * 100.0
                if initial_capital
                else 0.0
            )

            # Serialize trades for the UI — Trade is a dataclass so we
            # explicitly project the readable fields.
            serialized_trades = [
                {
                    "ticker": t.ticker,
                    "sleeve": t.sleeve,
                    "agent": t.agent,
                    "open_date": t.open_date.isoformat(),
                    "close_date": t.close_date.isoformat(),
                    "side": t.side,
                    "hold_days": t.hold_days,
                    "pnl": t.pnl,
                    "entry_value": t.entry_value,
                    "return_pct": t.return_pct,
                }
                for t in trades
            ]

            yield CompleteEvent(data={
                "summary": {
                    "initial_capital": initial_capital,
                    "final_value": final_value,
                    "total_return_pct": total_return_pct,
                    "n_days_simulated": len(day_results),
                    "n_trades": len(trades),
                    "missing_tickers": missing,
                },
                "performance_metrics": result.get("performance_metrics", {}),
                "final_portfolio": result.get("final_portfolio", {}),
                "results": day_results,
                "trades": serialized_trades,
                "attribution": {
                    "n_trades": len(trades),
                    "sleeves": {
                        sm.sleeve: {
                            "n_trades": sm.n_trades,
                            "win_rate": sm.win_rate,
                            "avg_hold_days": sm.avg_hold_days,
                            "total_pnl": sm.total_pnl,
                            "sharpe": sm.sharpe,
                            "max_drawdown": sm.max_drawdown,
                        }
                        for sm in sleeve_metrics.values()
                    },
                    "agents": {
                        aa.agent: {
                            "n_trades": aa.n_trades,
                            "win_rate": aa.win_rate,
                            "total_pnl_attributed": aa.total_pnl_attributed,
                            "avg_return_pct": aa.avg_return_pct,
                        }
                        for aa in agent_attr.values()
                    },
                    "warnings": [w.message() for w in warnings],
                },
            }).to_sse()

        except asyncio.CancelledError:
            return
        finally:
            progress.unregister_handler(agent_progress_handler)
            if backtest_task and not backtest_task.done():
                backtest_task.cancel()
            if disconnect_task and not disconnect_task.done():
                disconnect_task.cancel()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ─── /sleeves/watchlist (read + write) ──────────────────────────────────────


class WatchlistEntry(BaseModel):
    """One ticker, with optional free-text comment."""

    ticker: str
    comment: str = ""


class WatchlistPayload(BaseModel):
    entries: list[WatchlistEntry]


@router.get("/watchlist")
async def get_watchlist_endpoint() -> dict[str, Any]:
    """Return the current watchlist with per-ticker comments."""
    return {"entries": read_watchlist_with_comments()}


@router.put("/watchlist")
async def put_watchlist_endpoint(payload: WatchlistPayload) -> dict[str, Any]:
    """Replace the watchlist. Atomic write, then reload the module.

    Validation lives in ``watchlist_service.write_watchlist`` — bad tickers
    raise ``HTTPException(400)``.
    """
    persisted = write_watchlist([e.model_dump() for e in payload.entries])
    return {"entries": persisted}


# ─── Multi-watchlist endpoints ────────────────────────────────────────────────

@router.get("/watchlists")
async def get_watchlists() -> dict[str, Any]:
    """Return all named watchlists."""
    return {"watchlists": watchlists_service.get_all()}


@router.post("/watchlists")
async def create_watchlist(body: dict[str, Any]) -> dict[str, Any]:
    """Create a new watchlist. Body: {name: str, tickers?: [{ticker, comment}]}"""
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    tickers = body.get("tickers", [])
    result = watchlists_service.upsert(name, tickers)
    return result


@router.put("/watchlists/{name}")
async def update_watchlist(name: str, body: dict[str, Any]) -> dict[str, Any]:
    """Replace the tickers in a watchlist. Body: {tickers: [{ticker, comment}]}"""
    tickers = body.get("tickers", [])
    result = watchlists_service.upsert(name, tickers)
    return result


@router.patch("/watchlists/{name}/rename")
async def rename_watchlist(name: str, body: dict[str, Any]) -> dict[str, Any]:
    """Rename a watchlist. Body: {new_name: str}"""
    new_name = body.get("new_name", "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="new_name is required")
    ok = watchlists_service.rename(name, new_name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Watchlist '{name}' not found")
    return {"name": new_name}


@router.delete("/watchlists/{name}")
async def delete_watchlist(name: str) -> dict[str, Any]:
    """Delete a watchlist by name."""
    ok = watchlists_service.delete(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Watchlist '{name}' not found")
    return {"deleted": name}


# ─── /sleeves/portfolio/settings ────────────────────────────────────────────


@router.get("/portfolio/settings")
async def get_portfolio_settings() -> dict[str, Any]:
    """Return the full per-ticker portfolio settings overlay.

    Shape: ``{ "settings": { "<sleeve>": { "<TICKER>": { "allocation_pct": float, "agents": null | [...] } } } }``
    """
    return {"settings": portfolio_settings_service.get_all()}


class PortfolioSettingsPayload(BaseModel):
    """Body for full-replace of the portfolio settings overlay."""

    settings: dict[str, Any] = Field(
        description="Full settings dict — sleeve → ticker → {allocation_pct, agents}.",
    )


@router.put("/portfolio/settings")
async def put_portfolio_settings(payload: PortfolioSettingsPayload) -> dict[str, Any]:
    """Replace the entire per-ticker portfolio settings overlay atomically.

    Useful for bulk edits (e.g. importing a pre-built allocation table). For
    single-ticker updates prefer the per-ticker endpoints (when available).
    """
    updated = portfolio_settings_service.put_all(payload.settings)
    return {"settings": updated}


# ─── /sleeves/thesis/{scope} (LLM PM-memo synthesis) ────────────────────────


from app.backend.services.thesis_service import (  # noqa: E402
    synthesize_portfolio_thesis,
    synthesize_sleeve_thesis,
)


def _portfolio_thesis_inputs() -> tuple[str, dict, list, list] | None:
    """Build the structured inputs the thesis service expects, from the most
    recent scan + live sleeve config. Returns None if there is no scan to
    synthesize against.
    """
    scan = _latest_scan_summary()  # defined elsewhere in this module
    if scan is None or not scan.get("rows"):
        return None

    rows = scan["rows"]
    scan_date = scan.get("date") or ""
    sleeves_cfg = _live_sleeves()

    # Rollup
    bullish = sum(1 for r in rows if r.get("consensus") == "bullish")
    bearish = sum(1 for r in rows if r.get("consensus") == "bearish")
    neutral = sum(1 for r in rows if r.get("consensus") == "neutral")
    avg_conf = (
        sum(float(r.get("avg_confidence") or 0) for r in rows) / len(rows)
        if rows
        else 0.0
    )
    rollup = {
        "scanned": len(rows),
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
        "weighted_conviction": round(avg_conf, 1),
    }

    # Per-sleeve summaries
    per_sleeve: list[dict] = []
    for name, meta in sleeves_cfg.items():
        sleeve_rows = [r for r in rows if r.get("sleeve") == name]
        if not sleeve_rows:
            continue
        per_sleeve.append(
            {
                "name": name,
                "allocation_pct": meta.get("allocation_pct"),
                "agents": list(meta.get("agents", [])),
                "scanned": len(sleeve_rows),
                "bullish": sum(
                    1 for r in sleeve_rows if r.get("consensus") == "bullish"
                ),
                "bearish": sum(
                    1 for r in sleeve_rows if r.get("consensus") == "bearish"
                ),
                "neutral": sum(
                    1 for r in sleeve_rows if r.get("consensus") == "neutral"
                ),
                "weighted_conviction": round(
                    sum(
                        float(r.get("avg_confidence") or 0) for r in sleeve_rows
                    )
                    / len(sleeve_rows),
                    1,
                ),
            }
        )

    # High-conviction signals (top 8 by abs weighted score)
    hc = sorted(
        rows,
        key=lambda r: abs(float(r.get("weighted_score") or 0)),
        reverse=True,
    )[:8]
    high_conviction = [
        {
            "ticker": r.get("ticker"),
            "sleeve": r.get("sleeve"),
            "consensus": r.get("consensus"),
            "weighted_score": r.get("weighted_score"),
            "avg_confidence": r.get("avg_confidence"),
            "variant_perception": r.get("variant_perception"),
            "position_type": r.get("position_type"),
            "has_variant_perception": r.get("has_variant_perception"),
        }
        for r in hc
    ]
    return scan_date, rollup, per_sleeve, high_conviction


def _latest_scan_summary() -> dict[str, Any] | None:
    """Helper: parse the latest scan into a dict matching the GET
    /scans/latest response shape. Returns None if no scans on disk."""
    files = _list_scan_files()
    if not files:
        return None
    path = files[0]
    return _read_scan_json(path) if path.suffix == ".json" else _read_scan_csv(path)


# ─── Per-ticker thesis (Portfolio Pulse "Run analysis") ─────────────────────

_ticker_thesis_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _generate_ticker_thesis(ticker: str, context: str, deep: bool) -> dict[str, Any]:
    """One DeepSeek call producing a trade thesis for a single name.

    ``deep=False`` → a fast 2-3 sentence Quick take. ``deep=True`` → a richer
    multi-section read (thesis, bull case, bear case, fundamentals incl. the
    beat/miss record, catalysts, risks, verdict). Both are grounded in the
    saved agent analysis + Finnhub fundamentals passed in ``context``.
    """
    import json as _json
    import os as _os

    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    if deep:
        system = (
            "You are a senior buy-side analyst. Using the saved multi-agent signal "
            "analysis and the fundamentals provided (growth, margins, earnings "
            "beat/miss history, analyst consensus, insider flow) plus any recent "
            "news, write a thorough trade thesis. Lead with the strongest evidence. "
            "Respond ONLY with JSON: {\"bias\": \"bullish|bearish|neutral\", "
            "\"condensed\": \"one-sentence call\", \"full\": \"markdown with these "
            "sections: **Thesis**, **Bull case**, **Bear case**, **Fundamentals** "
            "(cite the beat/miss record and growth), **Catalysts & risks**, "
            "**Verdict**\"}."
        )
        max_tokens = 1100
    else:
        system = (
            "You are a buy-side analyst. Using the saved signal analysis and "
            "fundamentals (growth, earnings beat/miss, analyst consensus, insider "
            "flow), write a concise trade thesis. Respond ONLY with JSON: "
            "{\"bias\": \"bullish|bearish|neutral\", \"condensed\": \"one sentence\", "
            "\"full\": \"2-4 sentences that cite the beat/miss history, growth, and "
            "analyst consensus\"}."
        )
        max_tokens = 450

    llm = ChatOpenAI(
        model="deepseek-chat",
        openai_api_key=_os.environ.get("DEEPSEEK_API_KEY", ""),
        openai_api_base="https://api.deepseek.com/v1",
        temperature=0.3,
        max_tokens=max_tokens,
    )
    user = f"Ticker: {ticker}\n\n{context}"
    try:
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        txt = (resp.content or "").strip()
        start, end = txt.find("{"), txt.rfind("}")
        data = _json.loads(txt[start : end + 1]) if start >= 0 and end > start else {}
        return {
            "ticker": ticker,
            "depth": "deep" if deep else "quick",
            "bias": data.get("bias", "neutral"),
            "condensed": data.get("condensed", ""),
            "full": data.get("full", ""),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ticker thesis generation failed for %s: %s", ticker, exc)
        return {
            "ticker": ticker,
            "depth": "deep" if deep else "quick",
            "bias": "neutral",
            "condensed": "Could not generate a thesis — check the DeepSeek connection.",
            "full": "",
        }


@router.post("/thesis/ticker/{ticker}")
async def post_ticker_thesis(ticker: str, depth: str = "quick") -> dict[str, Any]:
    """Per-name analysis for Portfolio Pulse.

    ``depth=quick`` (default) returns a fast 2-3 sentence thesis; ``depth=deep``
    returns a richer multi-section read and also pulls recent news. Both are
    grounded in the saved scan agent analysis and Finnhub fundamentals.
    Cached per (ticker, depth, day).
    """
    symbol = (ticker or "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Ticker is required.")
    deep = depth == "deep"

    today_str = datetime.date.today().isoformat()
    cache_key = f"{symbol}:{depth}:{today_str}"
    now = time.monotonic()
    cached = _ticker_thesis_cache.get(cache_key)
    if cached and (now - cached[0]) < _REASON_CACHE_TTL_SECONDS:
        return cached[1]

    # Gather grounding context off the event loop.
    def _gather() -> str:
        blocks = [
            _chat_saved_analysis_block(symbol),
            _chat_fundamentals_block(symbol),
        ]
        if deep:
            blocks.append(_chat_news_block(symbol))
        joined = "\n\n".join(b for b in blocks if b)
        return joined or "No saved analysis or fundamentals available for this ticker."

    context = await asyncio.to_thread(_gather)
    result = await asyncio.to_thread(_generate_ticker_thesis, symbol, context, deep)
    _ticker_thesis_cache[cache_key] = (now, result)
    return result


@router.post("/thesis/portfolio")
async def post_portfolio_thesis() -> dict[str, Any]:
    """Synthesize the portfolio-level PM memo from the most recent scan.

    Cached by (scan_date + content signature) — re-fetching is cheap and
    re-running with the same data returns identical thesis. A new scan
    invalidates automatically since its date+signature differ.
    """
    inputs = _portfolio_thesis_inputs()
    if inputs is None:
        raise HTTPException(
            status_code=404,
            detail="No scan available to synthesize a thesis against.",
        )
    scan_date, rollup, per_sleeve, high_conviction = inputs
    return await asyncio.to_thread(
        synthesize_portfolio_thesis,
        scan_date=scan_date,
        portfolio_rollup=rollup,
        per_sleeve=per_sleeve,
        high_conviction=high_conviction,
    )


@router.post("/thesis/sleeve/{name}")
async def post_sleeve_thesis(name: str) -> dict[str, Any]:
    """Synthesize a sleeve-scoped PM memo."""
    sleeves_cfg = _live_sleeves()
    if name not in sleeves_cfg:
        raise HTTPException(
            status_code=400, detail=f"Unknown sleeve '{name}'."
        )

    scan = _latest_scan_summary()
    if scan is None or not scan.get("rows"):
        raise HTTPException(
            status_code=404,
            detail="No scan available to synthesize a thesis against.",
        )

    sleeve_rows = [r for r in scan["rows"] if r.get("sleeve") == name]
    if not sleeve_rows:
        raise HTTPException(
            status_code=404,
            detail=f"Sleeve '{name}' has no rows in the latest scan.",
        )

    meta = sleeves_cfg[name]
    sleeve_meta = {
        "name": name,
        "allocation_pct": meta.get("allocation_pct"),
        "agents": list(meta.get("agents", [])),
        "agent_weights": dict(meta.get("agent_weights", {})),
        "tickers": list(meta.get("tickers", [])),
    }

    return await asyncio.to_thread(
        synthesize_sleeve_thesis,
        sleeve_name=name,
        scan_date=scan["date"],
        sleeve_meta=sleeve_meta,
        rows=sleeve_rows,
    )
