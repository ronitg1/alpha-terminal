from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MODELS_URL = "https://openrouter.ai/api/v1/models"
_CACHE_TTL_SECONDS = 600
_cache: tuple[float, list[dict[str, Any]]] | None = None


async def get_openrouter_models() -> list[dict[str, Any]]:
    global _cache

    now = time.monotonic()
    if _cache is not None and (now - _cache[0]) < _CACHE_TTL_SECONDS:
        return list(_cache[1])

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            resp = await client.get(_MODELS_URL)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenRouter model catalog fetch failed: %s", exc)
        return []

    rows: list[dict[str, Any]] = []
    for item in payload.get("data", []):
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        name = str(item.get("name") or model_id).strip()
        display = f"{name} ({model_id})" if name and name != model_id else model_id
        rows.append(
            {
                "display_name": display,
                "model_name": model_id,
                "provider": "OpenRouter",
                "context_length": item.get("context_length"),
                "pricing": item.get("pricing") if isinstance(item.get("pricing"), dict) else None,
                "supported_parameters": item.get("supported_parameters")
                if isinstance(item.get("supported_parameters"), list)
                else [],
            }
        )

    rows.sort(key=lambda r: str(r["display_name"]).lower())
    _cache = (now, rows)
    return list(rows)
