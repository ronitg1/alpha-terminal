"""Telegram high-confidence alerts — config, dedup, and dispatch.

Wiring: the scheduled pre-scan runner calls :func:`maybe_notify` right after a
scan completes (user identity + timeframe in scope). We filter that scan's
results by the user's confidence threshold + enabled timeframes, drop any signal
already pushed (dedup ledger), and send ONE batched Telegram message.

Storage (dual backend, mirroring the other services):
- Bot TOKEN (a secret) → the encrypted ``api_keys`` store, provider
  ``telegram_bot`` (always the DB/SQLite table; Fernet-encrypted at rest).
- Routing/rules (chat_id, enabled, threshold, timeframes) → ``user_settings``
  columns under ``db``; ``app/data/alert_settings.json`` under ``file``.
- Dedup ledger → ``notified_signals`` table under ``db``;
  ``app/data/notified_signals.json`` under ``file``.

Everything here is best-effort: an alert failure is logged and swallowed so it
can never break a scan.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from app.backend.repositories.api_key_repository import ApiKeyRepository
from app.backend.repositories.portfolio_repository import PortfolioRepository
from app.backend.services import telegram_notify
from app.backend.services._storage import current_user_id, session_scope, use_db

logger = logging.getLogger(__name__)

_TOKEN_PROVIDER = "telegram_bot"
_VALID_TIMEFRAMES = {"week", "day", "1h", "15m"}
_DEFAULT_TIMEFRAMES = ["day", "1h"]
_DEFAULT_THRESHOLD = 90.0
_DEDUP_TTL_DAYS = 30

_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "data" / "alert_settings.json"
_DEDUP_PATH = Path(__file__).resolve().parents[2] / "data" / "notified_signals.json"
# File backend only: the bot token lives here (gitignored app/data, the user's
# own machine — same trust level as their .env). On the db/cloud backend the
# token is Fernet-encrypted in the api_keys store instead (see _set_token).
_SECRETS_PATH = Path(__file__).resolve().parents[2] / "data" / "alert_secrets.json"


# ─── small file helpers (atomic write, mirroring the other file stores) ───────

def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _clean_timeframes(tfs: Any) -> list[str]:
    if not isinstance(tfs, (list, tuple)):
        return list(_DEFAULT_TIMEFRAMES)
    out = [t for t in ({str(x).strip() for x in tfs}) if t in _VALID_TIMEFRAMES]
    return sorted(out) or list(_DEFAULT_TIMEFRAMES)


def _default_settings() -> dict[str, Any]:
    return {"chat_id": None, "enabled": False, "min_confidence": _DEFAULT_THRESHOLD,
            "timeframes": list(_DEFAULT_TIMEFRAMES)}


# ─── settings (chat_id / enabled / threshold / timeframes) ────────────────────

def _get_settings(user_id: str) -> dict[str, Any]:
    if use_db():
        with session_scope() as db:
            return PortfolioRepository(db, user_id).get_alert_settings()
    return {**_default_settings(), **_read_json(_SETTINGS_PATH).get(user_id, {})}


def _save_settings(user_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    settings = {
        "chat_id": settings.get("chat_id"),
        "enabled": bool(settings.get("enabled", False)),
        "min_confidence": float(settings.get("min_confidence", _DEFAULT_THRESHOLD)),
        "timeframes": _clean_timeframes(settings.get("timeframes")),
    }
    if use_db():
        with session_scope() as db:
            return PortfolioRepository(db, user_id).set_alert_settings(
                chat_id=settings["chat_id"], enabled=settings["enabled"],
                min_confidence=settings["min_confidence"], timeframes=settings["timeframes"],
            )
    store = _read_json(_SETTINGS_PATH)
    store[user_id] = settings
    _write_json(_SETTINGS_PATH, store)
    return settings


# ─── bot token (secret → encrypted api_keys store) ────────────────────────────

def _get_token(user_id: str) -> str | None:
    if use_db():
        try:
            with session_scope() as db:
                return ApiKeyRepository(db, user_id).get_decrypted(_TOKEN_PROVIDER)
        except Exception as exc:  # noqa: BLE001 — missing encryption key etc.; treat as no token
            logger.warning("Telegram token read failed: %s", type(exc).__name__)
            return None
    return _read_json(_SECRETS_PATH).get(user_id) or None


def _set_token(user_id: str, token: str) -> None:
    token = token.strip()
    if use_db():
        with session_scope() as db:
            ApiKeyRepository(db, user_id).set_key(_TOKEN_PROVIDER, token, description="Telegram bot")
        return
    store = _read_json(_SECRETS_PATH)
    store[user_id] = token
    _write_json(_SECRETS_PATH, store)


def _clear_token(user_id: str) -> None:
    if use_db():
        with session_scope() as db:
            ApiKeyRepository(db, user_id).delete(_TOKEN_PROVIDER)
        return
    store = _read_json(_SECRETS_PATH)
    if store.pop(user_id, None) is not None:
        _write_json(_SECRETS_PATH, store)


# ─── dedup ledger ─────────────────────────────────────────────────────────────

def _signal_key(row: dict, timeframe: str) -> str:
    return f"{row.get('ticker')}|{row.get('pattern')}|{timeframe}|{row.get('end_date')}"


def _filter_unnotified(user_id: str, keys: list[str]) -> list[str]:
    if not keys:
        return []
    if use_db():
        with session_scope() as db:
            return PortfolioRepository(db, user_id).filter_unnotified(keys)
    seen = _read_json(_DEDUP_PATH).get(user_id, {})
    return [k for k in keys if k not in seen]


def _mark_notified(user_id: str, keys: list[str]) -> None:
    if not keys:
        return
    if use_db():
        with session_scope() as db:
            PortfolioRepository(db, user_id).mark_notified(keys)
        return
    store = _read_json(_DEDUP_PATH)
    now = datetime.datetime.now(datetime.timezone.utc)
    user_map = dict(store.get(user_id, {}))
    for k in keys:
        user_map[k] = now.isoformat()
    # Prune entries older than the TTL so the file can't grow unbounded.
    cutoff = (now - datetime.timedelta(days=_DEDUP_TTL_DAYS)).isoformat()
    user_map = {k: ts for k, ts in user_map.items() if ts >= cutoff}
    store[user_id] = user_map
    _write_json(_DEDUP_PATH, store)


# ─── message formatting ───────────────────────────────────────────────────────

_TF_LABEL = {"week": "Weekly", "day": "Daily", "1h": "1h", "15m": "15m"}


def _esc(s: Any) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_message(hits: list[dict], timeframe: str) -> str:
    tf = _TF_LABEL.get(timeframe, timeframe)
    head = f"<b>Alpha Terminal — {len(hits)} high-confidence {tf} signal(s)</b>"
    lines = []
    for r in sorted(hits, key=lambda x: float(x.get("confidence", 0)), reverse=True):
        arrow = "\U0001F7E2" if r.get("bullish") else "\U0001F534"  # green/red circle
        conf = round(float(r.get("confidence", 0)))
        lines.append(f"{arrow} <b>{_esc(r.get('ticker'))}</b> — {_esc(r.get('pattern'))} · {conf}%")
    return head + "\n" + "\n".join(lines)


# ─── the dispatch hook (called from prescan_runner) ───────────────────────────

async def maybe_notify(user_id: str, timeframe: str, results: list[dict]) -> int:
    """Alert on this scan's high-confidence signals. Returns how many were sent.
    Best-effort — never raises into the scan."""
    try:
        settings = _get_settings(user_id)
        if not settings.get("enabled") or timeframe not in settings.get("timeframes", []):
            return 0
        chat_id = settings.get("chat_id")
        if not chat_id:
            return 0
        threshold = float(settings.get("min_confidence", _DEFAULT_THRESHOLD))
        hits = [r for r in results if float(r.get("confidence", 0) or 0) >= threshold]
        if not hits:
            return 0
        token = _get_token(user_id)
        if not token:
            logger.info("Telegram alert skipped for %s: enabled but no bot token", user_id)
            return 0
        keyed = [(r, _signal_key(r, timeframe)) for r in hits]
        fresh = set(_filter_unnotified(user_id, [k for _, k in keyed]))
        fresh_hits = [(r, k) for r, k in keyed if k in fresh]
        if not fresh_hits:
            return 0
        ok = await telegram_notify.send_message(
            token, chat_id, _format_message([r for r, _ in fresh_hits], timeframe)
        )
        if ok:
            _mark_notified(user_id, [k for _, k in fresh_hits])
            return len(fresh_hits)
        return 0
    except Exception as exc:  # noqa: BLE001 — alerting must never break a scan
        logger.warning("Telegram alert dispatch failed for %s: %s", user_id, type(exc).__name__)
        return 0


# ─── route-facing helpers (current request's user) ────────────────────────────

def get_settings() -> dict[str, Any]:
    """Alert settings for the current user, with a ``has_token`` flag (never the
    token itself)."""
    uid = current_user_id()
    settings = _get_settings(uid)
    settings["has_token"] = bool(_get_token(uid))
    return settings


def save_settings(*, enabled: bool | None = None, min_confidence: float | None = None,
                  timeframes: list[str] | None = None) -> dict[str, Any]:
    """Update the current user's non-secret alert rules (partial)."""
    uid = current_user_id()
    cur = _get_settings(uid)
    if enabled is not None:
        cur["enabled"] = bool(enabled)
    if min_confidence is not None:
        cur["min_confidence"] = max(0.0, min(100.0, float(min_confidence)))
    if timeframes is not None:
        cur["timeframes"] = _clean_timeframes(timeframes)
    saved = _save_settings(uid, cur)
    saved["has_token"] = bool(_get_token(uid))
    return saved


def set_bot_token(token: str) -> None:
    _set_token(current_user_id(), token)


def clear_config() -> None:
    """Disconnect: drop the token and clear chat_id + disable alerts."""
    uid = current_user_id()
    _clear_token(uid)
    cur = _get_settings(uid)
    cur["chat_id"] = None
    cur["enabled"] = False
    _save_settings(uid, cur)


async def pair(code: str) -> dict[str, Any]:
    """Finish pairing: scan recent bot updates for the verification ``code`` the
    user sent, capture their chat_id, persist it, and confirm. Returns
    ``{paired: bool, chat_id?: str}``."""
    uid = current_user_id()
    token = _get_token(uid)
    if not token:
        return {"paired": False, "error": "Save your bot token first."}
    code = (code or "").strip()
    updates = await telegram_notify.get_updates(token)
    chat_id = None
    for upd in reversed(updates):  # newest first
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        if code and code in text:
            chat_id = str((msg.get("chat") or {}).get("id") or "")
            break
    if not chat_id:
        return {"paired": False, "error": "Didn't see your code yet — send it to your bot, then retry."}
    cur = _get_settings(uid)
    cur["chat_id"] = chat_id
    cur["enabled"] = True
    _save_settings(uid, cur)
    await telegram_notify.send_message(
        token, chat_id, "✅ <b>Alpha Terminal connected.</b> You'll get high-confidence signal alerts here."
    )
    return {"paired": True, "chat_id": chat_id}


async def send_test() -> bool:
    """Send a test alert to the current user's paired chat."""
    uid = current_user_id()
    token = _get_token(uid)
    settings = _get_settings(uid)
    chat_id = settings.get("chat_id")
    if not token or not chat_id:
        return False
    return await telegram_notify.send_message(
        token, chat_id, "\U0001F514 <b>Test alert from Alpha Terminal.</b> Alerts are working."
    )
