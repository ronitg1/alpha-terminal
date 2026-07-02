"""Scheduled background pre-scan routes.

User-facing (require auth):
  GET    /scheduled/schedules        — list this user's scheduled times
  POST   /scheduled/schedules        — add a time {time_of_day, timezone}
  PATCH  /scheduled/schedules/{id}   — enable/disable {enabled}
  DELETE /scheduled/schedules/{id}   — remove a time
  GET    /scheduled/prescan          — this user's latest pre-computed results

Scheduler (no user auth — guarded by a shared secret):
  POST   /scheduled/run-due          — run every schedule that's due now

The external scheduler (a GitHub Actions cron) calls /scheduled/run-due with the
``X-Cron-Secret`` header every ~15 minutes. The secret must match the
``CRON_SECRET`` environment variable.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.backend.auth import get_current_user_id
from app.backend.services import prescan_runner, scan_schedule_service

router = APIRouter(prefix="/scheduled", tags=["scheduled"])


class AddScheduleBody(BaseModel):
    time_of_day: str = Field(..., description="24-hour HH:MM, local to timezone")
    timezone: str = Field("America/New_York", description="IANA timezone")
    timeframe: str = Field("day", description="Chart timeframe: week | day | 1h | 15m")
    lookback_days: int = Field(180, description="Lookback window (clamped to the timeframe's max)")


class ToggleBody(BaseModel):
    enabled: bool


class UpdateScheduleBody(BaseModel):
    timeframe: str = Field(..., description="Chart timeframe: week | day | 1h | 15m")
    lookback_days: int = Field(..., description="Lookback window (clamped to the timeframe's max)")


@router.get("/schedules")
async def list_schedules(user_id: str = Depends(get_current_user_id)) -> dict:
    return {"schedules": scan_schedule_service.list_schedules()}


@router.post("/schedules")
async def add_schedule(body: AddScheduleBody, user_id: str = Depends(get_current_user_id)) -> dict:
    try:
        return scan_schedule_service.add_schedule(
            body.time_of_day, body.timezone, body.timeframe, body.lookback_days
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/schedules/{schedule_id}")
async def toggle_schedule(
    schedule_id: int, body: ToggleBody, user_id: str = Depends(get_current_user_id)
) -> dict:
    try:
        return scan_schedule_service.set_schedule_enabled(schedule_id, body.enabled)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.put("/schedules/{schedule_id}")
async def update_schedule(
    schedule_id: int, body: UpdateScheduleBody, user_id: str = Depends(get_current_user_id)
) -> dict:
    """Change a schedule's timeframe + lookback."""
    try:
        return scan_schedule_service.update_schedule(schedule_id, body.timeframe, body.lookback_days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: int, user_id: str = Depends(get_current_user_id)) -> dict:
    try:
        scan_schedule_service.delete_schedule(schedule_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True}


@router.get("/prescan")
async def get_prescan(
    timeframe: str | None = None, user_id: str = Depends(get_current_user_id)
) -> dict:
    """The user's pre-scan for ``timeframe`` (e.g. ``1h``), or the most recently
    computed one across timeframes when ``timeframe`` is omitted."""
    return {"prescan": scan_schedule_service.get_prescan(timeframe)}


@router.post("/run-due")
async def run_due(x_cron_secret: str | None = Header(default=None)) -> dict:
    """Run all due pre-scans. Guarded by the shared CRON_SECRET, not user auth."""
    secret = os.environ.get("CRON_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Scheduler not configured (CRON_SECRET unset).")
    if not x_cron_secret or x_cron_secret != secret:
        raise HTTPException(status_code=403, detail="Invalid or missing cron secret.")
    return await prescan_runner.run_due()
