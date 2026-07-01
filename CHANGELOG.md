# Changelog

All notable changes to Alpha Terminal are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
