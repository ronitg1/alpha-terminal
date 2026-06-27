# Changelog

All notable changes to Alpha Terminal are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
