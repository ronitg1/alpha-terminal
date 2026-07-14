"""Outbound Telegram push — raw Bot API over httpx (no extra dependency).

Used to notify a user's phone when a scheduled scan surfaces a high-confidence
signal. Best-effort by design: a failed send is logged and dropped so it can
never break a scan. Honors Telegram flood-control (HTTP 429 ``retry_after``).

Only OUTBOUND push + a one-shot ``getUpdates`` poll (for pairing — discovering
the user's chat_id) are needed; we deliberately do NOT run a bot polling/webhook
daemon.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org"
_MAX_ATTEMPTS = 3


async def send_message(token: str, chat_id: str, text: str, *, parse_mode: str = "HTML") -> bool:
    """Send one message to ``chat_id``. Returns True on success, False on give-up.

    Never raises — the caller (a scan) must not fail because a push failed."""
    if not token or not chat_id or not text:
        return False
    url = f"{_API}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    resp = await client.post(url, json=payload)
                except httpx.TimeoutException:
                    if attempt == _MAX_ATTEMPTS - 1:
                        logger.warning("Telegram send timed out after %d attempts", _MAX_ATTEMPTS)
                        return False
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                if resp.status_code == 200:
                    return True
                if resp.status_code == 429:
                    # Flood control — honor Telegram's retry_after (capped at 30s).
                    retry_after = 1.0
                    try:
                        retry_after = float(resp.json().get("parameters", {}).get("retry_after", 1))
                    except Exception:  # noqa: BLE001
                        pass
                    if attempt == _MAX_ATTEMPTS - 1:
                        logger.warning("Telegram rate-limited; gave up after %d attempts", _MAX_ATTEMPTS)
                        return False
                    await asyncio.sleep(min(retry_after, 30.0))
                    continue
                # Any other 4xx (bad token/chat_id) won't succeed on retry.
                logger.warning("Telegram send failed: HTTP %s %s", resp.status_code, resp.text[:200])
                return False
    except Exception as exc:  # noqa: BLE001 — best-effort; must never break the caller
        logger.warning("Telegram send error: %s", type(exc).__name__)
        return False
    return False


async def get_updates(token: str) -> list[dict]:
    """One-shot fetch of recent bot updates, used only for pairing (to discover
    the chat_id of the user who messaged the bot). Returns [] on any error."""
    if not token:
        return []
    url = f"{_API}/bot{token}/getUpdates"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params={"allowed_updates": '["message"]'})
            if resp.status_code == 200:
                return resp.json().get("result", []) or []
            logger.warning("Telegram getUpdates failed: HTTP %s", resp.status_code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram getUpdates error: %s", type(exc).__name__)
    return []
