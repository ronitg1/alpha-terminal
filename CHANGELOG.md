# Changelog

All notable changes to Alpha Terminal are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[1.1.0]: https://github.com/ronitg1/alpha-terminal/releases/tag/v1.1.0
[1.0.0]: https://github.com/ronitg1/alpha-terminal/releases/tag/v1.0.0
[0.2.2]: https://github.com/ronitg1/alpha-terminal/releases/tag/v0.2.2
[0.2.1]: https://github.com/ronitg1/alpha-terminal/releases/tag/v0.2.1
[0.2.0]: https://github.com/ronitg1/alpha-terminal/releases/tag/v0.2.0
[0.1.0]: https://github.com/ronitg1/alpha-terminal/releases/tag/v0.1.0
