"""Ticker news with a Claude 'what changed / does it hit the thesis' line.

Instead of a raw feed, each recent headline for the user's tickers gets a one-line
read: what changed, and whether it SUPPORTS / THREATENS / is NEUTRAL to our thesis
on that name (using the saved thesis as context when we have one). All headlines go
through ONE LLM call to keep the cost down, and the result is cached per (ticker-set,
day) so re-opening the panel is free.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_cache_lock = threading.Lock()
_TTL = 3600.0  # 1h — news + LLM read is stable within the hour

_SYSTEM = (
    "You are a skeptical buy-side analyst. For EACH numbered news headline, write ONE "
    "short sentence: what actually changed and whether it SUPPORTS, THREATENS, or is "
    "NEUTRAL to the investment thesis on that stock. If a thesis is given in brackets, "
    "judge against it; otherwise judge against the obvious bull case. Be specific and "
    "concrete — no fluff, no restating the headline. Respond ONLY with a JSON array: "
    '[{"i": <index>, "impact": "supports|threatens|neutral", "line": "<one sentence>"}].'
)


def _thesis_for(theses: dict[str, Any], sym: str) -> dict[str, Any] | None:
    for depth in ("deep", "quick"):
        t = theses.get(f"ticker:{sym}:{depth}")
        if isinstance(t, dict):
            return t
    return None


def _parse_array(txt: str) -> list[dict[str, Any]]:
    start, end = txt.find("["), txt.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(txt[start : end + 1])
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def build_impact(tickers: list[str], *, limit: int = 12) -> list[dict[str, Any]]:
    """Recent headlines for ``tickers`` + a per-headline thesis-impact line."""
    from app.backend.services import finnhub_news, thesis_store
    from app.backend.services.llm_preferences import create_selected_chat_model

    syms = [t.strip().upper() for t in tickers if t.strip()][:25]
    if not syms:
        return []

    key = hashlib.sha256((",".join(sorted(syms)) + "|" + datetime.date.today().isoformat()).encode()).hexdigest()[:16]
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and (time.monotonic() - hit[0]) < _TTL:
            return hit[1]

    try:
        feed = finnhub_news.build_feed(syms, hours_back=72)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impact feed fetch failed: %s", type(exc).__name__)
        return []
    heads = (feed.get("book_headlines") or [])[:limit]
    if not heads:
        return []

    try:
        theses = thesis_store.get_all()
    except Exception:  # noqa: BLE001
        theses = {}

    prompt_lines = []
    for i, h in enumerate(heads):
        sym = str((h.get("related") or "")).split(",")[0].strip().upper()
        th = _thesis_for(theses, sym)
        ctx = ""
        if th:
            ctx = f" [our thesis: {th.get('bias', 'n/a')} — {str(th.get('condensed') or '')[:140]}]"
        prompt_lines.append(f"{i}. [{sym or '?'}] {h.get('headline')}{ctx}")

    impacts: dict[int, dict[str, Any]] = {}
    try:
        llm = create_selected_chat_model(temperature=0.2, max_tokens=900)
        from langchain_core.messages import HumanMessage, SystemMessage

        resp = llm.invoke([SystemMessage(content=_SYSTEM), HumanMessage(content="\n".join(prompt_lines))])
        for d in _parse_array((resp.content or "").strip()):
            if isinstance(d, dict) and isinstance(d.get("i"), int):
                impacts[d["i"]] = d
    except Exception as exc:  # noqa: BLE001 — degrade to headlines without the impact line
        logger.warning("Thesis-impact LLM call failed: %s", type(exc).__name__)

    out: list[dict[str, Any]] = []
    for i, h in enumerate(heads):
        d = impacts.get(i, {})
        impact = str(d.get("impact") or "neutral").lower()
        if impact not in ("supports", "threatens", "neutral"):
            impact = "neutral"
        out.append({
            "ticker": str((h.get("related") or "")).split(",")[0].strip().upper() or None,
            "headline": h.get("headline"),
            "url": h.get("url"),
            "source": h.get("source"),
            "datetime": h.get("datetime"),
            "impact": impact,
            "line": (re.sub(r"\s+", " ", str(d.get("line") or "")).strip()) or None,
        })

    with _cache_lock:
        _cache[key] = (time.monotonic(), out)
    return out
