"""Telegram high-confidence alert configuration routes.

User-facing (require auth). The bot token is a per-user secret (BYOK, encrypted
at rest) — the user creates their own bot via BotFather and pastes its token, so
no shared app-level bot and no cross-tenant exposure. The token is never returned
to the client; ``GET /alerts/settings`` reports only a ``has_token`` flag.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.backend.auth import get_current_user_id
from app.backend.services import telegram_alerts

router = APIRouter(prefix="/alerts", tags=["alerts"])


class SaveSettingsBody(BaseModel):
    enabled: bool | None = None
    min_confidence: float | None = Field(None, ge=0, le=100)
    timeframes: list[str] | None = None
    remote_enabled: bool | None = None


class TokenBody(BaseModel):
    token: str = Field(..., min_length=10, description="Telegram bot token from BotFather")


class PairBody(BaseModel):
    code: str = Field(..., description="The verification code the user sent to their bot")


@router.get("/settings")
async def get_settings(user_id: str = Depends(get_current_user_id)) -> dict:
    return telegram_alerts.get_settings()


@router.put("/settings")
async def save_settings(body: SaveSettingsBody, user_id: str = Depends(get_current_user_id)) -> dict:
    return telegram_alerts.save_settings(
        enabled=body.enabled, min_confidence=body.min_confidence, timeframes=body.timeframes,
        remote_enabled=body.remote_enabled,
    )


@router.post("/token")
async def set_token(body: TokenBody, user_id: str = Depends(get_current_user_id)) -> dict:
    try:
        telegram_alerts.set_bot_token(body.token)
    except Exception as exc:  # noqa: BLE001 — e.g. encryption not configured
        raise HTTPException(status_code=500, detail=f"Could not store token: {type(exc).__name__}")
    return {"ok": True, "has_token": True}


@router.post("/pair")
async def pair(body: PairBody, user_id: str = Depends(get_current_user_id)) -> dict:
    return await telegram_alerts.pair(body.code)


@router.post("/test")
async def send_test(user_id: str = Depends(get_current_user_id)) -> dict:
    ok = await telegram_alerts.send_test()
    if not ok:
        raise HTTPException(status_code=400, detail="No paired chat / bot token, or the send failed.")
    return {"ok": True}


@router.delete("/config")
async def disconnect(user_id: str = Depends(get_current_user_id)) -> dict:
    telegram_alerts.clear_config()
    return {"ok": True}
