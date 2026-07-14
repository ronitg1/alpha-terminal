# Changelog

All notable changes to Alpha Terminal are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.22.4] — 2026-07-14

### Changed
- **`/scan` in the Telegram bot now renders identically to the alerts.** Both share
  one plain-text renderer (`render_signal_report`): day-grouped (most recent first),
  sorted by day then confidence, each signal with entry → target, the recommended
  0.40Δ/30-DTE option contract (expiry + R/R), and position sizing. `/scan` keeps its
  own header/footer ("Chart patterns — …", "N more by confidence") but every signal
  line matches an alert. Plain text (no HTML) so an odd ticker can't trip Telegram's
  parser.

## [1.22.3] — 2026-07-14

### Changed
- **Option contract recommendation now targets ~0.40 delta and ~30 DTE.** Previously
  it scanned a 0.40–0.50Δ / 25–30 DTE band and picked the best payoff-per-dollar; now
  it recommends the listed contract **closest to 0.40 delta and 30 days to expiry**
  (normalized delta+DTE distance, delta-based so contracts without a delta are
  skipped). Applies everywhere the recommendation appears — the Pattern Scanner
  Contract panel, `/scan`, and Telegram alerts.

## [1.22.2] — 2026-07-14

### Changed
- **Telegram alerts are now this-week-only, day-sorted, and fully actionable.** In
  response to alert overload: (1) only signals whose breakout is within the last 7
  days are alerted (a 180-day scan no longer pings months-old patterns); (2) signals
  are grouped and sorted by **day first, then confidence**; and (3) each carries the
  **entry, target, recommended option contract (expiry + R/R), and position size** —
  the same trade plan + sizer the Pattern Scanner's Contract panel shows (size = ~1%
  of your connected portfolio value, falling back to $25k, in whole contracts). The
  message stays under Telegram's limit via the top-15 cap + "…and N more this week".

## [1.22.1] — 2026-07-14

### Fixed
- **Telegram alerts silently failed when a scan had many high-confidence signals.**
  The alert built one message containing every hit above the threshold; a broad scan
  (e.g. 3188 signals, hundreds ≥70%) produced a message past Telegram's 4096-char
  limit, so Telegram rejected the whole push ("Bad Request: text is too long") and
  **no alert arrived**. The alert now sends the top 20 most-confident new signals with
  an "…and N more" pointer to the app, and marks all fresh signals notified so the
  overflow isn't retried (and re-failed) every run. This was the real cause of missing
  scheduled-scan alerts.

## [1.22.0] — 2026-07-14

### Added
- **"Run now" for scheduled scans.** Settings → Scheduled scans has a Run now button
  that runs your pre-scan immediately across your scheduled timeframes and fires any
  qualifying Telegram alerts — the exact same path as the automatic runner, so you can
  test alerts (or refresh results) on demand instead of waiting for the next cron tick.
  New `POST /scheduled/run-now` (user-authed) + `prescan_runner.run_now_for_user`. The
  toast reports signals found and alerts sent per timeframe.

## [1.21.2] — 2026-07-14

### Changed
- **`/scan` in the Telegram bot now includes the live stock price and a recommended
  option contract.** Each of the top hits gains the current underlying price, the
  breakout entry + measured-move target, and a suggested contract with expiry
  (type · strike · expiration · DTE · ~mid), reusing the same trade-plan/contract
  logic as the app's Contract button (best-effort; falls back to the pattern's own
  levels if the live lookup fails). Backend-only; re-text `/scan` after deploy.

## [1.21.1] — 2026-07-14

### Fixed
- **`/scan` in the Telegram bot now shows the signal date and a suggested entry.**
  The reply previously listed only ticker · pattern · confidence, which read as
  context-free noise. Each hit now carries a second line — the signal date, the
  suggested entry (the pattern's own breakout trigger, from the same level map the
  in-app trade plan uses), and the measured-move target — plus a one-line reminder
  that the entry is a trigger, not a confirmed fill. Backend-only; re-text `/scan`
  after deploy (no app refresh needed).

## [1.21.0] — 2026-07-14

### Added
- **Two-way Telegram remote control.** The alert bot now takes commands back: text
  your own bot from your phone and the app runs it — the agentic research assistant
  for natural-language questions ("what patterns are on my watchlist?") plus quick
  commands **/scan SYM…**, **/portfolio**, **/help**, and **/stop** — replying in
  Telegram. Extends the existing outbound bot (same BYOK token, same pairing); no
  new secret. **Polling, not a webhook** — a single in-process supervisor
  long-polls `getUpdates` per remote-enabled user (`telegram_remote.py`), wired into
  the app lifespan next to the internal cron and gated the same way (on for the
  db/cloud backend; `ENABLE_TELEGRAM_REMOTE` / `DISABLE_TELEGRAM_REMOTE` to force).
  **Security:** every message is gated to the user's paired `chat_id` only — any
  other chat is ignored (but still ACKed via the offset so a stranger can't wedge
  the queue) — and each command runs bound to the owning user's identity + resolved
  provider keys, reset after. A first-poll backlog drop means a redeploy never
  replays old texts. New non-streaming `agent_chat.answer_once` runs the ReAct agent
  to a single reply; replies chunk to Telegram's 4096-char cap. Toggle it in
  Settings → Telegram alerts (only once paired). Alembic `e1f2a3b4c5d6`
  (additive/nullable `telegram_remote_enabled`). Dual-backend, mobile-friendly UI.
  Single replica only (two pollers on one bot trip Telegram's 409).

## [1.20.0] — 2026-07-14

### Documentation
- **Tracked roadmap** at [`docs/roadmap.md`](docs/roadmap.md) — secret-free feature
  specs (starting with the in-flight **two-way Telegram remote control** plan) so any
  clone on any machine has the plan. The local `HANDOFF.md` stays gitignored for
  volatile session state; durable specs now live in-repo.

### Added
- **Recurring "every N hours" scheduled scans.** Schedules were once-a-day at a set
  time; now each can instead run **Every 1h / 2h / 4h** (from a daily start anchor).
  Pick the frequency in Settings → Scheduled scans. The in-process scheduler already
  ticks every 15 min, so hourly scans "just work"; a new `interval_minutes` +
  `last_run_at` gate the recurrence (`_is_due`) instead of the per-day flag. Pairs
  naturally with hourly Telegram alerts — the dedup keys on the signal's bar, so an
  hourly 1h scan only pings you on genuinely new breakouts. Alembic `d0e1f2a3b4c5`
  (additive/nullable). Dual-backend, mobile-friendly UI.

## [1.19.0] — 2026-07-14

### Added
- **Statistical validation on the pattern + options backtests** — they already
  modeled execution realistically (real option chains + slippage + profit/stop/DTE
  exits); now they carry the same rigor as the Vibe-Trading engine. Both
  `/patterns/backtest` and `/sleeves/backtest/options-strategy` compute, from their
  realized trades, a **walk-forward consistency rate, a Monte-Carlo permutation
  p-value, and a bootstrap Sharpe confidence interval**, plus Sharpe / Sortino /
  Calmar / max-drawdown / profit-factor (`src/backtesting/trade_stats.py`, reusing
  the ported `vibe_engine` validators). The Backtest tab shows a "Statistical
  validation" card with a plain-English verdict ("p=0.03 — unlikely to be luck"),
  mobile-friendly.

## [1.18.1] — 2026-07-14

### Changed
- **Assistant chat now renders markdown + goes full screen.** Now that the agent
  returns rich answers (tables, bold, lists), the chat renders proper markdown
  (GFM tables scroll instead of overflowing) instead of raw `**`/`|` text, and a
  new expand button in the chat header opens it **full screen** on desktop (with a
  comfortable centered reading width and larger text). Verified live: a streamed
  markdown table renders as a real table.

### Added
- **`get_watchlists` + `scan_watchlist` agent tools.** The agent can now read your
  saved watchlists and scan them for patterns directly — previously it had no
  watchlist tool and fell back to your portfolio holdings when asked about "my
  watchlist." 17 agent tools total.

## [1.18.0] — 2026-07-14

### Added
- **Agentic AI assistant (tool-calling).** The chat is now an agent that calls live
  tools to ground its answers instead of a one-shot text model. It can pull quotes,
  scan patterns, fetch win-rates / trade plans, market movers & snapshot, the
  catalyst calendar, ticker news, your portfolio overview & Sharpe stats, 13F
  ownership, valuations — and run backtests. Built on LangGraph's `create_react_agent`
  (already a dependency; **no new deps**). Tool activity streams into the chat as
  small inline "using <tool>" chips; answers stream token-by-token over a typed-SSE
  endpoint (`POST /sleeves/chat/agent/stream`). The old text chat is kept as an
  automatic fallback. The agent loop runs on DeepSeek **V3** (reliable tool-calling);
  a saved R1 preference is auto-swapped to V3 for the loop, and OpenRouter BYOK models
  pass through for stronger tool-callers.
- **Backtesting with statistical validation.** A real event-driven backtest engine
  (ported from the MIT-licensed HKUDS/Vibe-Trading, `src/backtesting/vibe_engine/`,
  attribution in `THIRD_PARTY_NOTICES.md`) turns chart-pattern detections into a
  next-bar-open strategy and reports Sharpe/Sortino/Calmar/max-drawdown/win-rate plus
  **walk-forward consistency, a Monte-Carlo permutation p-value, and a bootstrap Sharpe
  confidence interval**. Exposed to the assistant as `backtest_strategy` (any tickers)
  and `backtest_portfolio` (your held names). Daily bars (intraday pending a Massive
  intraday-aggregates method). Look-ahead-safe (signals fill on the next bar's open;
  proven by a shift-invariance test).
- **"Analyze my portfolio" agent tool** — one call composes holdings + weights +
  sectors, Sharpe/risk stats, 13F ownership changes, and fair-value estimates for your
  top holdings, so the assistant can synthesize a portfolio read on request.
- **First-login tutorial covers alerts** — a new welcome slide walks through Settings →
  Scheduled scans + Settings → Alerts (connect Telegram, set confidence threshold +
  timeframes), and the interactive tour's Settings step now mentions phone alerts.

### Notes
- Verified live: the agent fired `get_quotes` for a price question and answered over
  real data; a 3-ticker/1-year backtest returned 55 trades + full metrics + all three
  validation blocks. 489 tests pass (+24). No new dependencies.

## [1.17.0] — 2026-07-14

### Added
- **Telegram high-confidence alerts.** When a scheduled scan surfaces a signal at
  or above your confidence threshold on an enabled timeframe, the terminal pushes
  it to your phone via Telegram — one batched message per scan (e.g. `NVDA — Bull
  Flag · 93% 🟢`). Configure in **Settings → Alerts**: connect your own bot (create
  it with BotFather, paste the token), pair your chat with a one-time code, then set
  the confidence threshold (default 90%) and which timeframes fire (default Daily +
  1h). A "Send test" button confirms delivery.
  - Rides entirely on the existing scheduled pre-scan runner (`prescan_runner` →
    `telegram_alerts.maybe_notify`), so no new scheduling infra. Fully best-effort:
    a failed send never breaks a scan.
  - A dedup ledger (`notified_signals`, keyed `ticker|pattern|timeframe|end_date`)
    means a recurring 15-min scan won't re-push the same play; a genuinely new
    breakout on a later bar still fires.
  - The bot token is a per-user secret (BYOK): Fernet-encrypted in `api_keys` on the
    cloud/DB backend, and in a gitignored local file on the file backend. The token
    is never returned to the client — settings expose only a `has_token` flag.
  - Outbound push is a ~40-line raw Bot API client over the existing `httpx` (no new
    dependency), honoring Telegram flood-control (429 `retry_after`).
  - New: `user_settings` alert columns + a `notified_signals` table (Alembic
    `c9d0e1f2a3b4`, additive/safe defaults); `/alerts/*` routes; the Alerts tab.

## [1.16.0] — 2026-07-02

### Added
- **Scheduled pre-scans can set their own timeframe + lookback.** Each scheduled scan
  now carries a chart timeframe (Weekly / Daily / 1h / 15m) and a lookback window,
  set in Settings → Scheduled scans, so you can run e.g. a **daily 2yr** premarket
  scan and a **1h 30d** intraday scan on different schedules. The runner uses each
  schedule's own values instead of the old hardcoded daily/180d. Lookbacks are
  clamped to each timeframe's server-side max.
- **Pre-scans are kept per timeframe** so those different-timeframe scans coexist
  instead of overwriting each other. The Pattern Scanner shows the saved pre-scan
  for whichever timeframe you select (and an empty "run a scan" state when a
  timeframe has none); on load it adopts the most recently computed one.

### Changed
- `prescan_results` is re-keyed from `(user_id)` to `(user_id, timeframe)` and
  `scan_schedules` gains `timeframe` + `lookback_days` (Alembic migration
  `b8c9d0e1f2a3`; defaults match the old behavior, so existing schedules keep
  scanning daily/180d). New `PUT /scheduled/schedules/{id}` to edit a schedule's
  timeframe/lookback, and `GET /scheduled/prescan?timeframe=` to fetch a specific
  one. The file backend transparently migrates the old single-slot pre-scan shape.

## [1.15.4] — 2026-07-02

### Added
- **In-process scheduler for scheduled pre-scans — replaces reliance on the external
  GitHub-Actions cron.** GitHub's free scheduled cron is throttled to firing every
  few hours (so a "10 AM" scan could sit unrun until early afternoon) and clips each
  run at a 180s HTTP timeout. The backend now runs `prescan_runner.run_due()` itself
  on a timer (default every 15 min, `INTERNAL_CRON_MINUTES`): timing is exact and a
  scan can take as long as it needs with no HTTP cutoff. It's self-healing — each
  tick runs any schedule whose local time has passed today and hasn't run yet, so a
  restart just catches up on the next tick. Enabled automatically on the DB/cloud
  backend (where the scheduled-scan tenants live) and off for the local file backend;
  override with `ENABLE_INTERNAL_CRON` / `DISABLE_INTERNAL_CRON`. The GitHub-Actions
  cron is kept as a harmless backup — now a fast no-op once the internal run has
  marked the day's schedules done, so it no longer reports timeouts as failures.
  Verified end-to-end: a due schedule ran on the first tick (200 tickers -> 3246
  signals) and its `last_run_on` was marked so it didn't re-run.

## [1.15.3] — 2026-07-02

### Fixed
- **Running a Pattern Scanner scan could leave yesterday's pre-scan on screen.**
  A manual scan only replaced the shown results (and cleared the "Showing your
  pre-scan from …" banner) on *success* — so while a slow scan was still running,
  or after one failed, the previous background pre-scan stayed visible and looked
  like the scan's output. A manual scan now supersedes the pre-scan the instant it
  starts: the banner and stale rows clear immediately, and the results area shows a
  clear "Scanning N names…" state (which persists correctly when you navigate away
  and back). A failed scan now leaves an empty state + error toast, never yesterday's
  data.

## [1.15.2] — 2026-07-02

### Fixed
- **Market catalyst calendar showed no earnings.** Three compounding causes, all fixed:
  1. **Cold-cache timeout.** Earnings are fetched from Finnhub per-symbol and
     sequentially (rate limit), so a cold fetch of the merged watchlist + notable
     set ran ~13-18s — past the route's old 12s cap, which dropped the earnings
     entirely and left only macro events. The calendar looked earnings-empty until
     the cache happened to warm.
  2. **Notable set crowded out by a big watchlist.** The old code merged watchlist +
     notable into one 34-symbol call; a default watchlist like "Market Cap Leaders"
     (139 names) filled all 34 slots, excluding the curated big-prints AND blowing
     the time budget. Earnings are now two independent, time-boxed calls — the
     prewarmed **notable set always renders**, with a bounded slice of the user's
     watchlist added best-effort on top, so a slow watchlist fetch can't drop the
     marquee earnings.
  3. **Opened on a macro-only week.** The week view auto-anchored to the earliest
     catalyst of any kind, which could be a macro event (e.g. a jobs report) in a
     week with no earnings — so even when earnings were present they weren't visible
     by default. It now anchors to the earliest upcoming **earnings**.
- Added a **startup prewarm** of the notable-earnings cache (`main.py`) so a fresh /
  cold backend instance serves earnings on the very first Market load instead of
  timing out.

## [1.15.1] — 2026-07-02

### Changed
- **Pattern Scanner runs its scan in the background.** Kicking off a scan and then
  navigating to another section (or another Screening sub-tab) no longer discards
  the in-flight scan and resets the tab. The scan lifecycle — results, the
  in-progress flag, the win-rate backfill, and the adopted pre-scan — moved out of
  the (unmountable) `PatternsTab` into a new `PatternScanProvider`
  (`app/frontend/src/contexts/pattern-scan-context.tsx`) mounted above
  `MainContent` in `DashboardLayout`, so it survives navigation. When a background
  scan finishes while you're elsewhere, a toast reports the signal count with a
  **View** action that jumps back to the results; returning to the Pattern Scanner
  shows the completed results instead of a blank form. Overlapping scans are
  single-flighted (only the most-recent response lands). (Scope: survives in-app
  navigation with the tab open; a full page reload still loses an in-flight
  on-demand scan — only scheduled pre-scans persist server-side.)

## [1.15.0] — 2026-07-01

### Added
- **Approximate Sharpe ratio on the Portfolio summary.** New `GET /portfolio/stats`
  (`app/backend/services/portfolio_stats.py`): applies the CURRENT stock weights to
  ~1 year of each holding's daily returns (Polygon daily bars, cached 6h per
  symbol), blends them into a portfolio return series, and reports annualized
  Sharpe (rf 4.5%), return, and vol. Constant-weight approximation — trades,
  deposits, options, and cash drag are ignored — and the stat's tooltip says so,
  including what share of the account it covers (stocks only). Cached per user for
  30 minutes; days with under 60% weight coverage are dropped rather than
  zero-filled, and under 60 return days the stat reports "not enough history"
  instead of a noisy number.
- **Approximate Sharpe on the Paper Trading account bar.** `account_snapshot` now
  carries `sharpe`/`sharpe_days`, annualized from the realized (closed-trade)
  equity curve over a weekday grid. Gated behind 5+ trade dates spanning 30+ days —
  below that the UI shows "—" with a "needs more closed-trade history" tooltip.
  Realized-only: open positions' swings don't move it (no daily account marks are
  stored), so it is labeled approximate.

## [1.14.4] — 2026-07-01

### Changed
- **README rewritten for the current app.** Version/tests badges (1.14 / 434), the
  Market dashboard (S&P 500 treemap heatmap, catalyst calendar, news + AI
  thesis-impact), Portfolio via SnapTrade (Summary / Positions / Thesis with the
  valuation football field + 13F ownership tracker), Paper Trading (replaces the
  old "P&L tab with Fidelity CSV import"), universal search, the AI assistant, and
  the local-first vs cloud-profile story. "Signals only — no execution" framing kept.
- **Onboarding screenshots recaptured for every slide.** The capture pipeline
  (`app/frontend/scripts/capture-onboarding.mjs`) was rebuilt: it now captures the
  Market *dashboard*, clicks `Paper Trading` (the old `P&L` click silently no-oped),
  produces the slide files the walkthrough actually references (`07-portfolio.png`
  with the 13F panel, `07b-paper-trading.png`, `08-settings.png`), serves a demo
  book for `/portfolio/overview` (no brokerage on the capture machine), and waits
  out every loading placeholder before shooting.

### Added
- `VITE_CAPTURE_MODE=1` (set by `.env.capture.local` under `--mode capture`)
  renders the auth-only Help/Settings buttons without Clerk so the settings slide
  can be captured against an auth-off dev server.

## [1.14.3] — 2026-07-01

### Changed
- Onboarding walkthrough copy refreshed for the current app: the Market dashboard
  slide (heatmap, macro, catalyst calendar, news thesis-impact), the sidebar
  universal search, and the Portfolio slide (SnapTrade, thesis valuation, 13F).
  *(Entry added retroactively — the commit shipped without one.)*

## [1.14.2] — 2026-07-01

### Changed
- **Removed the standalone "Notable earnings this week" panel; folded it into the
  catalyst calendar.** The calendar's earnings query now merges the watchlist with a
  curated set of notable market-movers, so big prints (AAPL, NFLX, TSLA, JPM…) show
  on the calendar alongside your names and the macro/policy events.
- Thesis valuation still **hides the DCF bar when it lands far from the price**
  (out of the sane window) rather than pinning a misleading bar at the edge.

## [1.14.1] — 2026-07-01

### Removed
- The concentration / risk panel on the Portfolio summary (per request).

## [1.14.0] — 2026-07-01

### Changed
- **Market heatmap rebuilt as a finviz-style treemap.** A proper squarified treemap
  grouped by sector, tile size = market cap, tile colour = performance (red→green,
  capped ±3%), with sector labels. **Defaults to the whole S&P 500** (~117 curated
  constituents enriched with a single bulk snapshot for live perf); a dropdown
  switches to a detailed view of the current watchlist (with the Today/Week/Month
  toggle). Tap a tile to research. Replaces the previous flex sector-grid heatmap.
  New backend `GET /market/sp500-heatmap` + curated constituents dataset.

## [1.13.1] — 2026-07-01

### Fixed
- **Paper Trading option marks match the broker better.** For illiquid / after-hours
  contracts (e.g. SHLS $16C) Polygon publishes no live bid/ask, so the mark fell back
  to the stale last trade ($1.30 vs the broker's $1.25 mid). It now computes a
  theoretical mark from Polygon's own implied vol via Black-Scholes when there's no
  NBBO — IV is by definition the vol that reprices to the mid, so this tracks the
  broker mark (SHLS now ≈$1.29). Liquid contracts still use the real NBBO mid.

### Changed
- Catalyst calendar now **defaults to the Week view**.

## [1.13.0] — 2026-07-01

### Added
- **13F ownership / flow tracker on the Portfolio summary.** For each holding it
  shows which of a curated set of famous funds (Berkshire, Bridgewater, Renaissance,
  Citadel, Pershing Square, Appaloosa, Scion/Burry, Tiger Global) hold it and how
  they moved last quarter — opened / added / trimmed / exited, with the share-count
  change. Live from SEC EDGAR: pulls each fund's two most recent 13F-HR filings,
  parses the holdings, and diffs quarter-over-quarter, matched to your names by
  issuer name (13F reports CUSIPs, not tickers). Cached a day. Verified end-to-end
  (e.g. Berkshire's KO/AAPL stakes, Burry's new NVDA position, Berkshire adding
  GOOGL +224%). Quarterly + ~45-day lagged by nature.

## [1.12.10] — 2026-07-01

### Added
- **"Notable earnings this week" panel on the Market summary.** A curated set of
  market-moving names plus the watchlist, split into Upcoming (with EPS estimate)
  and Reported (beat/miss vs estimate + the post-print price reaction — the first
  session's move). Broader than the watchlist calendar. Backend
  `GET /market/earnings-week`, cached for the week. Shows an empty note in a quiet
  week and fills in during earnings season (beat/miss + reaction logic verified
  against recent prints).

## [1.12.9] — 2026-07-01

### Added
- **News & thesis-impact panel on the Market summary.** Recent headlines filtered to
  the watchlist, each with a Claude one-liner on *what changed and whether it
  supports / threatens / is neutral* to the thesis on that name (using the saved
  thesis as context when available) — instead of a raw feed. All headlines go through
  a single batched LLM call, cached per (ticker-set, day). Backend
  `GET /news/thesis-impact`. Verified: correctly flags competitive/regulatory threats
  and marks irrelevant cross-mentions neutral.

## [1.12.8] — 2026-07-01

### Changed
- **Catalyst calendar shows events inline in each day cell** — no tapping needed.
  Earnings show as the ticker, macro/policy as a short label (Jobs / CPI / FOMC /
  45X / FEOC / ITC), category-colored, with a "+N" overflow when a day is busy.
  Tapping a day still opens its earnings for research.

## [1.12.7] — 2026-07-01

### Added
- **Sector heatmap on the Market summary.** The selected watchlist tiled by sector,
  tile size ≈ market cap, tile colour = performance (red→green), with a Today/Week/
  Month toggle. At-a-glance sector rotation and where the day's action is; tap a
  tile to open that name's research. Backend `GET /market/heatmap` (sector + market
  cap from Finnhub, cached 6h; capped to the top ~24 names, which dominate a
  cap-weighted treemap).

### Changed
- **Catalyst calendar has a Week / Month toggle.** Month shows the full grid; Week
  shows a single 7-day row with the week range, same day markers and tap-to-detail.

## [1.12.6] — 2026-07-01

### Changed
- **Catalyst calendar is now an actual month-grid calendar.** Instead of a
  chronological list, it renders a real month grid with category-colored dot
  markers on each catalyst day (earnings + Fed/CPI/jobs + IRA/45X/FEOC/ITC), prev/
  next month navigation, today highlighted, and it opens on the month of the next
  upcoming catalyst. Tapping a day lists that day's events below (earnings click
  through to research). 7-column grid stays usable on iOS.

## [1.12.5] — 2026-07-01

### Added
- **Concentration & risk panel on the Portfolio summary.** Surfaces what allocation
  alone doesn't: any single name ≥15% of the book (e.g. "NVDA is 22% — single-name
  concentration"), a diversification callout when the top two sectors are ~half the
  book, top-5 concentration, and a largest-positions bar. Sits next to Allocation.

### Changed
- **Thesis valuation now runs a Mini-DCF + exit-multiple comps, not just a P/E.**
  The football field triangulates three methods: a mini-DCF (FCF/share grown and
  discounted at a CAPM cost of equity via the name's beta, + terminal value), an
  exit-multiple comps leg (project EPS 5y forward, apply a normalized terminal P/E,
  discount back), and the 52-week range. Uses the real post-capex FCF (from EV/FCF)
  so capex-heavy names aren't overstated; every band is sanity-clamped and fully
  clamped/degenerate bands are dropped. The thesis prompt is fed the blended fair
  value, so the call is grounded in valuation.

## [1.12.4] — 2026-07-01

### Added
- **Catalyst calendar on the Market summary.** Merges per-ticker earnings (Finnhub)
  with a curated, editable macro/policy calendar — Fed decisions, CPI/PCE/jobs
  prints, and the IRA/45X/FEOC/ITC energy-policy milestones that move these names —
  chronologically grouped by date, with clickable earnings rows. Backend
  `GET /market/catalysts`; earnings are time-boxed so a big watchlist can't stall
  the panel, and the fetch is debounced to avoid an empty-ticker response wiping the
  earnings.

### Fixed
- **Scheduled pre-scan Action now reports why it failed.** It was exiting silently on
  any HTTP error; it now logs the status + body and annotates the specific cause
  (503 = `CRON_SECRET` unset on Railway, 403 = secret mismatch, 000 = unreachable).
  Root cause of the current failures: `CRON_SECRET` is not set on the Railway backend
  (the endpoint returns 503) — set it there (matching the GitHub Actions secret) to
  enable scheduled runs.

## [1.12.3] — 2026-07-01

### Changed
- **Market tab always returns to the summary dashboard.** Clicking Market now
  deselects any open ticker instead of leaving you on the last research card.

## [1.12.2] — 2026-07-01

### Added
- **Universal stock search in the left nav.** A search bar above the Watchlists lets
  you look up any US-listed stock/ETF (Finnhub typeahead, debounced, with keyboard
  arrows/Enter); picking a result opens that ticker's research card in the Market
  tab. Backend: `GET /market/search?q=`.

## [1.12.1] — 2026-07-01

### Fixed
- **iOS: Markets panel + movers no longer overflow.** A market-mover with a very
  long name (microcap ADR descriptions) was set to truncate, but grid/flex items
  default to `min-width: auto`, so the track expanded to the full name instead —
  pushing the whole page wider than the screen and clipping the right column of the
  Markets card. Added `min-w-0` down the chain so names actually truncate, plus
  `overflow-hidden` on both cards so neither can ever push the page wide again.

### Changed
- **Market data (indices + movers) cached ~90s.** It's identical for every user and
  slow to build (crypto/forex spot + Finnhub name warming, ~6–10s cold), so the
  cards used to sit blank. Now the first caller after expiry pays the cost and
  everyone else gets an instant hit (verified 6.7s→3ms / 10.4s→3ms).

## [1.12.0] — 2026-07-01

### Added
- **Market tab redesigned into a watchlist dashboard.** With no ticker selected the
  Market section now shows: a macro panel (indices + commodities incl. gold/silver
  and real-spot BTC/ETH) and market movers; a watchlist selector that defaults to
  the market-cap-leaders list; and Top-performers / Laggards for the selected
  watchlist with a Today / Week / Month toggle (week/month derived from each name's
  sparkline). Tapping any ticker still opens full single-stock research (chart,
  fundamentals, news). The old watchlist/portfolio management moved into a
  collapsible "Manage" section. Mobile-first (convention #8).

## [1.11.22] — 2026-07-01

### Changed
- **Scheduled pre-scan now also warms the portfolio.** On each due schedule the
  background runner already pre-ran the pattern scan; it now also force-refreshes
  the user's portfolio-overview cache (in the same process that serves it), so the
  Portfolio tab is instant at the times you configured — not just the scanner.
  Verified the scheduler picks up due schedules and runs end-to-end. (Still needs
  `CRON_SECRET` set on the host + the GitHub Actions cron to actually fire.)

## [1.11.21] — 2026-07-01

### Added
- **Valuation football field in the thesis.** Each holding's thesis now computes an
  estimated fair value and shows a football-field chart: a growth-justified P/E band
  (a PEG-style multiple scaled to the name's own growth — the "comps" leg) and the
  52-week range, on a shared scale with the current price marked. The thesis prompt
  is fed the blended fair value + upside, so the bull/bear call is grounded in
  valuation instead of hand-waving (verified: KO comes back neutral citing "premium
  valuation and limited upside"). Every band is clamped to a sane window around the
  price and dropped if too wide, so ranges never come out ridiculous. A full FCFF
  DCF isn't offered because this data plan's financial statements are premium-gated
  (403); the field is built from Finnhub's free fundamentals.

## [1.11.20] — 2026-07-01

### Changed
- **News tab rebuilt as three switchable sub-tabs** — Market (general-market news
  with category pills), Watchlist (news across your watchlist + portfolio
  holdings), and Ticker (search any symbol) — instead of a 3-column layout that
  crammed on desktop and became an endless triple-stack on iOS. Same tabs on mobile
  and desktop; content is a single centered readable column.

## [1.11.19] — 2026-07-01

### Changed
- **Portfolio loads instantly now (caching).** The overview was fully rebuilt
  (SnapTrade round-trips + quote/sector/option/52-week enrichment) on every
  navigation, so the tab "loaded forever." Added a per-user server-side cache with
  stale-while-revalidate: a cached copy returns immediately and refreshes in the
  background once it ages past 90s. The client also persists the last overview to
  localStorage and paints it instantly (tab + left nav) while the fetch runs, so
  you never see a blank screen. The Refresh button forces a rebuild
  (`?refresh=true`); disconnecting a brokerage invalidates the cache.

## [1.11.18] — 2026-07-01

### Fixed
- **Earnings calendar is fast again.** The per-symbol Finnhub queries were fired
  concurrently, which tripped the free tier's rate limit and set off a 429 →
  exponential-backoff storm (~16s, some symbols failing entirely) — so the calendar
  looked broken. Reverted to sequential per-symbol calls (which respect Finnhub's
  limiter) and added a per-(symbol, day) cache: the first load pays ~5s once, every
  later Portfolio load is an instant dict lookup (verified 0ms cached).

## [1.11.17] — 2026-07-01

### Changed
- **52-week range visual in Positions.** Redesigned the range bar (Fidelity-style
  low ─ ● ─ high with the traversed portion tinted and a marker whose colour
  tracks position — green near the high, red near the low) and added it to the
  mobile position cards, not just the desktop table. Verified: marker sits at the
  correct % of range with the right colour.
- **Markets card is compact on phones.** Each instrument now shows its value and %
  on one line in a 2-up grid so the 10 indices/commodities don't push your own
  numbers below the fold.
- **Market-movers long names no longer overflow.** Microcap names (e.g. long ADR
  descriptions) now truncate to one line instead of blowing out the card width.

## [1.11.16] — 2026-07-01

### Changed
- **Holding theses are no longer "bullish on everything."** The per-ticker thesis
  prompts (quick + deep) now anchor on a NEUTRAL default with a high bar for
  conviction, require an explicit valuation check and a concrete, company-specific
  bear case, and ask for a calibrated conviction level + what would flip the view.
  The deep memo gains **Valuation** and **Conviction & what would change my mind**
  sections. Verified: AAPL now returns a differentiated bearish read (insider
  selling, valuation) and a leveraged ETF correctly comes back neutral.

## [1.11.15] — 2026-07-01

### Fixed
- **ETF/fund company names now populate in the left nav.** Names came from
  Finnhub's `profile2`, which is empty for most ETFs (VOO, VXUS, SPXL, VIG, AVUV…),
  so those rows showed only a ticker. When Finnhub returns nothing we now fall back
  to Polygon's reference endpoint (scoped to the empties only), which has fund
  names. Stocks were already resolving.

## [1.11.14] — 2026-07-01

### Changed
- **P&L renamed to "Paper Trading"** everywhere (top bar, left nav) to match the
  simulated-account framing.
- **Open/Closed positions reflow into cards on phones.** The positions tables ran
  wider than the screen on iOS, clipping the Unrealized column so you couldn't see
  your P&L. They now render as a table on desktop (md+) and stacked cards below md
  (verified: every field, including unrealized $/%, fits at 390px with no overflow).
  Summary cards drop from 5-across to 2-across on narrow screens.
- Removed the last Fidelity-CSV references from the Paper Trading copy and docs.

## [1.11.13] — 2026-07-01

### Fixed
- **Bitcoin (and Gold/Silver) show real spot prices.** The Markets card used ETF
  proxies (BITO≈$60, GLD≈$240) which badly mispriced crypto and metals. It now
  pulls real spot for Bitcoin/Ethereum (`X:…USD`) and Gold/Silver (`C:XAUUSD`,
  `C:XAGUSD`) from Polygon's crypto/forex aggregates; equity indices keep their
  ETF proxies (which price sanely).
- **Portfolio earnings calendar was never loading.** The backend called
  `earnings_calendar()` with positional args against a keyword-only signature, so
  every call raised `TypeError`, got swallowed, and returned `[]`. It now queries
  per-symbol with keyword args. This also fixes the alias problem: asking Finnhub
  for `GOOG` returns symbol `GOOGL`, which the old whole-calendar filter dropped —
  results are now labeled with the held ticker, so GOOG's earnings show up.

## [1.11.12] — 2026-07-01

### Fixed
- **Market movers no longer show 0% for every name.** Polygon reports
  `todaysChange`/`todaysChangePerc` as 0 when the market is closed or pre-market;
  the movers card now derives the move from the previous close in that case.
- **Market movers show the company name** under each ticker, resolved via the same
  cached quote machinery the left nav uses (best-effort).
- **Portfolio events: earnings calendar is always reachable.** The "Earnings
  calendar" button moved to the card header and no longer hides when there are no
  imminent earnings; the card renders whenever you hold stocks, and the upcoming
  list shows an explicit "no earnings in the next 45 days" line with dates on each
  event.

## [1.11.11] — 2026-07-01

### Fixed
- **Reverted the shared `PortfolioProvider` (v1.11.10).** It fetched
  `/portfolio/overview` once on app mount, which raced Clerk's session init — the
  request went out without a token, 401'd, and never retried, so the Portfolio tab
  showed the "connect a brokerage" empty state and the left nav fell back to manual
  sleeves even though the brokerage was connected. Restored the per-component
  fetches (they self-heal on navigation, after auth is ready). The double-fetch
  optimization will return in an auth-aware form.

## [1.11.9] — 2026-07-01

### Changed (efficiency pass — backend)
- **Portfolio overview enrichment runs concurrently.** The sector, option-price, and
  52-week waves are independent (disjoint fields), so they now run under a single 8s
  budget instead of serializing three timeouts (~3× faster worst case).
- **Shared HTTP client across the option/52-week fan-out** (reuses the connection
  pool) instead of a fresh `MassiveClient`/session per contract.

### Fixed
- **Thesis (and other LLM endpoints) return a clean 402 instead of a 500** when a
  user has no DeepSeek/OpenRouter key — a global `MissingUserKey` handler soft-gates
  to Settings.
- Removed a dead redundant sort in `GET /pnl/positions`.

## [1.11.8] — 2026-07-01

### Removed (dead-code cleanup after the rework)
- Deleted the orphaned old **sleeve-signal Portfolio view** (`portfolio-section.tsx`),
  the **Robinhood pull card** + its frontend api/types (the Portfolio overview keeps
  the Robinhood *backend*), and the now-unused **finnhub-snapshot** component.
- Removed the **Fidelity CSV import** end to end (route `POST /pnl/import/fidelity`,
  `fidelity_import` service, its test, and the frontend method/type) — superseded by
  the SnapTrade brokerage connect.
- Cleaned unused imports across several backend modules.

## [1.11.7] — 2026-07-01

### Fixed
- **Header buttons no longer sit under the top-right account menu** (help / settings /
  profile). The Paper Trading and Portfolio headers now reserve space on the right so
  their controls don't overlap the fixed menu.

## [1.11.6] — 2026-07-01

### Added
- **Thesis sub-tab in the Portfolio tab (M6).** Runs the AI agent/thesis engine on
  each of your holdings — a quick per-name thesis (bias + summary, expandable),
  with a sequential "Run all". Reuses the existing per-ticker thesis endpoint
  (grounded in fundamentals + any saved agent analysis); uses your DeepSeek key.

## [1.11.5] — 2026-07-01

### Changed
- **Onboarding walkthrough updated for the new features (M4).** New slides for the
  **Portfolio** section (connect Fidelity/Robinhood via SnapTrade; Summary +
  Positions; allocation, movers, events, news, markets; account switcher; hide
  amounts) and **Paper Trading** (simulated $100k options account; add from the
  Pattern Scanner). Layout + tour copy updated (P&L → Paper Trading). Screenshot
  recapture still to do — the walkthrough shows a placeholder until then.

## [1.11.4] — 2026-07-01

### Changed
- **"My Portfolios" in the left nav is now driven by your connected accounts (M5).**
  One group per brokerage account, showing that account's underlyings (options
  collapsed), updating with your positions. When no brokerage is connected it falls
  back to the configured sleeves (and links to Portfolio to connect), so the nav is
  never empty. The sleeve config stays as the background scan engine — a full
  backend retirement of manual sleeves is held for review.

## [1.11.3] — 2026-07-01

### Added
- **52-week range now populated** in the Positions table (from a year of daily bars,
  cached, best-effort).
- **Portfolio Events card + earnings calendar (M3).** Summary card flags holdings at
  52-week highs/lows and lists upcoming earnings; a "Full calendar" drill-in shows
  all your holdings' earnings dates with a **weekly ↔ monthly** toggle. New
  `GET /portfolio/earnings` (Finnhub, filtered to your holdings).

## [1.11.2] — 2026-07-01

### Added
- **News card on the Portfolio Summary tab (M7).** Shows news for your holdings
  with a top toggle to switch to **Top market news** — one fetch of the existing
  news feed returns both (holdings headlines + macro). Best-effort; hides if empty.

## [1.11.1] — 2026-07-01

### Added
- **Markets + Market-movers cards on the Portfolio Summary tab (M2).** New
  `GET /market/indices` (S&P 500, Nasdaq, Dow, Russell, Gold, Oil, Bitcoin,
  Treasuries via liquid ETF proxies) and `GET /market/movers` (top gainers/losers
  from Polygon). Both best-effort; the cards hide if data is unavailable.

## [1.11.0] — 2026-07-01

### Changed
- **P&L tab reworked into a Paper Trading simulator.** Removed the Robinhood pull,
  Fidelity CSV import, and SnapTrade connect (brokerage sync lives in the Portfolio
  tab now). It's now a simulated options account: **$100k starting buying power**,
  cash/equity/realized/unrealized derived from your tracked contracts (new
  `GET /pnl/account`), a **Reset** button (`POST /pnl/account/reset`), and an
  account bar showing buying power, positions value, and P&L.
- **"Add to Paper Trading" from the Pattern Scanner.** The trade-plan card now has a
  button that opens the recommended contract as a paper position at its **current
  price** (live during market hours, last close when shut), sized to your risk %.

## [1.10.11] — 2026-07-01

### Fixed
- **Options priced off live market data, not the broker's stale mark.** Some brokers
  (notably Robinhood via SnapTrade) report an option's price as a copy of the buy
  price, which showed a bogus $0 gain. Options are now valued from Polygon's option
  snapshot (last trade / today's close) when available — correct value, total gain,
  and today's change — falling back to the broker mark only when Polygon has no data.

## [1.10.10] — 2026-07-01

### Changed
- **Options read like Fidelity** in the Positions table: the main label is now
  `UNDERLYING STRIKE Call/Put` (e.g. "NVDA 210 Call") with the **expiration date**
  ("Jan-15-2027") on the line beneath, instead of the raw contract symbol.

## [1.10.9] — 2026-07-01

### Fixed
- **Stock names now show under every holding.** Names came from Polygon's
  best-effort company-name cache (often blank); we now use the security
  description SnapTrade already sends (e.g. "NVIDIA CORP"), falling back to the
  quote name only when the broker doesn't supply one.

## [1.10.8] — 2026-07-01

### Changed
- **Top "Cash" figure now includes money-market positions** (SPAXX etc.), not just
  settled cash, so it reflects your true cash-equivalent balance.
- **Position rows are uniform** — every row is the same height with a consistent
  two-line symbol cell and non-wrapping, middle-aligned columns.

### Added
- **SPCX (SpaceX) and other aerospace/defense names** map to an "Aerospace &
  Defense" sector and stay in the Stocks group even when price data is unavailable
  (newly-IPO'd), instead of falling into "Funds & ETFs".

## [1.10.7] — 2026-07-01

### Changed
- **Position subtotals redesigned as a proper aligned total row** (like a brokerage
  "Account total"): on desktop the subtotal's **Today $**, **Total gain/loss $**, and
  **Value** line up under their columns in a bold footer; on mobile it's a clean
  subtotal bar. Section header is now just the name + count.

## [1.10.6] — 2026-07-01

### Fixed
- **Allocation sectors no longer all collapse to "Other."** The per-ticker Finnhub
  lookups could exceed the timeout and get cancelled, wiping every classification
  (including SPAXX→Cash). Now cash, index ETFs, and a **curated ticker→sector map**
  are applied **synchronously first** (so they always land), and Finnhub is used
  only for the unknown tail. Sectors are now **detailed** (Semiconductors, Software
  & Cloud, Internet & Media, …) since you hold a lot of tech.
- **Option "Today $ / %"** now comes from Polygon's option **snapshot** (`day.change`)
  — **live during market hours, last close when the market is shut** — instead of a
  daily bar that can lag intraday.

## [1.10.5] — 2026-07-01

### Fixed
- Portfolio positions are now fully enriched (sector + option day-change) **before**
  account totals are computed, so an account's headline **"Today"** figure includes
  its options' daily change instead of dropping them.

## [1.10.4] — 2026-07-01

### Added
- **Options now show Today $ / Today %.** Computed from Polygon option-contract bars
  (close-to-close, so it reflects the closing price when the market is shut) —
  the underlying's quote can't price an option. Best-effort + cached; kept out of
  the "top movers" card so leveraged option swings don't dominate.

### Changed
- **Allocation groups are collapsible** — click a sector/Cash/Market Index row to
  expand every holding you own in it, with its $ amount and % of the portfolio.
- **Positions split into Stocks / ETFs & Funds / Options**, each with a **subtotal**
  (value + total gain).

### Fixed
- **Option average cost auto-detects per-contract vs per-share.** Brokers disagree
  (Fidelity reports per contract, Robinhood per share); the magnitude vs the
  per-share price now decides, so cost basis and gain are right for both instead of
  being 100× off or showing $0.

## [1.10.3] — 2026-07-01

### Changed
- **Allocation card grouped by sector.** Instead of a flat top-8 list with a
  catch-all "other", holdings are now grouped into **Cash** (SPAXX and other money
  markets), **Market Index** (VOO/VIG/SPXL and other broad ETFs), and **broad
  sectors** (Technology, Health Care, …) with the **top 3 names** shown under each.
  **Options are rolled into their underlying** (NVDA shares + NVDA calls count as
  one NVDA position). Sector comes from Finnhub's industry mapped to broad buckets
  (`portfolio_classify.py`), cached and best-effort (never blocks the response).

## [1.10.2] — 2026-07-01

### Fixed
- **Option cost basis / total gain-loss (the −$1.5M).** SnapTrade returns an
  option's *average cost per contract* (total premium) but its *last price per
  share*. The code multiplied the per-contract cost by 100 again, inflating cost
  basis ~100× (a $17 option showed a $325k basis, −98% "loss"). Average cost is now
  converted to per-share, so cost basis, total gain/loss $, and % are correct.
- **Negative cash.** Cash is a residual (total − invested); when the broker's total
  lagged our quote-marked values it went negative. The account total is now never
  less than the positions shown and cash is floored at zero.

### Changed
- **Positions tab is split into "Stocks & ETFs" and "Options" sections**, each with
  its own count, instead of one mixed list.

### Fixed
- **Portfolio total gain/loss.** No longer trusts SnapTrade's `open_pnl` (which it
  reports as absurd values for options, e.g. a −$1.4M total). Gain/loss is now
  always computed as today's value − cost basis (avg cost × qty, ×100 for option
  contracts); null when average cost is unavailable so the UI shows "—" not a bogus
  number.
- **Portfolio cash figure.** The account's *total balance* was being mistaken for
  cash (hugely inflated). Cash is now the residual: total balance − invested.

### Added
- **Add another brokerage** from the Portfolio tab — an "Add account" button opens
  the SnapTrade portal to link any supported institution (not just Fidelity), on
  top of an existing connection.
- **Hide amounts (privacy).** An eye toggle in the Portfolio header masks all
  dollar values (`••••••`) so balances aren't visible to someone glancing at the
  screen; percentages stay visible. Preference persists per browser.

## [1.10.0] — 2026-07-01

### Added
- **SnapTrade → Fidelity read-only integration (Phase A).** Users connect a
  brokerage through SnapTrade's hosted portal (credentials never touch the app)
  and see stock + option positions. New signed `httpx` client (no SDK) with the
  validated HMAC request signing; per-user SnapTrade `user_secret` stored
  **encrypted at rest** (Fernet) in a new `snaptrade_connections` table (migration
  `a7b8c9d0e1f2`) / local JSON. Routes `GET /snaptrade/status`, `POST
  /snaptrade/connect`, `GET /snaptrade/portfolio`, `DELETE /snaptrade/connection`,
  double-gated: dormant unless `SNAPTRADE_CLIENT_ID`/`SNAPTRADE_CONSUMER_KEY` are
  set, and limited to owner/approved users (free-tier cost control). Frontend
  connect card in the Portfolio tab. **Requires** `SNAPTRADE_CLIENT_ID` +
  `SNAPTRADE_CONSUMER_KEY` on Railway (commercial keys) for production use.
- **Unified Portfolio tab (M1).** The Portfolio nav tab is now a two-view
  (**Summary** / **Positions**) experience with an **account switcher** and an
  **"All accounts" combined** view. New `GET /portfolio/overview` merges every
  connected brokerage (SnapTrade + Robinhood), enriches holdings with live quotes,
  and computes value, day change, total gain/loss, % of account, and cost basis.
  Summary shows totals + allocation + top/bottom movers; Positions is a full metric
  grid that reflows to cards on iOS. Empty state prompts to connect a brokerage.
  (Markets/market-movers cards, 52-week range, and the earnings calendar land in
  follow-up milestones.)

### Changed
- **Settings dialog is now tabbed** — **API keys · Scheduled scans · Access** —
  and height-capped with a scrolling body + fixed header, so Scheduled scans is
  reachable instead of buried. Works on iOS (`dvh`, no horizontal overflow).
- **Access-request management reworked (owner).** Two boxes: **Shared-key users**
  (approved accounts, each removable to revoke access) and **Outstanding requests**
  (pending). Approve moves a user up; deny/remove **deletes** the row entirely (no
  "denied" limbo). The seed `default` account is hidden. New owner-only `DELETE
  /access/requests/{id}`.

### Conventions
- **CLAUDE.md convention #8:** every UI change must work on iOS/mobile, not just
  desktop (hard requirement).

## [1.9.0] — 2026-07-01

### Added (collaborator PRs, reviewed + integrated)
- **OpenRouter BYOK + per-user model selection** (PR #1, @mehulrao). Users can add
  their own OpenRouter key and pick the LLM model (DeepSeek vs any OpenRouter
  model) in Settings; scans/theses/chat/news/transcripts route through the choice,
  DeepSeek stays the default. No shared env fallback for OpenRouter (usage-billed);
  explicit `api_keys` dicts remain authoritative (security invariant preserved).
  Its Alembic migration was re-chained (id `e5f6a7b8c9d0` collided with the v1.8.0
  scan_schedules migration) to `f6a7b8c9d0e1` onto the current head.
- **Robinhood MCP read-only portfolio pull** (PR #2, @mehulrao). Users save their
  own Robinhood MCP token (BYOK, encrypted); a P&L card pulls a read-only holdings
  snapshot via `GET /robinhood/portfolio`. No shared fallback with auth on, HTTPS
  pinned to `agent.robinhood.com`, read-only tool allow-list (trading/order/
  transfer names blocked with tests), token + account fields redacted.

Both integrated onto current main with conflicts resolved so the two providers
coexist. 390 tests pass; frontend builds.

## [1.8.1] — 2026-06-30

### Fixed
- **Pattern backtest getting "stuck" on a ticker (e.g. INTC) in optimize mode.**
  A signal-dense, heavily-optioned name fanned out into hundreds of real-option
  fetches priced in a single silent `await`, so the SSE connection idled and a
  proxy/browser dropped it — the run appeared frozen on that ticker. Now the
  backtest heartbeats progress every ~5s while pricing (keeping the stream alive
  and honoring disconnects), isolates per-ticker pricing failures (one bad name
  can't abort the run), and caps signals per ticker at 40. The frontend adds a
  90s stall watchdog that surfaces a clear message instead of hanging. Verified:
  an optimize + real-pricing run over INTC/AAPL/NVDA streams through and completes.

## [1.8.0] — 2026-06-30

### Added
- **Scheduled background pre-scans.** Users can set times in Settings ("Scheduled
  scans") for their Pattern Scanner to run automatically in the background, so
  results are ready and instant when they open the tab. Times are stored in the
  user's local timezone; the scanner adopts the latest pre-scan on open with a
  "Showing your pre-scan from …" banner (a manual scan still refreshes live).
  - New tables `scan_schedules` + `prescan_results` (Alembic `e5f6a7b8c9d0`),
    dual file/DB service (`scan_schedule_service`), repository, and a reusable
    `run_pattern_scan()` core shared by the live route and the scheduler.
  - New routes under `/scheduled/*`: user CRUD for times + `GET /scheduled/prescan`
    (auth-scoped), and `POST /scheduled/run-due` guarded by a shared `CRON_SECRET`.
  - The scheduler is a **free GitHub Actions cron** (`.github/workflows/prescan-cron.yml`)
    that pings `/scheduled/run-due` every ~15 min; the app runs whichever users'
    schedules are due (once-per-day dedupe via `last_run_on`). Gated to fire only
    from the production repo. Per-user market-data keys are bound for each
    background scan when auth is on. +4 tests (dual-backend store + due logic).

## [1.7.7] — 2026-06-30

### Changed
- **Longer cache for daily/weekly price data (faster repeat scans).** Pattern-scan
  candle data was cached for only 15 minutes regardless of bar size; daily/weekly
  bars now cache for 1 hour (intraday stays short). The first scan of a ticker
  warms it for every user until expiry, cutting repeat-scan latency on the shared
  production server. (A cross-user Redis cache is the planned next step.)

## [1.7.6] — 2026-06-30

### Fixed
- **iOS "Add to Home Screen" (standalone) rendering.** Changed the standalone
  status-bar style from `black-translucent` (which renders content *behind* the
  status bar and looked cramped in the home-screen app) to `black`, which
  reserves an opaque status bar above the content — better for the dark UI.
  Plain Safari was already fine; this only affects the installed home-screen app.
  Note: iOS snapshots the web-app meta tags at install time, so the icon must be
  removed and re-added to pick up the change.

## [1.7.5] — 2026-06-30

### Fixed
- **Pattern chart modal on mobile.** Tapping a result opened a modal that laid
  the chart and the 320px analysis panel side-by-side, crushing the chart to a
  sliver on a phone. The modal is now full-screen on phones with the chart on top
  (full-width canvas) and the analysis panel stacked below; desktop is unchanged.
- **Options chain table overflow on mobile.** The calls/puts chain table pushed
  past the screen edge (a flex/grid `min-width:auto` ancestor wouldn't shrink).
  Added `min-w-0` on the chain grid/items and wrapped the table in a horizontal
  scroll container, so it scrolls in place within its card instead of overflowing.
- Audited every tab at 375–393px: Market, Screening (Scanner/Options/Backtest),
  Portfolio, P&L, News, Calls all have zero uncontained horizontal overflow.

## [1.7.4] — 2026-06-30

### Changed
- **Pattern Scanner results redesigned for mobile.** On phones the results now
  render as a clean stacked **card list** (ticker, date + freshness, confidence,
  pattern pill, win-rate, description, full-width "View contract") grouped by
  Today/Yesterday — instead of a cramped, overflowing data table. The table is
  unchanged on desktop (md+). The filter bar (Sort/When/Min-conf/Bias chips) now
  stacks full-width and wraps on phones instead of running off the right edge,
  and the header ticker/pattern filters share the row width. The Screening
  sub-tabs scroll horizontally rather than wrapping. Verified zero overflow at
  375px with 106 results rendered.

## [1.7.3] — 2026-06-30

### Fixed
- **Pattern Scanner results were invisible on mobile.** In the stacked
  single-column mobile layout the results card had no fixed height, so its
  `flex-1` scroll area collapsed to 0px and rows (and the empty-state message)
  never showed. The results list now takes natural height on phones and the page
  scrolls; desktop's internal-scroll behavior is unchanged. Verified: a 6-ticker
  scan renders its rows on a 375px viewport.

## [1.7.2] — 2026-06-30

### Added
- **Mobile / iOS support.** The dashboard is now usable on phones. Desktop keeps
  the exact 3-pane layout (md+); on small screens the left nav collapses to a
  slide-in drawer (☰ in a new thin top bar), the AI chat panel becomes a
  full-screen overlay, and the center content goes full-width.
  - iOS specifics: `viewport-fit=cover` + safe-area insets (notch / home
    indicator), `100dvh` height so the Safari toolbar never hides content,
    no tap-highlight flash, locked text-size-adjust, web-app-capable meta.
  - Fixed mobile horizontal-overflow: the Pattern Scanner's fixed `320px+content`
    grid now stacks; portfolio rows trim the allocation % / shrink the price
    column on phones. Verified zero horizontal overflow at 375px across Market,
    Screening, Portfolio, and P&L.
  - Page title / home-screen name set to "Alpha Terminal".

## [1.7.1] — 2026-06-29

### Fixed
- **Provider API keys are trimmed before use.** A stray leading/trailing space in
  an env var (easy to introduce when pasting a secret into a hosting dashboard)
  was sent verbatim and 401'd, silently hiding data — e.g. the Market tab's whole
  "Financials & analyst data" (Finnhub) card vanishing. `key_context` now strips
  Massive/Finnhub/FDS keys at the single point every client reads through. +1 test.
  - Note: this is defense-in-depth; the card disappears entirely when the key is
    simply *unset* in the environment — set `FINNHUB_API_KEY` in the deploy env.

## [1.7.0] — 2026-06-29

### Added
- **Onboarding flag is now per-account (server-side).** The first-login
  walkthrough's "seen" state moved from browser localStorage to a per-user
  `user_settings.onboarding_completed` column, so it shows exactly once per
  account — surviving a browser/localStorage clear or a new device. `GET /auth/me`
  now returns `onboarding_completed`; `POST /auth/onboarding-complete` records it.
  localStorage is kept as a fast-path cache and offline fallback.
  - New: Alembic migration `d4e5f6a7b8c9_add_onboarding_completed`,
    `user_settings_service.py` (dual file/DB backend), `auth-api.ts` (frontend).
  - Threaded through both storage backends + provisioning seed; `+2` cutover tests.

### Fixed
- **Sidebar prices/sparklines now populate for every visible ticker.** The left
  rail requested quotes for *all* tickers across every watchlist (hundreds) in one
  call, blowing past the backend's 150-per-request cap so anything past the first
  150 stayed blank. Now it fetches only the tickers in expanded groups, and the
  API client chunks requests at 150 as a safety net.

### Changed
- Onboarding walkthrough slide 4 (Pattern Scanner) now shows a real scan with
  detected patterns instead of the empty form (capture script runs a small scan).

## [1.6.9] — 2026-06-29

### Added
- **First-login onboarding walkthrough.** New users see a one-time welcome popup
  on first login: an 8-slide carousel (overview, Market, Pattern Scanner,
  Options/Backtest, AI assistant, Portfolio/P&L, and API-key setup) with real
  screenshots, plus an optional interactive `driver.js` tour that spotlights the
  live nav. Skippable on every step; auto-shows only once (per-user localStorage
  flag `alpha-onboarding-v1:<userId>`); replayable anytime via a Help ("?")
  button in the top-right account controls.
  - New: `app/frontend/src/components/onboarding/` (`welcome-dialog.tsx`,
    `use-onboarding.tsx`, `onboarding-steps.tsx`); screenshots in
    `app/frontend/public/onboarding/`.
  - `data-tour` attributes added to `left-nav.tsx` / `user-menu.tsx` for the tour.
  - New dep: `driver.js`. Dormant when `VITE_AUTH_ENABLED` is off.
- **Screenshot re-capture pipeline.** `npm run capture:onboarding`
  (`scripts/capture-onboarding.mjs`, puppeteer-core) regenerates the walkthrough
  images headlessly against an auth-off dev server. `.gitignore` now un-ignores
  `app/frontend/public/onboarding/*.png` (global `*.png` rule).

## [1.6.8] — 2026-06-29

### Fixed
- **Finnhub stock-detail data always shows for all signed-in users.** Finnhub is
  free-tier public data; the approval gate (which controls the paid Massive
  subscription) no longer also blocks Finnhub. Every authenticated user now gets
  the shared Finnhub key via the request-scoped context, so the "Financials &
  analyst data" panel renders for any stock clicked in the sidebar.
- **"Using shared key" indicator in API-keys Settings.** Massive and Finnhub now
  show a "Using shared key" badge instead of "Not set" when the user is approved
  to use the owner's shared keys (or is the owner). Gives users clear confirmation
  that they're covered without having to add their own key.
- **HANDOFF.md** refreshed to reflect Phase 3 completion and v1.6.8 state.

## [1.6.7] — 2026-06-29

### Added (Phase 3 — self-service shared-access requests; DORMANT behind AUTH_ENABLED)
- **"Request free access" flow.** In the API-keys Settings dialog, a user without
  their own market-data keys can request free use of the owner's shared keys; the
  owner sees pending requests in the same dialog and approves/denies them. An
  approved request grants that (verified) email shared-key access, on top of the
  static `SHARED_DATA_EMAILS` env allowlist — so the owner manages access in-app
  instead of editing env vars.
- **Backend:** new `access_requests` table (migration `c3d4e5f6a7b8`),
  `AccessRequestRepository`, and `/access` routes (`GET /access/me`,
  `POST /access/request`, owner-only `GET /access/requests` +
  `POST /access/requests/{id}/{approve|deny}`). `key_resolver.is_shared_data_approved`
  now also consults DB grants; new `is_owner()` gates the owner routes.
- **Tests:** +7 (repo upsert/approve/case-insensitive/re-request, DB-grant →
  shared-approved, is_owner, request→owner-approve→approved round-trip, owner
  flags, auth required). Suite **351 passing**. All dormant when auth is off.

## [1.6.6] — 2026-06-29

### Added (Phase 3 — shared market-data key allowlist; DORMANT behind AUTH_ENABLED)
- **Approved-emails allowlist for the owner's shared market-data keys.** When auth
  is on: the owner plus any **verified** email in `SHARED_DATA_EMAILS` use the
  owner's shared Massive/Finnhub/FDS keys; every other account must bring their
  own Massive/Finnhub key (or market data/news won't load, and a scan soft-gates
  with "add your Massive key"). DeepSeek stays per-user for everyone. An
  unverified email never qualifies (anti-spoof).
- **Market-data clients are now per-request keyed.** New dependency-free
  `src/tools/key_context.py` binds the resolved Massive/Finnhub/FDS key for the
  request (set once by the middleware, propagated into the scan worker thread);
  the Massive/Finnhub clients, `src/tools/api.py` provider routing + FDS fallback,
  and `pattern_data.py` all read it instead of `os.environ`. Dormant when auth is
  off or under the file backend — the clients fall back to the shared env keys,
  unchanged.
- **Fixed two key-leak paths** (found in security review, would have let a
  non-approved user spend the owner's keys once auth is on): the Finnhub client
  was a process-wide singleton that pinned the first caller's key — now built
  per-request; and `api.py`'s FDS fallback + routing read `os.environ` directly,
  bypassing the allowlist — now routed through the request key context.
- **Tests:** +allowlist/approval cases, `provider_keys_for_request`, key-context
  binding, Finnhub per-request + bound-empty-blocks-shared-key. Suite **344
  passing**. Architect security-reviewed; both blockers fixed.
- **New env:** `SHARED_DATA_EMAILS` (see DEPLOY.md).

## [1.6.5] — 2026-06-28

### Added (Phase 3 — auth, step 6 of 7; DORMANT behind VITE_AUTH_ENABLED)
- **Frontend login + BYOK settings (Clerk).** Added `@clerk/clerk-react`. When
  `VITE_AUTH_ENABLED` is on (and `VITE_CLERK_PUBLISHABLE_KEY` is set), the app
  shows a Clerk sign-in gate (Email + Google), a top-right account menu, and an
  **API keys** settings dialog to add/replace/remove your own DeepSeek (required),
  Massive, and Finnhub keys — key values are write-only and never shown again.
- **Token attachment.** A single `window.fetch` wrapper attaches the Clerk
  session token to every backend call, covering the regular API clients AND the
  fetch-based SSE streams (morning scan, chat). It is FormData-safe (won't corrupt
  uploads), retries once on a transient null token, and only attaches to the
  absolute backend origin (no third-party leak).
- **Fully dormant when off.** With `VITE_AUTH_ENABLED` unset (the default), no
  Clerk provider/gate/menu renders and `window.fetch` is untouched — the app is
  byte-for-byte the current dashboard. Verified: dormant renders the full
  dashboard, auth-on renders the Clerk gate, `tsc` + `npm run build` pass.
- **Backend:** owner-claim now accepts `email_verified` as the boolean `true` or
  the string `"true"` (Clerk emits the latter). Suite **335 passing**.
- Architect-reviewed (token threading, multipart, dormant safety); code fixes
  applied. Live OAuth round-trip + a Clerk production instance are the Step 7
  runtime items.

## [1.6.4] — 2026-06-28

### Added (Phase 3 — auth, step 5 of 7; DORMANT behind AUTH_ENABLED)
- **First-login provisioning + owner data-claim.** New
  `app/backend/services/provisioning.py`: the first time an authenticated user is
  seen, their `users` row is created and they either **claim** the existing
  `default`-owned data (portfolios, watchlists, settings, theses, P&L, scans, API
  keys) or get a **generic starter portfolio**. Runs from `get_current_user_id`
  only under the DB backend; idempotent (in-process cache + row-existence guard);
  never breaks a request on failure.
- **Secure owner match.** The claim fires only for the configured owner, by either
  `OWNER_USER_ID` (the Clerk `sub` — unspoofable, preferred) or `OWNER_EMAIL`
  matched against a **verified** email (`email_verified` true in the token). An
  unverified or attacker-supplied email can never claim the owner's data — closes
  the open-signup takeover/key-theft vector the security review flagged. The claim
  is inherently one-time (only the owner's `sub` triggers it, only on first login).
- **Tests:** +9 (starter seeding, verified-email claim, unverified-email refused,
  `OWNER_USER_ID` claim, non-owner, idempotency, end-to-end claim on first gated
  request, auth-off no provisioning). Suite **334 passing**. Architect
  security-reviewed; the blocker (unverified-email claim) fixed here.

## [1.6.3] — 2026-06-28

### Added (Phase 3 — auth, step 4 of 7; DORMANT behind AUTH_ENABLED)
- **Per-user key resolver wired into the LLM + data paths.** New
  `app/backend/services/key_resolver.py` resolves each provider's key as
  *this user's stored key → else the shared env key*, gated by policy: **DeepSeek
  requires the user's own key** (no shared fallback when auth is on); **Massive +
  Finnhub fall back to the shared owner env keys**. When auth is off the resolver
  returns the env key with no DB hit — fully dormant.
- **Scan uses the caller's DeepSeek key end-to-end.** `run_sleeve`/`run_scan` take
  an `api_keys` override that rides into the agent state and through `call_llm`;
  the SSE scan routes resolve the user's key in-request and pass it into the
  worker thread. If auth is on and the user hasn't added a DeepSeek key, the scan
  soft-gates with a clear "add your key in Settings" message (the first-LLM-use
  prompt). Thesis/news/transcript/chat LLM calls use the resolver too.
- **Owner-budget leak closed.** `get_model` no longer falls back to the shared
  `DEEPSEEK_API_KEY` env var when an explicit key dict is supplied — so a logged-in
  user without their own key can never silently spend the owner's DeepSeek budget
  (it fails closed). The legacy hedge-fund + backtest paths now build their key
  dict through the resolver. LLM error logging no longer echoes exception text
  (which could embed a key).
- **Tests:** +11 (resolver env/user/policy/isolation, `resolved_api_keys`
  fail-closed, the `get_model` dict-authoritative guard, scan key injection into
  agent state + `call_llm`). Suite **325 passing**. Architect security-reviewed;
  both blockers (the env-fallback leak and the backtest soft-gate) fixed here.

## [1.6.2] — 2026-06-28

### Added (Phase 3 — auth, step 3 of 7; DORMANT behind AUTH_ENABLED)
- **BYOK API-key storage — user-scoped + encrypted at rest.** The legacy
  single-tenant, plaintext `api_keys` table is now per-user (`user_id` column,
  `UNIQUE(user_id, provider)`) with values **encrypted** via Fernet
  (`app/backend/crypto.py`, keyed by `API_KEY_ENCRYPTION_KEY`, comma-separated for
  rotation). New Alembic migration `b2c3d4e5f6a7` reshapes the table (with a guard
  that halts the deploy rather than dropping a non-empty table).
- **`/api-keys` routes rebuilt** around the authenticated user: POST (upsert),
  GET list, GET `{provider}`, DELETE — all scoped to the caller. Keys are
  **validated with a live provider call before saving** (DeepSeek / Massive /
  Finnhub), and the plaintext key value is **never returned** to the client
  (responses are metadata-only). Unknown provider → 400; provider rejects the key
  → 400; provider unreachable/rate-limited → 503 (so an outage never mislabels a
  valid key); key over 512 chars → 422.
- **Boot guard:** the backend refuses to start if `AUTH_ENABLED` is on but
  `API_KEY_ENCRYPTION_KEY` is unset — a loud deploy-time failure instead of a
  per-request 500. Dormant when auth is off.
- **Dependencies:** `cryptography` (added in 1.6.0) now also backs at-rest key
  encryption.
- **Tests:** +26 (encryption round-trip/rotation/failure, encrypted-at-rest +
  per-user repo isolation, validate-on-save status classification, 503-on-outage,
  never-return-value, oversized-key 422, boot guard). Suite **314 passing**.
  Architect security-reviewed; both blockers (provider-outage-vs-bad-key, and the
  missing encryption-key boot guard) and the should-fixes are addressed here.

## [1.6.1] — 2026-06-27

### Added (Phase 3 — auth, step 2 of 7; DORMANT behind AUTH_ENABLED)
- **Per-user data isolation.** The owning user for the current request is now
  carried in a request-scoped context var (`app/backend/context.py`), set once at
  the request edge by a new pure-ASGI `UserContextMiddleware`
  (`app/backend/middleware.py`) and read by every storage service. The 6 file/DB
  services and the 5 scan-result repository call sites now scope to
  `current_user_id()` instead of the hardcoded `default` user. With auth **on**,
  two different Clerk tokens get two completely separate datasets; with auth
  **off** the context var defaults to the `default` user, so behavior is
  unchanged. The binding propagates into the SSE `StreamingResponse` body and the
  morning-scan `asyncio.to_thread` worker (both verified by tests).
- **Login enforcement at the router level.** The data routers (sleeves, patterns,
  news, transcripts, pnl) and the legacy data/key routers (hedge-fund, flows,
  flow-runs, api-keys) now require an authenticated user via a router-level
  dependency. Dormant when auth is off (resolves the default user, no 401); when
  on, an unauthenticated request gets a 401 before the handler runs.
- **Scope-aware scan progress.** `src/utils/progress.py` dispatch is now confined
  to the active scan's scope, so two users scanning concurrently no longer receive
  each other's live progress events (a cross-user leak the process-wide singleton
  previously allowed). The default `None` scope preserves CLI behavior.
- **An `UNAUTHENTICATED_USER_ID` sentinel** is bound for unauthenticated requests
  when auth is on, so any not-yet-gated route reads an empty dataset rather than
  the owner's — defense in depth.
- **Tests:** +12 (per-user isolation through real CRUD routes under the DB
  backend, context propagation through SSE + `to_thread`, concurrent-request
  non-bleed, scan-persistence scoping, and scope-aware progress dispatch). Suite
  is **288 passing**. Architect-reviewed (no blockers; the progress-singleton
  leak and router-gating gaps it flagged are fixed here).

### Known follow-ups
- `flows`, `flow_runs`, and `api_keys` still use the legacy single-tenant tables
  (no `user_id`); they are gated but globally shared until step 3 user-scopes
  `api_keys` (and encrypts it).
- The legacy `hedge_fund` SSE path still uses the unscoped (`None`) progress
  scope; two concurrent hedge-fund runs could cross-deliver progress (the
  dashboard's scan path is fixed).

## [1.6.0] — 2026-06-27

### Added (Phase 3 — auth, step 1 of 7; DORMANT behind a flag, nothing changes until flipped)
- **Backend authentication seam behind `AUTH_ENABLED` (default off).** New
  `app/backend/auth.py` adds a FastAPI dependency, `get_current_user_id`, that
  resolves the user for a request. With the flag **off** (every install today,
  local and the current cloud deploy) it yields the existing `default` user — no
  behavior change, safe to ship. With the flag **on** it requires a valid Clerk
  session JWT in the `Authorization: Bearer …` header, verifies the RS256
  signature against Clerk's published keys (JWKS), checks expiry (with a small
  clock-skew leeway), optionally pins the issuer (`CLERK_ISSUER`), and returns
  the Clerk user id. Missing/invalid/expired tokens return **401**; a server
  with auth on but no Clerk keys configured returns **500** (a loud deploy-time
  misconfiguration, not a silent bad-token). This mirrors the Phase 2
  `STORAGE_BACKEND` flag discipline: build dormant, push safely, flip in cloud.
- **`GET /auth/me`** — returns the resolved `user_id` and whether auth is
  enforced. The first consumer of the dependency and the frontend's future
  token-check endpoint.
- **Config (env):** `AUTH_ENABLED`, `CLERK_JWKS_URL` (or derived from
  `CLERK_ISSUER` as `<issuer>/.well-known/jwks.json`), `CLERK_ISSUER`.
- **Dependencies:** added `PyJWT[crypto]` and `cryptography` (the latter also
  for Phase 3 step 3's at-rest key encryption).
- **Tests:** 19 new auth tests covering both flag states and the real RS256
  verification path (generated keypair, in-memory JWKS, `kid` matching, expiry,
  leeway, wrong-signature, wrong-issuer, missing-subject, JWKS caching). Suite
  is **276 passing**.

## [1.5.2] — 2026-06-27

### Fixed
- **Frontend cloud build (Vercel).** Removed a stale `app/frontend/pnpm-lock.yaml`
  left over from an earlier pnpm setup. The project uses npm (`package-lock.json`),
  but Vercel auto-detected the pnpm lockfile and ran `pnpm install` with a frozen,
  out-of-date lockfile — failing the build. Deleting it lets Vercel use npm. No
  effect on local development.

## [1.5.1] — 2026-06-27

### Fixed
- **Railway start command now binds to the host port.** `railway.toml`'s
  `startCommand` passed `--port $PORT` without a shell, so Railway handed uvicorn
  the literal string `$PORT` ("not a valid integer") and the app never bound —
  failing the deploy healthcheck. Wrapped it in `sh -c` (matching the
  `preDeployCommand`) so `$PORT` expands. Surfaced on the first real cloud deploy.

## [1.5.0] — 2026-06-27

### Changed (Phase 2 database cutover — COMPLETE; dormant, local behavior unchanged)
- **Scan engine takes the portfolio config as a parameter.** New
  `run_scan(sleeves, end_date, ...)` in `src/run_morning_scan.py` runs the scan
  over a portfolio config passed in by the caller, instead of reaching for the
  module-global `PORTFOLIO_SLEEVES`. The CLI still passes the local global as its
  default, so `poetry run python -m src.run_morning_scan` is unchanged; a hosted,
  multi-user caller can now inject a specific user's sleeves (read from Postgres)
  — the last structural blocker to true per-user scans.
- **News sleeve-relevance lookup is now backend-aware** (`routes/news.py`) — it
  reads sleeves through the storage-backend-aware config service, so it reflects
  the active portfolio (Postgres under `db`, the local config under `file`).

This release **completes the Phase 2 storage cutover**: every file-backed store
(watchlists, portfolios, settings, theses, P&L, scans, the opportunistic list)
and the scan engine now honor `STORAGE_BACKEND`. The flag still defaults to
`file`, so the local single-user app is byte-for-byte unchanged; the cloud
deploy sets `STORAGE_BACKEND=db`. Remaining multi-user work (real accounts,
per-user isolation, background jobs) is Phases 3–4.

### Tests
- `tests/test_morning_scan.py` gains `run_scan` config-injection coverage
  (selection, ticker filter, watchlist override, the `--watchlist`/`--sleeve`
  re-injection edge). Full suite at 257 passing.

## [1.4.5] — 2026-06-27

### Added (Phase 2 database cutover — dormant; local behavior unchanged)
- **Scan results store cut over.** Under `STORAGE_BACKEND=db`, the dashboard's
  scan history (list, latest, by-date) and the per-scan write/merge read and
  write the `scan_results` Postgres table instead of the JSON sidecar in
  `outputs/`. The CSV is still written for CLI compatibility. Reads carry a
  synthetic `path` and null size (no file on disk) — opaque metadata the UI
  already ignores.
- **Legacy single watchlist cut over.** The opportunistic watchlist
  (`/watchlist`) now persists in the shared watchlists table under a reserved
  name when `STORAGE_BACKEND=db`, kept hidden from the multi-watchlist list so
  the two stay separate — exactly as they are as separate files locally.
- This completes the storage cutover for every file-backed store. (The shared
  scan engine still reads the global portfolio config; threading per-user config
  through it is the final, separate step.)

### Fixed
- **Reserved watchlist name is now rejected (400) on write** in both backends,
  so a user can't accidentally create/rename a watchlist into the reserved
  opportunistic slot.

### Tests
- Extended `tests/test_storage_cutover.py` with scan-store CRUD/list/upsert +
  payload key-set parity, and legacy-watchlist isolation + reserved-name guards
  (service and route level). Suite at 251 passing.

## [1.4.4] — 2026-06-27

### Added (Phase 2 database cutover — dormant; local behavior unchanged)
- **Three more stores cut over** to dispatch to Postgres under
  `STORAGE_BACKEND=db`, each returning the exact same shapes as before:
  - **Per-ticker portfolio settings** (`portfolio_settings_service` →
    `PortfolioSettingsRepository`) — the allocation/agent overrides per sleeve.
  - **Saved theses** (`thesis_store` → `ThesisRepository`) — portfolio/sleeve/
    ticker LLM memos; the `saved_at` stamp is still applied the same way.
  - **P&L positions** (`pnl_service` → `PnlRepository`) — persistence only; all
    the P&L math, id generation, and validation stay exactly where they were.
  Under the default `file` backend every one of these still reads/writes its
  local JSON file, unchanged.

### Fixed
- **P&L record shape made backend-identical.** A freshly created position now
  always carries the `closing_import_key` field (null until a closing fill
  matches it), so file-backend and DB-backend records have the same key set —
  matching what the Fidelity importer already produced.

### Tests
- Extended `tests/test_storage_cutover.py` with both-backend shape-identity
  coverage for all three stores (incl. ticker-case normalization, thesis
  replace, P&L CRUD + import-key dedupe + create key-set parity). Suite at 240.

## [1.4.3] — 2026-06-27

### Added (Phase 2 database cutover — dormant; local behavior unchanged)
- **Sleeve / portfolio config store cut over.** Under `STORAGE_BACKEND=db`,
  reading and editing your sleeves (the portfolio definitions that drive every
  scan) now goes to Postgres via `PortfolioRepository` instead of rewriting the
  Python config file. Identical shapes and the same HTTP error codes (409 on a
  duplicate name, 404 on a missing sleeve, 400 when you try to delete your last
  one), so the dashboard is unaffected. The cash-reserve read is backend-aware
  too. Under the default `file` backend nothing changes — it still rewrites
  `portfolio_config.py` and hot-reloads, exactly as before.
- **Fresh-database seed.** A new Alembic data migration copies the shipped
  sleeves + cash reserve into the database for the default owner on a brand-new
  Postgres, so turning on the `db` backend boots with real content instead of an
  empty portfolio (which would have made scans fail). Idempotent — it never
  overwrites edits you've already made.

### Tests
- Extended `tests/test_storage_cutover.py`: full sleeve CRUD + HTTP-code
  mapping + integrity-conflict handling under the DB backend, a non-destructive
  exercise of the file backend's config-rewrite path, and a cross-backend
  shape-identity check. Suite at 233 passing.

## [1.4.2] — 2026-06-27

### Added (Phase 2 database cutover — dormant; local behavior unchanged)
- **`STORAGE_BACKEND` switch.** A new environment variable selects where live
  application state is stored: `file` (the default — today's local JSON/config
  files, so every existing install is byte-for-byte unchanged) or `db` (the
  multi-tenant Postgres repositories built in 1.4.0). The cloud deploy will set
  `STORAGE_BACKEND=db`; locally it stays `file`. Because it defaults to `file`,
  this code is inert until the flag is flipped.
- **Watchlists store cut over.** `watchlists_service` now dispatches to
  `WatchlistRepository` when `STORAGE_BACKEND=db`, returning the identical
  `{name, tickers}` shapes — so routes and the frontend are unaffected by the
  backend choice. New `app/backend/services/_storage.py` houses the shared
  dispatch seam (backend flag, short-lived DB session, and an
  `IntegrityError → ValueError` translator for clean conflict handling).

### Fixed
- **Watchlist rename now returns 409, not 500, on a name clash** (DB backend).
  The repository enforces per-user name uniqueness; the rename route now catches
  that and returns a proper conflict status.

### Tests
- New `tests/test_storage_cutover.py` exercises each cut-over service under
  **both** backends and asserts they are shape-identical, plus the rename
  conflict path (service + route) and the integrity-error translation. Suite at
  228 passing.

## [1.4.1] — 2026-06-27

### Added
- **Pattern Backtest: lookback control.** The backtest now has an explicit
  Lookback toggle per timeframe (e.g. 1h: 2wk/1mo/2mo/3mo; daily: 3mo/6mo/1y/2y;
  weekly up to 5y), instead of silently using the max window. Results now show
  the exact window replayed (start → end, N days, signal/ticker counts).
- **Min-confidence** options extended to include **90% and 100%**.

## [1.4.0] — 2026-06-27

### Added (cloud-deploy groundwork — all additive, local behavior unchanged)
- **Deploy-ready backend.** Config is now environment-driven so the same code
  runs locally and in the cloud: CORS origins via `ALLOWED_ORIGINS`, database
  via `DATABASE_URL` (managed Postgres in the cloud, local SQLite otherwise),
  an optional `SKIP_OLLAMA_CHECK` for fast container startup, and a trivial
  `/health` route for platform health checks. Added `docker/Dockerfile.web`
  (uvicorn web image), `railway.toml` (auto-runs DB migrations on deploy),
  `app/frontend/vercel.json`, and a `DEPLOY.md` walkthrough.
- **Multi-tenant database layer (dormant until enabled).** New SQLAlchemy
  models + Alembic migration for users, portfolios, watchlists, settings, P&L,
  theses, and scans — every row scoped to a `user_id` — plus user-scoped
  repositories mirroring the existing file stores 1:1, with tests. Nothing is
  wired into the running app yet; the file-based stores remain the live path,
  so local single-user behavior is unchanged. This is the foundation for the
  hosted multi-user build.

### Fixed
- **Alembic chain now runs from scratch.** A pre-existing migration created a
  duplicate index, which broke `alembic upgrade head` on a fresh database
  (i.e. the first deploy to a new Postgres). Removed the duplicate.
- Modernized the SQLAlchemy `declarative_base` import (2.0 form).

## [1.3.1] — 2026-06-26

### Changed
- **Pattern Scanner contract recommendation, reworked.** Replaced the old
  "ATM-at-breakout, nearest-default-DTE" pick. For each pattern the scanner now
  recommends the single best **payoff-per-dollar** contract within a
  **0.40–0.50 delta, 25–30 DTE** band — the option that gains the most relative
  to its cost if the pattern reaches its measured-move target (falls back to the
  closest listed contract, and says so, when nothing is exactly in-band).
- **The recommendation is consistent everywhere and always actionable.** The
  inline "Contract" panel and the click-in chart modal now derive from the same
  recommendation and highlight the same contract in the chain. The panel shows
  what the pattern implies for the move (entry → target) plus that contract's
  take-profit / stop-loss. A played-out ("stale") setup no longer dead-ends with
  "moved out of position" — it re-anchors to a fresh entry at the current price
  toward the pattern's projected target, clearly flagged.

## [1.3.0] — 2026-06-26

### Changed
- **"Sleeve" is now "Portfolio" everywhere it's visible.** Renamed all
  user-facing labels (sidebar "My Portfolios", the Market-tab manager, the
  Pattern Scanner's portfolio picker, Portfolio Pulse "By Portfolio" /
  per-portfolio Run-agents and thesis controls, chat suggestions, and the
  Backtest + Options Screener tabs — labels, the "Portfolio agents (LLM)"
  backtest engine name, and the per-portfolio attribution/trade columns) and the
  LLM thesis / news / transcript prompts so generated text says "portfolio"
  too. Internal code, API routes (`/sleeves/*`), and config keys are
  unchanged. (Existing saved theses keep their old wording until re-run.)
- **Market News "Your book" feed is now your portfolio holdings only.**
  Watchlist tickers are excluded — they're exploratory, not owned — so the
  book column stays focused on what you actually hold.
- Made two `test_portfolio_config` tests config-agnostic so they don't break
  when you reorganize your portfolios (they no longer hard-code sleeve names
  or assume allocations are set).

### Fixed
- **Options Screener no longer errors with "Failed to load screener" after
  you rename your portfolios.** The Screener and both Backtest panels
  defaulted to a hardcoded portfolio name (`mega_tech`); once that portfolio
  was renamed/removed, the first load fired against a dead name and 400'd.
  They now seed to your first actual portfolio as soon as config loads, so
  there's nothing hardcoded to go stale.
- **Large Pattern Scanner runs ("all watchlists") no longer fail to fetch.**
  Scanning a big universe (e.g. ~318 names across every watchlist) routinely
  ran past the frontend's 120s fetch timeout and aborted with "failed to
  fetch." Raised the scan timeout to 5 minutes, widened the detector thread
  pool and the data-fetch concurrency, and added a heads-up toast for big
  scans plus a clear timeout message ("try fewer tickers") instead of a raw
  fetch error. A 139-name scan now completes in ~90s; 300+ names finish
  within the new budget.
- **Watchlists can now be deleted from the left sidebar.** The sidebar
  watchlist headers only had an edit (pencil) button — delete existed only
  on the Market-tab manager, so there was no obvious way to remove a
  watchlist where they're actually listed. Added a trash button (with a
  confirm toast) next to each. Also wrapped the sidebar's create handlers so
  a failed create (e.g. backend unreachable) surfaces the context's error
  toast and leaves the form open to retry, instead of an unhandled rejection.

### Added
- **Pattern Scanner backtest.** New "Backtest" tab inside the Pattern Scanner
  that replays the detectors over history: every time a pattern fires, it
  simulates buying an option (target delta + DTE) and selling it a set number
  of candles later, then reports win rate, average return, expectancy, P&L,
  and a per-pattern breakdown. **Optimize** mode sweeps delta x DTE x hold and
  ranks every combination so you can see which option to buy and how long to
  hold. Prices off **real historical option fills** by default (the data plan
  exposes intraday option aggregates at 1h/15m, confirmed live); BSM is a
  fast fallback, flagged because it diverges from real premiums (~24% median,
  worse for OTM / high-IV names). Universe is any portfolio or watchlist, or a
  custom ticker list. New: `src/backtesting/pattern_options.py` engine +
  `strike_for_delta`/`bsm_delta` in `options_proxy.py`, the SSE route
  `POST /patterns/backtest`, and the `PatternBacktestPanel` UI. 11 new unit
  tests (206 total).
- **Options Screener and Options-Strategy Backtest can now run off a
  watchlist, not just a portfolio.** Both tabs gained a combined
  "Portfolio / Watchlist" picker (portfolios and watchlists grouped in one
  dropdown); pick either as the ticker universe. The backend resolves the
  chosen source to its tickers (unknown/empty lists return a clear error).
  The optional ticker-subset box still narrows whichever list you pick. The
  LLM-agent ("Portfolio agents") backtest stays portfolio-only — it needs a
  portfolio's agent panel, which a watchlist doesn't have.
- **Weekly timeframe in the Pattern Scanner.** A fourth bar size alongside
  daily / 1h / 15m, for long-base position setups that play out over months.
  Weekly bars are date-labeled (no intraday RTH filtering), lookbacks run up
  to 5 years (default 3y), the historical win threshold scales up to 6% over
  the 20-bar (~5-month) outcome window, and trade plans pick ~90-DTE
  contracts with the theta hold widened to match. Also fixes a latent bug
  where any non-daily timespan was treated as intraday — weekly now bypasses
  the RTH filter and HH:MM labeling correctly.

## [1.2.0] - 2026-06-12

Risk-managed trade plans on the Pattern Scanner's options plays, a freshness-
first scanner workflow, per-sleeve agent runs, and a reliability pass on
quotes and intraday data.

### Fixed
- **Intraday charts no longer stop days in the past on heavily-traded
  names.** Polygon paginates long intraday aggregate requests once its
  internal scan budget is hit — on 90-day hourly windows, NVDA truncated at
  ~52 trading days and AMD shortly after, so their Pattern Scanner charts
  froze mid-history while quieter names were complete. The data client now
  follows the `next_url` cursor (up to 10 pages) and returns the full
  window. Two related hardenings: hourly RTH filtering now keeps the 09:00
  bar (it contains the 09:30 market open — previously the session's first
  and most important hour was dropped), and truncated provider responses
  are served but never cached, so a degraded reply can't pin a stale chart
  for the cache TTL.
- **Left-rail quotes no longer blank out when the data provider is slow.**
  A failed price fetch used to be cached as a null for a full minute —
  one Polygon timeout blanked the ticker and clobbered its previously-good
  quote. Quotes now use last-known-good semantics: only successes enter the
  cache, failures serve the most recent real price (slightly stale beats a
  dash), and the sidebar merges refreshes without overwriting loaded prices.
- **Trade plans are no longer priced on dead signals.** The Trade Plan card
  now classifies the latest detection as **LIVE / WATCH / STALE** against
  where price actually is: target already reached or stop breached → stale
  (no option plan; "rescan" notice with the original geometry for
  reference); valid setup but trigger far from price → watch (premiums
  labeled as estimates at the trigger); triggered-and-in-progress or
  near-trigger → live. Previously a months-old Cup & Handle could present a
  full premium plan on a setup trading 11% below its stop.

### Added
- **Pattern Scanner trade plans — on the options play.** Click any pattern
  and the Signal Analysis panel shows a concrete plan for the play's
  **contract** (ATM call/put at the breakout, expiry suited to the scan
  timeframe): **buy / cut / take-profit premiums**, max loss per contract,
  premium-space risk/reward, and a **position sizer in contracts** (account $
  + risk % → number of contracts). A risk-tolerance toggle (Conservative /
  Moderate / Aggressive) sets the underlying stop at **1.0× / 1.5× / 2.5×
  ATR** — so the same tolerance gives wider stops on volatile names — and
  those underlying levels are translated to premiums by **Black-Scholes
  repricing at the contract's IV, anchored to the live market mid**, with the
  expected hold's theta priced into the target (hold scales with the scan
  timeframe). A **theta viability guard** flags plays where decay outruns the
  measured move — it first retries a longer-dated expiry automatically, and
  if no contract clears theta the card warns "not viable as a long option"
  instead of showing a take-profit below entry. Falls back to underlying
  share levels when the chain is unavailable. Backed by `GET
  /patterns/trade-plan/{ticker}/{pattern}` and a tested pure engine
  (`src/patterns/trade_plan.py`, 16 unit tests).
- **Pattern Scanner "Today's plays" sort.** Results default to freshest
  breakouts first, grouped by day (Today / Yesterday / …) and ranked by
  confidence within each day, with new filter chips for recency, minimum
  confidence, and bias.
- **Per-sleeve "Run agents"** in Portfolio Pulse — scan one sleeve's names
  on demand; results merge into the day's saved scan instead of replacing it.

### Changed
- Forced **dark theme** (the terminal's design language) instead of following
  the OS setting, which rendered white on light-mode machines.
- Reworked the left-nav into a **3×2 pill grid** so six sections (incl. the
  new P&L tab) no longer squish into one cramped row.
- Saved analyses (per-ticker scans, theses) now **persist across refresh and
  restart**.

### Fixed
- Watchlist saves no longer fail silently; Save flushes the typed ticker.

## [1.1.0] - 2026-06-10

A new P&L tab, intraday pattern scanning, and a production-hardening pass
driven by a full backend + frontend audit.

### Added
- **P&L Tracker tab.** Track contracts you take or find attractive in one
  ledger: manual entry (stocks + options, long/short, paper vs real), a
  one-click **Track** button on every option-chain row (1 contract at the
  live mid), and live **mark-to-market** (chain-snapshot mid → last trade →
  day close, with a per-contract aggregate fallback after hours; stocks at
  the latest close). Summary cards (realized / unrealized / total, win
  rate), a realized equity curve, and open/closed tables with prefilled
  close-at-mark. Positions persist in the gitignored `app/data/`.
- **Fidelity CSV import.** Both Fidelity export flavors (Positions and
  Activity/transactions) parse into real fills: compact option symbols
  (`-NVDA260717C200`) are decoded, opening fills create positions, closing
  fills FIFO-match (partial closes split correctly), and re-imports are
  idempotent via row fingerprints. The raw CSV is never persisted. A plan
  for SnapTrade/Akoya auto-sync — and the explicit rejection of stored-
  credential scraping — lives in `docs/FIDELITY_INTEGRATION.md`.
- **Intraday Pattern Scanner.** All 12 detectors now run on **1-hour and
  15-minute bars** alongside daily: a timeframe toggle with per-timeframe
  lookbacks, regular-trading-hours filtering (premarket noise excluded),
  US-Eastern timestamps on intraday signals and charts, and historical
  win-rate thresholds that scale with the bar size (3% daily / 1.5% hourly
  / 0.75% on 15m over 20 bars).
- Startup-time `requests` dependency declared explicitly (was transitive).

### Fixed
- **API-key log leak.** The pattern scanner's data client passed the Polygon
  key as a URL query parameter, which httpx logged at INFO on every request
  (and embedded in exception messages). Auth moved to a Bearer header,
  httpx/httpcore loggers capped at WARNING, and provider errors re-raise
  sanitized. Transient Polygon `ReadTimeout`s now retry once.
- **Scan UI could hang forever.** The SSE consumer read a stale `scanStatus`
  closure after the stream ended and had no inactivity timeout; a stalled
  backend left the dashboard on "running" indefinitely. Now a 5-minute
  watchdog aborts dead streams and a missing terminal event surfaces as an
  error instead of a silent success.
- Chat context crashed (`ValueError`) when a screener candidate lacked a
  conviction percentage.
- Sleeves backtest pre-flight made blocking provider calls inside the async
  endpoint, freezing every other request while it probed tickers — moved to
  a worker thread.
- Corrupt scan files (truncated JSON/CSV) returned 500s and could blind the
  dashboard; `/sleeves/scans/latest` now falls back to the next readable
  scan and corrupt files report 422 with the filename.
- Named-watchlist writes were not atomic (crash mid-write corrupted the
  store) and accepted unvalidated bodies; both fixed.

### Changed
- Frontend dead-code purge: **46 orphaned files removed** (legacy flow/agent
  runner cluster, the entire `settings/` directory, the superseded sleeves
  component chain, unused UI primitives), plus the unused `NodeProvider`
  wrapper. Backend base URL consolidated into one `api-base.ts` (was 22
  hardcoded `localhost:8000` literals).
- All `alert()`/`confirm()` dialogs replaced with sonner toasts (delete
  confirmations use toast action buttons).
- Backtest day-skips and graph errors now log with context instead of
  swallowing exceptions / printing; FastAPI startup migrated from the
  deprecated `on_event` to a lifespan handler; assorted dead imports pruned.

## [1.0.0] - 2026-06-03

First stable release. The five-tab dashboard (Market · Screening · Portfolio ·
News · Calls) is feature-complete and tested, the internals have been cleaned
up and documented, and the API/UI surface is considered stable.

### Added
- **Per-name conviction score + recommendation in Portfolio Pulse.** Every name
  now carries an overall recommendation (Strong Buy → Strong Sell) and a 0–100
  conviction score, derived from the signed weighted agent blend — shown as a
  compact pill on each row and a headline verdict card in the expanded detail.
- **Startup configuration check** in the backend: a loud, actionable warning
  when `DEEPSEEK_API_KEY` or a market-data key is missing, so a misconfigured
  install fails visibly instead of silently degrading to "no edge".

### Changed
- **Extracted the options scoring engine** out of the 5,463-line
  `app/backend/routes/sleeves.py` into a dedicated
  `app/backend/services/options_scoring.py` (per-strategy scorers, conviction-%
  helpers, chart-pattern scorer factory, and the strategy registry). The route
  file is now ~3,360 lines focused on HTTP; the registered route surface is
  byte-identical (verified) and all 156 tests pass.
- **Consolidated runtime user state** under `app/data/` (watchlists now sit next
  to portfolio settings), with a read-only fallback to the legacy
  `app/backend/data/` path so existing installs keep their watchlists.
- **Rewrote the Pattern Scanner documentation** to match the platform: 12
  detectors (7 bullish / 5 bearish), the `0.4·breakout + 0.3·volume +
  0.3·touch` confidence blend, the chart modal with trendline + key-level
  overlays, the 730-day historical win-rate, and the three graded options plays.
- Replaced the stale upstream `app/README.md` (wrong clone URL, wrong
  providers, wrong Python version) with a correct pointer to the root README,
  and fixed the API-key hints in `app/run.sh` / `app/run.bat`.
- Refreshed the FastAPI app identity to "Alpha Terminal API" and the stale
  `sleeves.py` module docstring.

### Fixed
- **Fundamentals & Valuation analyst rendering.** These agents store `reasoning`
  as a structured dict, which the UI previously stringified to `"[object
  Object]"`. The verdict card now renders each category (profitability, growth,
  financial health, price ratios) as a labeled, signal-colored row.

### Removed
- Pruned orphaned frontend files with zero importers: `portfolio-pulse-header.tsx`
  and the entire `stocks/` directory (`stock-card.tsx`, `use-my-stocks.ts`).

## [0.2.2] - 2026-06-02

### Added
- Per-name agent scan in Portfolio Pulse — run a single ticker's sleeve agents
  on demand without overwriting the saved morning scan.

### Changed
- Agents now read Finnhub fundamentals through `get_financial_metrics`, so they
  see real growth/margin/ratio data even when Massive's plan omits it (fixes the
  "no data for this stock" behavior on the Starter plan).

## [0.2.1] - 2026-06-01

### Added
- LLM thesis synthesis at three scopes in Portfolio Pulse: per-name, per-sleeve,
  and whole-portfolio.

### Removed
- Legacy IDE-shell components inherited from the upstream fork, replaced by the
  three-pane dashboard layout.

## [0.2.0] - 2026-05-31

### Added
- **Market News tab** — Finnhub-backed, three-column layout, macro
  auto-categorization, and AI article summaries.
- **Earnings-call analysis tab** — paste text / URL / PDF and get a 9-section
  structured breakdown.
- **Finnhub free-tier integration** — insider + growth/turnover backfill,
  Market-tab fundamentals enrichment, and a shared token-bucket rate limiter.
- **Pattern Scanner** — 12 chart-pattern detectors with confidence scoring,
  a chart modal with trendline overlays, historical win-rate analysis, and
  graded options plays.

### Changed
- **Realistic options backtester** — profit-target / stop-loss / DTE-roll exit
  model, a slippage model, percentage-based conviction gating, and exact entered
  contract display.

## [0.1.0] - 2026-05-27

Initial Alpha Terminal release.

### Added
- Three custom agents: `alpha_seeker`, `energy_transition`, `emerging_tech`,
  alongside the 19 upstream investor-persona agents.
- Themed portfolio **sleeves**, the morning scan, and sleeve attribution.
- The **Sleeves Dashboard** — read-only foundation, live SSE scan trigger,
  per-ticker drill drawer, watchlist editor, and scan history.
- DeepSeek (R1/V3) LLM routing and Massive (Polygon) market-data adapter with a
  `financialdatasets.ai` fallback.

[1.2.0]: https://github.com/ronitg1/alpha-terminal/releases/tag/v1.2.0
[1.1.0]: https://github.com/ronitg1/alpha-terminal/releases/tag/v1.1.0
[1.0.0]: https://github.com/ronitg1/alpha-terminal/releases/tag/v1.0.0
[0.2.2]: https://github.com/ronitg1/alpha-terminal/releases/tag/v0.2.2
[0.2.1]: https://github.com/ronitg1/alpha-terminal/releases/tag/v0.2.1
[0.2.0]: https://github.com/ronitg1/alpha-terminal/releases/tag/v0.2.0
[0.1.0]: https://github.com/ronitg1/alpha-terminal/releases/tag/v0.1.0
