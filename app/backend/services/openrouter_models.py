from __future__ import annotations

import json
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
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        logger.warning("OpenRouter model catalog fetch failed: %s", type(exc).__name__)
        return []
    if not isinstance(payload, dict):
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
            }
        )

    rows.sort(key=lambda r: str(r["display_name"]).lower())
    _cache = (now, rows)
    return list(rows)
