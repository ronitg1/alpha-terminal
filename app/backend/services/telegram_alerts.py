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

import asyncio
import datetime
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from app.backend.database.app_models import UserSettings
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
# Telegram rejects a message over 4096 chars ("text is too long"). Each alerted
# signal now carries entry/target/contract/sizing (several lines), so cap lower.
_MAX_ALERT_SIGNALS = 15
# Only alert on signals whose breakout is within the last N days ("this week") —
# a 180d scan surfaces months-old patterns the user doesn't want pinged about.
_ALERT_RECENT_DAYS = 7
# Position sizing mirrors the Pattern Scanner's sizer: risk this % of the account
# per trade, sized in whole contracts off the option's per-contract risk.
_ALERT_RISK_PCT = 1.0
_DEFAULT_ACCOUNT = 25000.0

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
            "timeframes": list(_DEFAULT_TIMEFRAMES), "remote_enabled": False}


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
        "remote_enabled": bool(settings.get("remote_enabled", False)),
    }
    if use_db():
        with session_scope() as db:
            return PortfolioRepository(db, user_id).set_alert_settings(
                chat_id=settings["chat_id"], enabled=settings["enabled"],
                min_confidence=settings["min_confidence"], timeframes=settings["timeframes"],
                remote_enabled=settings["remote_enabled"],
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


def _fmt_price(v: Any) -> str | None:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return None


def _signal_date(row: dict) -> datetime.date | None:
    raw = row.get("end_date")
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _within_recent(row: dict, days: int = _ALERT_RECENT_DAYS) -> bool:
    """True when the signal's breakout is within the last ``days`` (and not future)."""
    d = _signal_date(row)
    if d is None:
        return False
    return 0 <= (datetime.date.today() - d).days <= days


def _sort_key(row: dict) -> tuple[int, float]:
    """Day first (most recent), then confidence — both descending via reverse=True."""
    d = _signal_date(row) or datetime.date.min
    return (d.toordinal(), float(row.get("confidence", 0) or 0))


async def _account_value() -> float:
    """Account size for position sizing — the user's connected portfolio value when
    available, else the Pattern Scanner's default. Best-effort; never raises."""
    try:
        from app.backend.services import portfolio_overview

        data = await portfolio_overview.build_overview()
        if isinstance(data, dict) and data.get("connected"):
            acct = data.get("combined") or (data.get("accounts") or [{}])[0]
            tv = acct.get("total_value") if isinstance(acct, dict) else None
            if tv and float(tv) > 0:
                return float(tv)
    except Exception as exc:  # noqa: BLE001 — sizing basis is best-effort
        logger.warning("Alert account-value lookup failed: %s", type(exc).__name__)
    return _DEFAULT_ACCOUNT


def _sizing_suffix(opt: dict, account: float) -> str | None:
    """Position size in whole contracts risking ~1% of the account, mirroring the
    Pattern Scanner sizer (contracts = account × risk% ÷ risk-per-contract). When
    even one contract exceeds the 1% budget, show the single-contract economics
    instead of a useless '0'."""
    try:
        rpc = float(opt.get("risk_per_contract"))
    except (TypeError, ValueError):
        return None
    if rpc <= 0 or account <= 0:
        return None
    prem_per = float(opt.get("entry_premium") or 0) * 100
    try:
        maxloss_per = float(opt.get("max_loss_per_contract"))
    except (TypeError, ValueError):
        maxloss_per = rpc
    n = int((account * _ALERT_RISK_PCT / 100.0) // rpc)
    if n >= 1:
        return f"size {n} ct (~${n * prem_per:,.0f}, max -${n * maxloss_per:,.0f})"
    # One contract already exceeds the 1% risk budget — show what a single costs/risks.
    return f"1 ct ~${prem_per:,.0f} (risks ${rpc:,.0f}, {rpc / account * 100:.1f}% acct)"


def _contract_suffix(opt: dict | None, account: float) -> str | None:
    """One-line option contract + R/R + sizing, e.g.
    'CALL $95 · exp 2026-08-15 (27d) · ~$3.20 · R/R 2.1 · size 3 ct (~$960, max -$960)'."""
    if not opt:
        return None
    typ = str(opt.get("type") or "").upper()
    strike = opt.get("strike")
    exp = opt.get("expiration")
    if not (typ and strike and exp):
        return None
    try:
        parts = [f"{typ} ${float(strike):g}", f"exp {exp}"]
    except (TypeError, ValueError):
        return None
    dte = opt.get("dte")
    if isinstance(dte, (int, float)):
        parts[-1] += f" ({int(dte)}d)"
    mid = opt.get("current_mid")
    try:
        if mid:
            parts.append(f"~${float(mid):.2f}")
    except (TypeError, ValueError):
        pass
    rr = opt.get("risk_reward")
    if rr:
        parts.append(f"R/R {rr}")
    sizing = _sizing_suffix(opt, account)
    if sizing:
        parts.append(sizing)
    return " · ".join(parts)


def render_signal_report(
    items: list[tuple[dict, dict | None]],
    *,
    header: str,
    account: float = _DEFAULT_ACCOUNT,
    more_count: int = 0,
    more_label: str = "more",
) -> str:
    """Plain-text signal report shared by Telegram alerts AND the /scan command, so
    the two look identical. Grouped under a per-day header (most recent first), each
    signal carrying entry → target, the recommended option contract (expiry + R/R),
    and position sizing. ``items`` are (signal, context) pairs already sorted by day
    then confidence; ``context`` is the enriched signal_context or None. Plain text
    (no HTML) so an arbitrary ticker/pattern can never trip Telegram's parser."""
    lines = [header]
    last_day: str | None = None
    for row, ctx in items:
        d = _signal_date(row)
        day_label = d.strftime("%b %d") if d else "—"
        if day_label != last_day:
            lines.append(f"\n{day_label}")
            last_day = day_label
        arrow = "\U0001F7E2" if row.get("bullish") else "\U0001F534"  # green/red circle
        conf = round(float(row.get("confidence", 0) or 0))
        forming = " · ⏳ forming" if row.get("forming") else ""  # hourglass = unconfirmed bar
        lines.append(f"{arrow} {row.get('ticker')} {row.get('pattern')} · {conf}%{forming}")
        ctx = ctx if isinstance(ctx, dict) else None
        entry = _fmt_price((ctx or {}).get("entry"))
        target = _fmt_price((ctx or {}).get("target"))
        if entry or target:
            seg = ([f"entry {entry}"] if entry else []) + ([f"target {target}"] if target else [])
            lines.append("   " + " → ".join(seg))  # → arrow
        contract = _contract_suffix((ctx or {}).get("option"), account) if ctx else None
        if contract:
            lines.append(f"   \U0001F4C4 {contract}")  # page emoji
    msg = "\n".join(lines)
    if more_count > 0:
        msg += f"\n\n…and {more_count} {more_label} — open the app to see them all."
    return msg


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
        # Only this week's breakouts — a long-lookback scan surfaces months-old
        # patterns that would spam the alert with stale plays.
        hits = [r for r in hits if _within_recent(r)]
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
        # Sort by day (most recent first), then confidence. Cap the shown set —
        # each signal now carries entry/target/contract/sizing, and one giant
        # message trips Telegram's 4096-char limit. Mark ALL fresh as notified
        # (not just the shown N) so the overflow isn't re-tried every run.
        fresh_hits.sort(key=lambda rk: _sort_key(rk[0]), reverse=True)
        shown = fresh_hits[:_MAX_ALERT_SIGNALS]
        more = len(fresh_hits) - len(shown)

        # Enrich the shown signals with the same live price / entry / target /
        # option-contract the Pattern Scanner's Contract panel uses (concurrent,
        # best-effort), plus the account basis for sizing.
        from app.backend.routes.patterns import signal_context

        raw_ctx = await asyncio.gather(
            *[signal_context(str(r.get("ticker")), str(r.get("pattern")), timeframe) for r, _ in shown],
            return_exceptions=True,
        )
        contexts = [c if isinstance(c, dict) else None for c in raw_ctx]
        account = await _account_value()
        items = list(zip([r for r, _ in shown], contexts))

        tf = _TF_LABEL.get(timeframe, timeframe)
        total = len(items) + more
        header = f"Alpha Terminal — {total} high-confidence {tf} signal(s) this week"
        text = render_signal_report(
            items, header=header, account=account, more_count=more, more_label="more this week"
        )
        # Plain text (parse_mode "") — the shared renderer emits no HTML.
        ok = await telegram_notify.send_message(token, chat_id, text, parse_mode="")
        if ok:
            _mark_notified(user_id, [k for _, k in fresh_hits])
            return len(shown)
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
                  timeframes: list[str] | None = None, remote_enabled: bool | None = None) -> dict[str, Any]:
    """Update the current user's non-secret alert rules (partial)."""
    uid = current_user_id()
    cur = _get_settings(uid)
    if enabled is not None:
        cur["enabled"] = bool(enabled)
    if min_confidence is not None:
        cur["min_confidence"] = max(0.0, min(100.0, float(min_confidence)))
    if timeframes is not None:
        cur["timeframes"] = _clean_timeframes(timeframes)
    if remote_enabled is not None:
        cur["remote_enabled"] = bool(remote_enabled)
    saved = _save_settings(uid, cur)
    saved["has_token"] = bool(_get_token(uid))
    return saved


# ─── cross-user helper for the inbound remote poller ──────────────────────────

def all_remote_users() -> list[dict[str, Any]]:
    """Every user with two-way remote control ready to poll: remote_enabled on,
    a paired chat_id, and a stored bot token. Returns
    ``[{user_id, chat_id, token}]``. Best-effort — a user whose token can't be
    read (e.g. missing encryption key) is skipped, never raised.

    This is the ONLY cross-user read in the alerts layer; it powers the single
    in-process poller (``telegram_remote.run_remote_loop``)."""
    out: list[dict[str, Any]] = []
    if use_db():
        with session_scope() as db:
            rows = (
                db.query(UserSettings)
                .filter(
                    UserSettings.telegram_remote_enabled.is_(True),
                    UserSettings.telegram_chat_id.isnot(None),
                )
                .all()
            )
            for r in rows:
                if not r.telegram_chat_id:
                    continue
                try:
                    token = ApiKeyRepository(db, r.user_id).get_decrypted(_TOKEN_PROVIDER)
                except Exception as exc:  # noqa: BLE001 — skip a user we can't decrypt for
                    logger.warning("Remote poll: token read failed for %s: %s", r.user_id, type(exc).__name__)
                    token = None
                if token:
                    out.append({"user_id": r.user_id, "chat_id": str(r.telegram_chat_id), "token": token})
        return out
    settings_store = _read_json(_SETTINGS_PATH)
    secrets_store = _read_json(_SECRETS_PATH)
    for uid, s in settings_store.items():
        if not isinstance(s, dict) or not s.get("remote_enabled") or not s.get("chat_id"):
            continue
        token = secrets_store.get(uid)
        if token:
            out.append({"user_id": uid, "chat_id": str(s["chat_id"]), "token": token})
    return out


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
