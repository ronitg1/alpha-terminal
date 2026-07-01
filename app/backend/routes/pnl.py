"""P&L tracker API — mounted at /pnl/*.

Endpoints:

* ``GET    /pnl/positions``            — all tracked positions.
* ``POST   /pnl/positions``            — create (manual or one-click track).
* ``PATCH  /pnl/positions/{id}``       — edit qty/prices/notes or close.
* ``POST   /pnl/positions/{id}/close`` — close at a price.
* ``DELETE /pnl/positions/{id}``       — remove.
* ``GET    /pnl/marks``                — per-share marks for open positions
  (options via chain snapshot → contract-aggregate fallback; stocks via the
  latest daily close). 60s cache.
* ``GET    /pnl/summary``              — realized/unrealized totals, win
  rate, per-underlying rollup, equity curve. Marks included unless
  ``?marks=false``.
* ``GET    /pnl/account``              — simulated paper-trading account
  (cash, buying power, equity, P&L).
* ``POST   /pnl/account/reset``        — clear all paper trades.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.backend.services import pnl_service
from src.tools.massive import MassiveClient, MassiveError, convert_prices

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pnl", tags=["pnl"])

_MARKS_CACHE_TTL_SECONDS = 60
_marks_cache: dict[str, tuple[float, dict[str, Any]]] = {}


# ─── Models ──────────────────────────────────────────────────────────────────


class OptionLegPayload(BaseModel):
    type: Literal["call", "put"]
    strike: float = Field(gt=0)
    expiration: str  # YYYY-MM-DD
    contract_ticker: str | None = None


class PositionCreate(BaseModel):
    kind: Literal["option", "stock"]
    ticker: str = Field(min_length=1, max_length=10)
    side: Literal["long", "short"] = "long"
    qty: float = Field(gt=0)
    option: OptionLegPayload | None = None
    entry_price: float = Field(ge=0)
    entry_date: str | None = None
    source: Literal["manual", "screener", "pattern", "fidelity"] = "manual"
    real: bool = False
    notes: str = ""


class PositionPatch(BaseModel):
    qty: float | None = Field(default=None, gt=0)
    entry_price: float | None = Field(default=None, ge=0)
    entry_date: str | None = None
    side: Literal["long", "short"] | None = None
    notes: str | None = None
    real: bool | None = None


class ClosePayload(BaseModel):
    exit_price: float = Field(ge=0)
    exit_date: str | None = None


# ─── CRUD endpoints ──────────────────────────────────────────────────────────


@router.get("/positions")
async def list_positions() -> dict[str, Any]:
    """All tracked positions, open first then closed, newest first within each."""
    positions = pnl_service.get_all()
    positions.sort(key=lambda p: (p.get("status") != "open", p.get("created_at", "")), reverse=False)
    positions.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    positions.sort(key=lambda p: p.get("status") != "open")
    return {"positions": positions}


@router.post("/positions")
async def create_position(body: PositionCreate) -> dict[str, Any]:
    """Track a new position (paper idea or real fill)."""
    if body.kind == "option" and body.option is None:
        raise HTTPException(status_code=400, detail="Option positions need an option leg.")
    try:
        record = pnl_service.create({
            **body.model_dump(),
            "option": body.option.model_dump() if body.option else None,
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _marks_cache.clear()
    return record


@router.patch("/positions/{position_id}")
async def patch_position(position_id: str, body: PositionPatch) -> dict[str, Any]:
    """Edit a position's mutable fields."""
    record = pnl_service.update(position_id, body.model_dump(exclude_none=True))
    if record is None:
        raise HTTPException(status_code=404, detail=f"Position {position_id} not found.")
    _marks_cache.clear()
    return record


@router.post("/positions/{position_id}/close")
async def close_position(position_id: str, body: ClosePayload) -> dict[str, Any]:
    """Close a position at the given per-share price."""
    record = pnl_service.close(position_id, body.exit_price, body.exit_date)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Position {position_id} not found.")
    _marks_cache.clear()
    return record


@router.delete("/positions/{position_id}")
async def delete_position(position_id: str) -> dict[str, Any]:
    """Remove a position entirely (use close to keep history instead)."""
    if not pnl_service.delete(position_id):
        raise HTTPException(status_code=404, detail=f"Position {position_id} not found.")
    _marks_cache.clear()
    return {"deleted": position_id}


# ─── Marks ───────────────────────────────────────────────────────────────────


def _occ_ticker(underlying: str, option: dict[str, Any]) -> str:
    """Polygon contract symbol, e.g. O:NVDA260717C00200000."""
    exp = option["expiration"].replace("-", "")[2:]  # YYMMDD
    cp = "C" if option["type"] == "call" else "P"
    strike_millis = int(round(float(option["strike"]) * 1000))
    return f"O:{underlying.upper()}{exp}{cp}{strike_millis:08d}"


def _stock_mark(client: MassiveClient, ticker: str) -> tuple[float | None, str]:
    today = _dt.date.today()
    start = (today - _dt.timedelta(days=7)).isoformat()
    try:
        aggs = client.get_daily_aggregates(ticker, start, today.isoformat())
        prices = convert_prices(aggs)
        if prices:
            return float(prices[-1].close), "last_close"
    except MassiveError as exc:
        logger.warning("Stock mark failed for %s: %s", ticker, exc)
    return None, "unavailable"


def _option_mark(client: MassiveClient, ticker: str, option: dict[str, Any]) -> tuple[float | None, str]:
    # 1. Chain snapshot filtered to the exact contract — carries live quotes.
    try:
        snap = client.get_options_chain(
            ticker,
            expiration_date=option["expiration"],
            contract_type=option["type"],
            strike_price_gte=float(option["strike"]) - 0.001,
            strike_price_lte=float(option["strike"]) + 0.001,
            limit=5,
        )
        for row in snap.get("results") or []:
            quote = row.get("last_quote") or {}
            bid, ask = quote.get("bid"), quote.get("ask")
            if bid and ask and ask > 0:
                return round((float(bid) + float(ask)) / 2, 4), "mid_quote"
            trade = row.get("last_trade") or {}
            if trade.get("price"):
                return float(trade["price"]), "last_trade"
            day = row.get("day") or {}
            if day.get("close"):
                return float(day["close"]), "day_close"
    except MassiveError as exc:
        logger.warning("Chain snapshot mark failed for %s: %s", ticker, exc)

    # 2. Contract daily aggregates — works after hours and for stale chains.
    try:
        occ = option.get("contract_ticker") or _occ_ticker(ticker, option)
        today = _dt.date.today()
        start = (today - _dt.timedelta(days=7)).isoformat()
        aggs = client.get_option_aggregates(occ, start, today.isoformat())
        results = aggs.get("results") or []
        if results:
            return float(results[-1].get("c")), "contract_close"
    except MassiveError as exc:
        logger.warning("Contract aggregate mark failed for %s: %s", ticker, exc)
    return None, "unavailable"


def _compute_marks(open_positions: list[dict[str, Any]]) -> dict[str, Any]:
    """Synchronous mark computation — runs in a worker thread."""
    client = MassiveClient()
    marks: dict[str, dict[str, Any]] = {}
    stock_cache: dict[str, tuple[float | None, str]] = {}
    for p in open_positions:
        if p["kind"] == "stock":
            if p["ticker"] not in stock_cache:
                stock_cache[p["ticker"]] = _stock_mark(client, p["ticker"])
            mark, source = stock_cache[p["ticker"]]
        else:
            mark, source = _option_mark(client, p["ticker"], p.get("option") or {})
        marks[p["id"]] = {"mark": mark, "source": source}
    return marks


async def _marks_for(positions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    open_positions = [p for p in positions if p.get("status") == "open"]
    if not open_positions:
        return {}
    cache_key = ",".join(sorted(p["id"] for p in open_positions))
    now = time.monotonic()
    hit = _marks_cache.get(cache_key)
    if hit and now - hit[0] < _MARKS_CACHE_TTL_SECONDS:
        return hit[1]
    marks = await asyncio.to_thread(_compute_marks, open_positions)
    _marks_cache[cache_key] = (now, marks)
    return marks


@router.get("/account")
async def get_account() -> dict[str, Any]:
    """Simulated paper-trading account state (cash, buying power, equity, P&L),
    derived from the tracked positions + live marks."""
    positions = pnl_service.get_all()
    mark_meta = await _marks_for(positions)
    marks = {pid: m["mark"] for pid, m in mark_meta.items()}
    snapshot = pnl_service.account_snapshot(positions, marks)
    snapshot["asof"] = _dt.datetime.now().isoformat(timespec="seconds")
    return snapshot


@router.post("/account/reset")
async def reset_account() -> dict[str, Any]:
    """Clear all paper trades — resets buying power to the full starting cash."""
    removed = pnl_service.clear_all()
    _marks_cache.clear()
    return {"reset": True, "removed": removed, "starting_cash": pnl_service.DEFAULT_STARTING_CASH}


@router.get("/marks")
async def get_marks() -> dict[str, Any]:
    """Per-share marks for all open positions. {id: {mark, source}}."""
    positions = pnl_service.get_all()
    return {"marks": await _marks_for(positions), "asof": _dt.datetime.now().isoformat(timespec="seconds")}


@router.get("/summary")
async def get_summary(marks: bool = True) -> dict[str, Any]:
    """Aggregate P&L. Pass ?marks=false to skip provider calls (realized only)."""
    positions = pnl_service.get_all()
    mark_map: dict[str, float | None] = {}
    mark_meta: dict[str, dict[str, Any]] = {}
    if marks:
        mark_meta = await _marks_for(positions)
        mark_map = {pid: m["mark"] for pid, m in mark_meta.items()}
    summary = pnl_service.summarize(positions, mark_map)
    summary["marks"] = mark_meta
    summary["asof"] = _dt.datetime.now().isoformat(timespec="seconds")
    return summary
