"""Two-way Telegram remote control — inbound command poller.

The one-way alert bot (``telegram_alerts`` / ``telegram_notify``) pushes signals
OUT. This module adds the return path: the user texts their own bot and the app
runs it — the agentic research assistant plus a few quick commands (``/scan``,
``/portfolio``, ``/help``) — and replies in Telegram.

Design (and the security posture that matters):

- **Polling, never a webhook.** A single in-process supervisor
  (:func:`run_remote_loop`) long-polls ``getUpdates`` per remote-enabled user. A
  bot cannot have both a webhook and ``getUpdates`` set, so we never set a
  webhook; and two pollers on one bot trip Telegram's 409, so this must run on a
  single replica only.
- **Gated to the paired chat_id ONLY.** :func:`_poll_user` processes a message
  only when ``message.chat.id`` equals the user's paired chat_id; every other
  chat is ignored (but still ACKed via the offset so a stranger can't wedge the
  queue). This is the whole authorization model — the bot obeys exactly one chat.
- **Runs as the owning user.** Each command executes under that user's identity +
  resolved provider keys (mirroring ``prescan_runner._run_for_user``), bound on
  the context vars for the call and reset in ``finally``.
- **No replay on restart.** A user's first poll drops the backlog (see the
  priming step in :func:`run_remote_loop`) so a redeploy doesn't re-run old texts.

Everything is best-effort: a failed send or a bad update is logged and dropped,
never raised into the loop.
"""
from __future__ import annotations

import asyncio
import logging
import os

from app.backend import context
from app.backend.auth import auth_enabled
from app.backend.database.app_models import User
from app.backend.services import agent_chat, key_resolver, telegram_alerts, telegram_notify
from app.backend.services._storage import session_scope, use_db
from src.tools import key_context

logger = logging.getLogger(__name__)

# How long each ``getUpdates`` call blocks server-side waiting for a message.
# Long polling means an idle bot costs one open request per window (not a busy
# loop), and a message that arrives mid-window returns near-instantly.
_LONG_POLL_SECONDS = 25
# Breather between polling passes so a burst of errors can't spin the loop hot.
_PASS_SLEEP_SECONDS = 2.0
# Telegram hard cap on a single outbound message.
_TELEGRAM_MAX_CHARS = 4096

_HELP = (
    "Alpha Terminal remote control\n"
    "\n"
    "Just text me a question and I'll research it with live market data — e.g. "
    "\"what patterns are on my watchlist?\" or \"is NVDA overvalued?\".\n"
    "\n"
    "Quick commands:\n"
    "/scan NVDA AMD — scan tickers for chart patterns\n"
    "/portfolio — your connected brokerage snapshot\n"
    "/help — this message\n"
    "/stop — turn remote control off"
)


# ─── small formatting helpers ─────────────────────────────────────────────────

def _parse_tickers(raw: str, *, cap: int = 15) -> list[str]:
    """Split a comma/space-separated ticker string into a de-duped upper list."""
    out: list[str] = []
    for tok in (raw or "").replace(",", " ").split():
        sym = tok.strip().upper()
        if sym and sym not in out:
            out.append(sym)
    return out[:cap]


def _fmt_money(v: object) -> str | None:
    try:
        return f"${float(v):,.0f}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _fmt_pct(v: object) -> str | None:
    try:
        return f"{float(v):+.1f}%"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _chunk(text: str, limit: int = _TELEGRAM_MAX_CHARS) -> list[str]:
    """Split ``text`` into <=limit pieces, preferring newline boundaries."""
    out: list[str] = []
    s = text or ""
    while len(s) > limit:
        cut = s.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        out.append(s[:cut])
        s = s[cut:].lstrip("\n")
    if s:
        out.append(s)
    return out


async def _send_chunked(token: str, chat_id: str, text: str) -> None:
    """Send ``text`` to ``chat_id``, split across Telegram's 4096-char cap.

    Sent as plain text (no parse_mode) because the agent's reply is arbitrary and
    would otherwise trip Telegram's HTML/Markdown parser on a stray ``<`` or ``_``."""
    for piece in _chunk(text) or [""]:
        if piece:
            await telegram_notify.send_message(token, chat_id, piece, parse_mode="")


# ─── quick commands ───────────────────────────────────────────────────────────

async def _cmd_scan(rest: str) -> str:
    symbols = _parse_tickers(rest, cap=10)
    if not symbols:
        return "Usage: /scan NVDA AMD TSLA — I'll scan those tickers for daily chart patterns."
    from app.backend.routes.patterns import run_pattern_scan, signal_context
    from src.patterns.patterns import PATTERN_DETECTORS
    # Fall back to the pattern's own breakout/target levels when the live plan is
    # unavailable (no chain / off-hours), so entry/target still render.
    from src.patterns.trade_plan import _levels

    results = await run_pattern_scan(symbols, list(PATTERN_DETECTORS), "day", 180)
    if not results:
        return f"No chart patterns found on {', '.join(symbols)} (daily)."

    # Same ordering + enrichment as the alerts: day first, then confidence.
    results.sort(key=lambda r: telegram_alerts._sort_key(r), reverse=True)
    top = results[:8]
    raw_ctx = await asyncio.gather(
        *[signal_context(str(r.get("ticker")), str(r.get("pattern")), "day") for r in top],
        return_exceptions=True,
    )
    items: list[tuple[dict, dict | None]] = []
    for r, ctx in zip(top, raw_ctx):
        ctx = ctx if isinstance(ctx, dict) else None
        if ctx is None or ctx.get("entry") is None:
            brk, _inv, tgt = _levels(str(r.get("pattern") or ""), r.get("key_levels") or {})
            if brk is not None or tgt is not None:
                ctx = {"entry": brk, "target": tgt, "option": (ctx or {}).get("option")}
        items.append((r, ctx))

    account = await telegram_alerts._account_value()
    more = len(results) - len(top)
    header = f"Chart patterns — {', '.join(symbols)} (daily)"
    # Shared renderer → identical formatting to the alert messages.
    return telegram_alerts.render_signal_report(
        items, header=header, account=account, more_count=more, more_label="more by confidence"
    )


async def _cmd_portfolio() -> str:
    from app.backend.services.portfolio_overview import build_overview

    data = await build_overview()
    if not data.get("connected"):
        return "No brokerage account is connected. Connect one in the app to see your portfolio here."
    account = data.get("combined") or (data.get("accounts") or [{}])[0]
    account = account if isinstance(account, dict) else {}
    lines = ["Portfolio:"]
    tv = _fmt_money(account.get("total_value"))
    if tv:
        lines.append(f"Total value: {tv}")
    dc = _fmt_pct(account.get("day_change_pct"))
    if dc:
        lines.append(f"Today: {dc}")
    tg = _fmt_pct(account.get("total_gain_pct"))
    if tg:
        lines.append(f"Total gain: {tg}")
    positions = sorted(
        (p for p in (account.get("positions") or []) if isinstance(p, dict)),
        key=lambda p: p.get("current_value") or 0,
        reverse=True,
    )[:8]
    if positions:
        lines.append("Top positions:")
        for p in positions:
            sym = p.get("symbol") or p.get("underlying") or "?"
            cv = _fmt_money(p.get("current_value")) or "—"
            pct = _fmt_pct(p.get("total_gain_pct"))
            lines.append(f"• {sym} — {cv}" + (f" ({pct})" if pct else ""))
    return "\n".join(lines)


# ─── command dispatch (runs bound to the owning user) ─────────────────────────

def _user_email(user_id: str) -> str | None:
    """Best-effort email lookup for identity binding (only meaningful under db)."""
    if not use_db():
        return None
    try:
        with session_scope() as db:
            u = db.get(User, user_id)
            return u.email if u else None
    except Exception:  # noqa: BLE001 — email is best-effort for key approval
        return None


async def _dispatch(text: str) -> str:
    """Route one already-authorized message (user identity is bound by the caller)."""
    if not text:
        return _HELP
    head, _, rest = text.partition(" ")
    cmd = head.split("@", 1)[0].lower()  # tolerate "/scan@mybot"
    if cmd in ("/help", "/start", "help"):
        return _HELP
    if cmd == "/stop":
        # save_settings targets the bound current user; flips the poller off.
        telegram_alerts.save_settings(remote_enabled=False)
        return "Remote control disabled. Turn it back on in the app (Settings → Telegram alerts) any time."
    if cmd == "/scan":
        return await _cmd_scan(rest)
    if cmd == "/portfolio":
        return await _cmd_portfolio()
    # Anything else is a natural-language question for the agentic assistant.
    return await agent_chat.answer_once([{"role": "user", "content": text}], {"section": "Telegram"})


async def process_text(user_id: str, text: str) -> str:
    """Run one inbound message for ``user_id`` and return the reply text.

    Binds the user's identity (+ resolved provider keys when auth is on) for the
    duration so scans, portfolio, and the agent read that user's data, then resets
    it — mirroring ``prescan_runner._run_for_user``. Never raises: a failure comes
    back as a short user-facing apology so the poller always has something to send."""
    text = (text or "").strip()
    email = _user_email(user_id)
    id_tokens = context.set_current_user_identity(user_id, email, True)
    key_tokens = None
    if auth_enabled():
        massive, finnhub, fds = key_resolver.provider_keys_for_request(user_id, email, True)
        key_tokens = key_context.set_provider_keys(
            massive=massive, finnhub=finnhub, financial_datasets=fds
        )
    try:
        return await _dispatch(text)
    except Exception as exc:  # noqa: BLE001 — always return something sendable
        logger.warning("Remote command failed for %s: %s", user_id, type(exc).__name__)
        return "Something went wrong handling that — try again in a moment."
    finally:
        if key_tokens is not None:
            key_context.reset_provider_keys(key_tokens)
        context.reset_current_user_identity(id_tokens)


# ─── polling ──────────────────────────────────────────────────────────────────

async def _poll_user(user_id: str, token: str, chat_id: str, offset: int | None) -> int | None:
    """Poll one user's bot once and handle any message from their paired chat.

    Processes ONLY updates whose ``message.chat.id`` equals ``chat_id`` (the
    authorization gate); all other chats are ignored. The returned next-offset
    still advances past those ignored updates so an unrelated chat messaging the
    bot can't stall the queue. Returns the unchanged ``offset`` when nothing new
    arrived."""
    updates = await telegram_notify.get_updates(token, offset=offset, timeout=_LONG_POLL_SECONDS)
    if not updates:
        return offset
    max_update_id: int | None = None
    for upd in updates:
        uid = upd.get("update_id")
        if isinstance(uid, int):
            max_update_id = uid if max_update_id is None else max(max_update_id, uid)
        msg = upd.get("message") or {}
        chat = msg.get("chat") or {}
        if str(chat.get("id")) != str(chat_id):
            continue  # not the paired chat — ignore, but still ACK via the offset
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        reply = await process_text(user_id, text)
        await _send_chunked(token, chat_id, reply)
    return offset if max_update_id is None else max_update_id + 1


async def _drain_backlog(token: str) -> int | None:
    """One-shot fetch that computes the next offset PAST the current backlog
    without processing any of it — so a restart doesn't replay old commands.
    Returns the next-offset, or None when there's no backlog to skip."""
    updates = await telegram_notify.get_updates(token, offset=None, timeout=0)
    max_update_id: int | None = None
    for upd in updates:
        uid = upd.get("update_id")
        if isinstance(uid, int):
            max_update_id = uid if max_update_id is None else max(max_update_id, uid)
    return None if max_update_id is None else max_update_id + 1


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def remote_enabled() -> bool:
    """Whether to run the inbound poller. On by default for the db/cloud backend
    (where the multi-tenant owner lives); off for the local file backend so a dev
    server doesn't start a poller. Force on with ``ENABLE_TELEGRAM_REMOTE`` or off
    with ``DISABLE_TELEGRAM_REMOTE`` (the off switch wins — e.g. to run a second
    replica without two pollers hitting Telegram's 409)."""
    if _truthy_env("DISABLE_TELEGRAM_REMOTE"):
        return False
    return use_db() or _truthy_env("ENABLE_TELEGRAM_REMOTE")


async def _run_loop() -> None:
    # Per-user next-offset (in-memory; a restart re-primes from the backlog).
    offsets: dict[str, int | None] = {}
    primed: set[str] = set()
    await asyncio.sleep(30)  # let startup checks + prewarm settle first
    while True:
        try:
            for u in telegram_alerts.all_remote_users():
                uid, token, chat_id = u["user_id"], u["token"], u["chat_id"]
                if not token or not chat_id:
                    continue
                try:
                    if uid not in primed:
                        # First sight of this user: skip whatever's queued so a
                        # redeploy doesn't re-run stale texts.
                        offsets[uid] = await _drain_backlog(token)
                        primed.add(uid)
                        continue
                    offsets[uid] = await _poll_user(uid, token, chat_id, offsets.get(uid))
                except Exception as exc:  # noqa: BLE001 — one user must not stop the rest
                    logger.warning("Remote poll failed for %s: %s", uid, type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 — a bad pass must not kill the loop
            logger.warning("Remote poll pass failed: %s", type(exc).__name__)
        await asyncio.sleep(_PASS_SLEEP_SECONDS)


def run_remote_loop() -> None:
    """Launch the inbound Telegram poller as a background task (gated). No-op when
    disabled or when there's no running loop. Best-effort, mirroring
    ``main._start_internal_cron``."""
    if not remote_enabled():
        return
    try:
        asyncio.get_running_loop().create_task(_run_loop())
        logger.info("Telegram remote control poller enabled.")
    except RuntimeError:
        # No running loop (shouldn't happen inside lifespan) — skip silently.
        pass
