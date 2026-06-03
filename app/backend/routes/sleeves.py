"""Sleeves Dashboard API — every ``/sleeves/*`` endpoint.

This module owns the HTTP/SSE layer for the dashboard. Grouped by concern
(see the section banners below):

* **Config + analysts** — read/CRUD the ``PORTFOLIO_SLEEVES`` config.
* **Scans** — list past morning-scan CSVs and stream a live scan over SSE
  (``start`` / ``progress`` / ``sleeve_complete`` / ``complete`` / ``error``).
* **Ticker enrichment** — price history, fundamentals, and Finnhub data for one name.
* **Options** — the screener + chain endpoints and the per-candidate "reason".
* **Quotes** — lightweight, time-boxed batch quotes for the left rail.
* **Chat** — the streaming research assistant.
* **Backtests** — the BSM/real options-strategy backtest and the sleeves backtest.
* **Watchlists + portfolio settings** — persisted user state.
* **Thesis** — LLM PM-memo synthesis at portfolio / sleeve / ticker scope.

The pure-computation scoring engine (per-strategy scorers, conviction-%
helpers, chart-pattern scorer factory, and ``_STRATEGY_REGISTRY``) lives in
``app/backend/services/options_scoring.py`` and is imported below — keeping
this file focused on routing.

Scan CSVs are produced by ``src/run_morning_scan.py`` under
``outputs/YYYY-MM-DD_morning_scan.csv``; each row carries an aggregated
weighted_score plus the per-agent verdicts (serialized), which we parse back
into a structured ``per_agent`` list for the UI.
"""
from __future__ import annotations

import asyncio
import csv
import datetime
import json
import logging
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


# Scorer engine extracted to a service module (see options_scoring.py).
from app.backend.services.options_scoring import (
    _BENCHMARK_TICKER,
    _STRATEGY_ORDER,
    _STRATEGY_REGISTRY,
    _VALID_STRATEGIES,
    _fetch_bars,
    _fetch_closes,
)



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
