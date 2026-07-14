# Roadmap — planned + in-flight work

Tracked, secret-free specs so any clone (any machine) has the plan. The local
`HANDOFF.md` (gitignored) holds volatile session state — dirty tree, exact env
paths, in-progress notes — but the durable feature specs live here.

Conventions for every item below: follow [CLAUDE.md](../CLAUDE.md), verify locally
(pytest + `tsc --noEmit`; render Alembic migrations as offline Postgres SQL before
pushing), keep it working on iOS/phone width (convention #8), and after a milestone
push to **both** remotes (`origin` public + `cloud` prod), update
[CHANGELOG.md](../CHANGELOG.md), bump the version in `app/backend/main.py`, then poll
prod `GET /health` `.version` to confirm the deploy/migration landed.

---

## NEXT (in flight) — Two-way Telegram remote control

**Goal:** text my Telegram bot from my phone → the backend runs it (agentic assistant
+ quick commands) → replies come back in Telegram. This **extends the existing
outbound alert bot** (`telegram_notify.py` / `telegram_alerts.py`) into a remote.

**Approach:** POLLING, not a webhook (no public-URL / webhook / extra env-var
dependency; uses the existing per-user bot token; fine on the single Railway replica).
**Gate every action to the user's paired `telegram_chat_id` only.** Nothing is
committed for this feature yet.

### Build steps

1. **Model + migration.** Add `user_settings.telegram_remote_enabled` (Boolean,
   `default=False`, `server_default=func.false()`). New Alembic revision,
   `down_revision = "d0e1f2a3b4c5"` (current head), additive. Validate offline SQL.
2. **Settings plumbing.** Thread `remote_enabled` through
   `telegram_alerts.get_settings/save_settings` + `_get_settings/_save_settings`
   (file backend: `alert_settings.json`; db backend: `PortfolioRepository`
   alert-settings on the new column). Add to `GET /alerts/settings` and to
   `SaveSettingsBody` (`PUT /alerts/settings`). Same seam the threshold/timeframes use.
3. **Inbound fetch.** Extend `telegram_notify.get_updates(token, offset=None,
   timeout=0)` to pass `offset` + `timeout` to Telegram `getUpdates`.
4. **Non-streaming agent.** Add `agent_chat.answer_once(messages, context) -> str` —
   run the react agent and concatenate to a single reply (Telegram isn't streaming).
   Reuse `create_agent_chat_model` + `build_agent_tools`.
5. **`telegram_remote.py` service:**
   - `process_text(user_id, text) -> str`: commands `/help`, `/start`, `/stop`
     (disable remote), `/scan SYM…`, `/portfolio`; anything else → `answer_once`.
     **Bind the user's identity + provider keys** for the call, mirroring
     `prescan_runner._run_for_user` (`context.set_current_user_identity` +
     `key_context.set_provider_keys` when `auth_enabled()`), reset in `finally`.
   - `_poll_user(user_id, token, chat_id, offset)`: `get_updates(offset)`; for each
     update where `message.chat.id == chat_id` → `process_text` → `send_message`
     (chunk to ≤4096). **Ignore all other chats.** Return `max(update_id)+1`.
   - `run_remote_loop()`: background supervisor gated like `_start_internal_cron`
     (`use_db()` OR `ENABLE_TELEGRAM_REMOTE`; off with `DISABLE_TELEGRAM_REMOTE`).
     Each pass polls every user with token + chat_id + `remote_enabled`, sleeps
     ~2–3s. In-memory per-user offset; on a user's FIRST poll, **drop the backlog**
     so a restart doesn't replay old commands.
6. **Start it** in `app/backend/main.py` `_lifespan`, next to `_start_internal_cron()`
   (gated).
7. **Frontend.** A "Remote control" toggle in
   `app/frontend/src/components/auth/telegram-alerts-settings.tsx` (only when
   connected) → `alertsApi.saveSettings({ remote_enabled })`; add `remote_enabled` to
   the `AlertSettings` type + `save_settings` body in `alerts-api.ts`. Copy: "Text
   your bot to run scans or ask the assistant." Mobile-friendly.
8. **Tests.** `process_text` command routing (mock `answer_once` + sender); poll
   ignores a non-paired `chat_id`; settings round-trip on both backends.
9. **Verify + ship.** pytest + tsc + a live smoke (text the bot → get a reply). Push
   both remotes, bump version + CHANGELOG, poll prod `/health`.

### Gotchas

- A bot can't have BOTH a webhook and `getUpdates` — we never set a webhook, keep it
  that way.
- Single Railway replica only (two pollers on one bot → Telegram **409**).
- Each inbound message runs the LLM agent (DeepSeek V3) — fine for an owner tool; note
  the cost.
- The pairing flow (`telegram_alerts.pair`) also calls `getUpdates`, which can race the
  poller — pair before enabling remote.
- Security: act ONLY on the paired `chat_id`. The bot token is the user's BYOK secret,
  entered in the app's own form — never typed by the assistant.

---

## Deferred / backlog

- **Intraday backtests** are daily-only — `MassiveClient` has no stock
  intraday-aggregates method (only options). Adding one unlocks 1h/15m
  `run_pattern_backtest`.
- **DeepSeek V3 tool-calling** verified live only for single-tool calls; multi-tool
  chains untested live. Legacy `/sleeves/chat/stream` is the fallback; OpenRouter BYOK
  is the escape hatch to stronger tool-callers.
- **Brokerage-access approval gate** (5-slot SnapTrade cap) — needs a DB migration +
  auth-on testing.
- **Sparkline / diff-highlighting between scans** — needs ≥3 / ≥2 historical scans.
- **Wire `sleeve_attribution.py`** to the upstream backtester output format.
