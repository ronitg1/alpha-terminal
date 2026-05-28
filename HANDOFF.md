# HANDOFF — rg-alpha-engine Sleeves Dashboard v2

**For:** the next Claude session in this repo. Read this BEFORE doing anything else, then read [CLAUDE.md](CLAUDE.md) for the broader conventions.

**Last updated:** 2026-05-27 by prior session

---

## Quick orientation

`rg-alpha-engine` is a forked + customized `virattt/ai-hedge-fund`. We've built a custom Sleeves Dashboard on top of it (FastAPI backend + React/Vite frontend under `app/`). The dashboard surfaces signals from a portfolio of "sleeves" (energy_transition, mega_tech, emerging_tech, opportunistic) each scored by a panel of agents (a mix of upstream investor-persona agents plus 3 custom: `alpha_seeker`, `energy_transition`, `emerging_tech`).

**The approved active plan** lives at `C:\Users\rdpadmin\.claude\plans\while-those-are-running-luminous-wreath.md`. It's a 5-phase response to a user review of the v1 dashboard. Phases A→B→E→D→C is the ship order. **We are mid-Phase B.**

---

## Status snapshot

### Phase A — Clarity · ✅ SHIPPED (commit `0aa465e`)

Surface what the backend already has. Done:
- Drill drawer accordion headers show a one-sentence reasoning preview (≤140 chars) before expanding.
- Sleeve card rows have a `title=` tooltip showing the dominant agent's reasoning.
- High-conviction strip falls back to "Top by Confidence · top 3 neutrals" when no rows clear the conviction bar.
- New `GET /sleeves/analysts` endpoint exposing display_name / description / investing_style.
- New `<AnalystChip>` atom (Shadcn Tooltip wrapper) replaces bare agent badges everywhere — sleeve cards now show "Aswath Damodaran" not "aswath_damodaran".
- `<TrafficLight>` with `status="unknown"` now shows a Tooltip explaining why the field can't be classified (e.g. FEOC needs supplier-chain data we don't have). Removes the "this is broken" perception.

### Phase B — Per-ticker data · 🚧 IN PROGRESS

**Status:** backend imports added (`MassiveClient`, `convert_company_news`, `convert_prices`, `get_financial_metrics`) in `app/backend/routes/sleeves.py` — see uncommitted diff. **Nothing else done yet.** The endpoint and frontend components are unwritten.

**Still to do:**

1. **B1 — Backend `GET /sleeves/ticker/{ticker}`**. Add the endpoint in `app/backend/routes/sleeves.py` (imports are already in place from the dirty diff). Returns `{ ticker, price_history, fundamentals, recent_news }`:
   - `price_history`: 90 days, via `MassiveClient.get_daily_aggregates(ticker, from, to)` then `convert_prices(response)`.
   - `fundamentals`: latest TTM via `get_financial_metrics(ticker, end_date, period="ttm", limit=1)` (will route to FDS since `DATA_PROVIDER=fds` in `.env`).
   - `recent_news`: top 5 via `MassiveClient.get_company_news(ticker, start_date=..., end_date=..., limit=5)` then `convert_company_news(response, ticker=ticker)`.
   - Add a 5-min TTL in-memory cache (dict keyed by ticker; check timestamps).
2. **B2 — `<PriceSparkline>` atom** at `app/frontend/src/components/sleeves/price-sparkline.tsx`. Inline SVG polyline of closes. Props: `{prices: PriceBar[], width?, height?}`. End-price label + % change badge (green/red).
3. **B3 — `<FundamentalsCard>`** at `app/frontend/src/components/sleeves/fundamentals-card.tsx`. KV grid: market cap, P/E, P/B, EV/EBITDA, op margin, net margin, revenue growth, FCF yield. Reuse the `<KVChip>` pattern from `ticker-drill-drawer.tsx`. "Loading…" skeleton while fetch resolves.
4. **B4 — `<RecentNewsList>`** at `app/frontend/src/components/sleeves/recent-news-list.tsx`. Top 5 headlines: publisher chip + title + relative date + link in new tab.
5. **B5 — Drill drawer restructure**: re-order `ticker-drill-drawer.tsx` to: header → sparkline → fundamentals → variant perception callout → per-agent verdicts → recent news.
6. **Types**: add `PriceBar`, `Fundamentals`, `NewsItem`, `TickerData` to `app/frontend/src/types/sleeves.ts`.
7. **API client**: add `sleevesApi.getTickerData(ticker)` to `app/frontend/src/services/sleeves-api.ts`.

### Phase E — Lagging mega-tech options screener · ⬜ NOT STARTED

User has Massive options data on their plan. Strategy: rank mega-tech tickers by conviction across three signals (20-day return vs QQQ, 5-day return vs QQQ, RSI < 45). Show both calls and puts (user picks direction per ticker).

New files when started:
- `src/tools/massive/options.py` — `get_options_chain()` wrapper for Polygon `/v3/snapshot/options/{ticker}`.
- Backend endpoints in `app/backend/routes/sleeves.py`: `GET /sleeves/options/screener?sleeve=mega_tech` and `GET /sleeves/options/chain/{ticker}`.
- Frontend `app/frontend/src/components/sleeves/options/` directory with `options-tab.tsx`, `options-screener-card.tsx`, `option-chain-viewer.tsx`, `option-leg-row.tsx`.

### Phase D — Backtest panel · ⬜ NOT STARTED

Two sub-tabs: sleeves backtest (reuse upstream `BacktestService` + new `sleeve_attribution` bridge) and options-strategy backtest (gated on Massive historical options data — confirm entitlement before starting D2; fallback is Black-Scholes proxy).

### Phase C — Custom agent prompt iteration · ⬜ NOT STARTED, BEST DONE LAST

Wait until B+E are live so we can observe real outputs and tune against them. Touches `src/agents/{alpha_seeker,energy_transition,emerging_tech}.py`.

---

## Dirty / uncommitted state

```
 M app/backend/routes/sleeves.py    # 7 added lines: imports for Massive client + converters. Phase B1 in-progress.
 M app/frontend/package-lock.json   # incidental from npm install; commit when convenient or stash
?? .claude/launch.json              # user added autoPort:true. Already gitignored? Check before committing.
```

The next session should:
1. Look at the import block at the top of `app/backend/routes/sleeves.py` (lines ~50-65) — `MassiveClient`, `MassiveError`, `convert_company_news`, `convert_prices`, `get_financial_metrics` are already imported and ready to use.
2. Implement Phase B1 (the `/sleeves/ticker/{ticker}` endpoint) using those imports.
3. Then move through B2–B5 in any order.

---

## Commits this branch (local-only `main`, no remote yet)

```
0aa465e ui(phase A): clarity — surface reasoning, analyst descriptions, FEOC explainer    ← Phase A complete
2ce48eb config: model routing — Sonnet by default, Opus on demand, pinned subagents
1af9d40 docs: update CLAUDE.md + README for Sleeves Dashboard
df4cf96 ui(phase 4a+4b): JSON sidecar for past scans + history dropdown                   ← v1 Phase 4
fbcf942 ui(phase 3b): watchlist editor                                                    ← v1 Phase 3b
0985d9e ui(phase 3): ticker drill drawer + traffic-light atom                             ← v1 Phase 3
608bbbe fix(energy_transition): widen sub_sector field
6ee2f98 ui(phase 2): live scan trigger + SSE activity feed                                ← v1 Phase 2
7c6afcd ui(phase 1): Sleeves Dashboard — read-only foundation                             ← v1 Phase 1
5ec4d04 fix(scan): load_dotenv() so DEEPSEEK_API_KEY is visible to agents
6e23ffa scan: --tickers filter + extended cred check + provider banner
57ef53a scripts: cred_check + NVDA smoke test for live verification
a76d6b6 docs: add CLAUDE.md with project conventions + ralph-wiggum guidance
c5d19c2 phases 2-6: custom agents, sleeves, morning scan, attribution, watchlist          ← rg-alpha-engine core
e4f5c04 foundation: rg-alpha-engine project bootstrap                                     ← rg-alpha-engine core
```

---

## How to resume in a new chat

1. **Open Claude Code** with the working dir set to `C:\Users\rdpadmin\Desktop\rg-alpha-engine`. The session will default to **Sonnet** (per `.claude/settings.json`). Use `/model opus` if you're starting hard architectural work; stay on Sonnet for the routine implementation.
2. **First prompt to give Claude:** *"Read HANDOFF.md, then continue with Phase B per the plan at C:/Users/rdpadmin/.claude/plans/while-those-are-running-luminous-wreath.md. The backend imports for B1 are already in place."*
3. **Spin up dev environment:**
   ```powershell
   # terminal 1 — backend
   cd C:\Users\rdpadmin\Desktop\rg-alpha-engine
   $env:Path += ";C:\Users\rdpadmin\AppData\Roaming\Python\Scripts"
   poetry run uvicorn app.backend.main:app --reload
   # terminal 2 — frontend
   cd C:\Users\rdpadmin\Desktop\rg-alpha-engine\app\frontend
   npm run dev
   # then open http://localhost:5173
   ```
4. **Quick health checks before doing anything live:**
   ```powershell
   poetry run python scripts/cred_check.py   # confirms Massive + FDS + DeepSeek keys all respond
   poetry run pytest tests/                  # confirms unit tests still pass
   ```

---

## Gotchas the next session will hit

1. **Two uvicorn instances on port 8000 will collide silently.** Earlier this session we had a stale process (PID 16556) holding port 8000 alongside a fresh one. Symptoms: new endpoints return 404 even though the file has the route. Fix: `Get-Process node, python | Select-Object Id, ProcessName` to find them, then `Stop-Process -Id <pid>`. Always check `netstat -ano | findstr :8000` if a new route 404s.

2. **Some other dev server (PID 32564, node) is holding port 3000 since 2026-05-17.** Not ours. It blocks the Claude_Preview MCP from working out-of-the-box. Either kill it after confirming with the user, or use `autoPort: true` in `.claude/launch.json` (already set).

3. **Frontend has 21 npm vulnerabilities + ~15 pre-existing TS errors from upstream code** (App.tsx Layout casing, sidebar.tsx ref types, unused vars in Flow.tsx). None in our new code; `tsc --noEmit` is noisy but the dev server works fine. Don't waste time fixing these unless asked.

4. **`DATA_PROVIDER=fds` in `.env`** routes all data calls to financialdatasets.ai. The user's Massive plan doesn't include the Financials & Ratios expansion — calling `/stocks/financials/v1/*` returns 403. For Phase B, fundamentals must come from FDS; prices can come from Massive (Massive's price endpoints work on the current plan).

5. **`EnergyTransitionSignal.sub_sector` was widened to plain `str`** in commit `608bbbe` because the LLM kept generating values not in the original Literal enum (`'nuclear'`, `'solar_inverter'`, etc.). If you tighten validation again, regression-test with a live scan.

6. **Run Scan from the UI re-scans whatever tickers were in the last scan** — that's the default. There's no UI to scan a single ad-hoc ticker yet; the drill drawer is the per-ticker investigation surface. If you want one-shot scans, that's Phase E's ticker input or a future Quick-Scan box.

7. **`outputs/` contains generated CSVs and (after live scans) JSON sidecars.** Not in `.gitignore` currently. Consider adding it before any GitHub push.

8. **🚨 EXPOSED API KEYS still need rotating** — DeepSeek, Massive, financialdatasets.ai, Anthropic. All four were visible in earlier chat logs. They still work for testing but assume compromised for production.

---

## File map (where things live)

### Backend
```
app/backend/
├─ main.py                              ← FastAPI app entry
├─ routes/
│  ├─ sleeves.py                        ← all /sleeves/* endpoints — config, watchlist, scans, scan/run SSE
│  ├─ hedge_fund.py                     ← upstream — /hedge-fund/run + /hedge-fund/backtest SSE patterns to mirror
│  └─ ...                               ← upstream — flows, api_keys, etc.
├─ services/
│  ├─ watchlist_service.py              ← atomic file rewrite for src/config/watchlist.py
│  └─ backtest_service.py               ← upstream — BacktestService used by /hedge-fund/backtest
└─ models/
   ├─ events.py                         ← SSE event classes (StartEvent, ProgressUpdateEvent, etc.)
   └─ schemas.py                        ← request/response pydantic models
```

### Frontend
```
app/frontend/src/
├─ App.tsx                              ← mounts <Layout/>
├─ components/
│  ├─ Layout.tsx                        ← TabBar / Sidebars / BottomPanel chrome; auto-opens Sleeves tab
│  ├─ tabs/, panels/                    ← upstream — chrome components
│  └─ sleeves/                          ← OUR dashboard lives here
│     ├─ sleeves-tab.tsx                ← top-level tab container
│     ├─ dashboard-header.tsx           ← title + Refresh + Run Scan + scan-history dropdown
│     ├─ high-conviction-strip.tsx      ← top of dashboard ranked cards (+ Top by Confidence fallback)
│     ├─ sleeve-grid.tsx                ← 2x2 grid wrapper
│     ├─ sleeve-card.tsx                ← per-sleeve table; opens drill drawer
│     ├─ ticker-drill-drawer.tsx        ← right-side Sheet with per-agent accordion
│     ├─ watchlist-editor.tsx           ← Dialog for editing the opportunistic watchlist
│     ├─ live-activity-panel.tsx        ← SSE event log shown during a live scan
│     ├─ signal-pill.tsx                ← bull/bear/neutral atom
│     ├─ traffic-light.tsx              ← clean/amber/red atom; FEOC unknown explainer
│     └─ analyst-chip.tsx               ← Tooltip-wrapped agent name (display_name + description)
├─ contexts/
│  ├─ sleeves-context.tsx               ← single source of truth: config, latestScan, scanStatus, liveActivity, watchlist, analystMeta, scanHistory, selectedTicker
│  └─ tabs-context.tsx                  ← upstream — TabType extended with 'sleeves'
├─ services/
│  ├─ sleeves-api.ts                    ← fetch wrapper for /sleeves/* endpoints
│  └─ tab-service.ts                    ← upstream — extended with createSleevesTab()
└─ types/sleeves.ts                     ← wire-format types
```

### Python core (unchanged from upstream + our additions)
```
src/
├─ agents/                              ← 19 upstream + 3 custom (alpha_seeker, energy_transition, emerging_tech)
├─ tools/
│  ├─ api.py                            ← public surface used by agents; dispatches Massive vs FDS
│  └─ massive/                          ← Polygon adapter
│     ├─ client.py                      ← MassiveClient with retries
│     └─ converters.py                  ← Polygon JSON → FDS-shape pydantic
├─ config/
│  ├─ portfolio_config.py               ← sleeves, agent panels, weights
│  └─ watchlist.py                      ← opportunistic watchlist (rewritten by UI)
├─ run_morning_scan.py                  ← CLI entrypoint; reusable functions for backend
├─ utils/
│  ├─ analysts.py                       ← ANALYST_CONFIG with display_name + description + investing_style
│  ├─ llm.py                            ← call_llm; DeepSeek defaults
│  └─ llm_router.py                     ← task-aware DeepSeek picker + exp backoff helper
└─ backtesting/
   └─ sleeve_attribution.py             ← Trade dataclass + per-sleeve metrics; ready to be bridged into the live backtester
```

### Operational
```
.claude/
├─ settings.json                        ← project model defaults (sonnet + effortLevel:high)
├─ launch.json                          ← Claude Preview dev-server config (frontend on 5173, autoPort:true)
└─ agents/
   ├─ explorer.md                       ← Sonnet-pinned read-only file-search subagent
   └─ architect.md                      ← Opus-pinned plans-only subagent

outputs/                                ← morning scan output CSVs + (post-live-scan) JSON sidecars
scripts/
├─ cred_check.py                        ← three-provider key sanity check
└─ smoke_test_nvda.py                   ← single-ticker live test
```

---

## Plan reference (one paragraph each)

**Phase A** — make existing neutrals understandable. Reasoning preview in collapsed accordion, AnalystChip with descriptions, FEOC tooltip when unknown. ✅ SHIPPED.

**Phase B** — drill drawer shows context not just verdicts. New `/sleeves/ticker/{ticker}` endpoint pulls 90-day prices (Massive), latest fundamentals (FDS), top-5 news (Massive). New atoms `<PriceSparkline>` (inline SVG, no chart lib), `<FundamentalsCard>` (KV grid), `<RecentNewsList>`. Drawer re-ordered. 🚧 IN PROGRESS (backend imports staged).

**Phase E** — options screener. Conviction-ranked mega-tech tickers (20d vs QQQ, 5d vs QQQ, RSI<45 = 3 signals; 3/3 = high conviction). Calls + puts split view. User picks direction per ticker. Needs `src/tools/massive/options.py` + 2 backend endpoints + 4 frontend components.

**Phase D** — backtest panel with two sub-tabs: sleeves backtest (reuse `BacktestService` + `sleeve_attribution`), options-strategy backtest (needs historical options data — confirm Massive plan entitlement first, fallback to Black-Scholes proxy).

**Phase C** — custom agent prompt iteration. Loosen alpha_seeker's "no edge" bar, give energy_transition permission to use industry knowledge for FEOC, tune emerging_tech. Best done after B+E so we have real outputs to tune against.

---

## Anti-scope reminders

- No sleeve editor UI (user explicitly deferred).
- No new chart library — inline SVG only.
- No Greeks calculator — surface what Massive returns.
- No live order placement — copy-to-clipboard only.
- No onboarding modal — tooltips + callouts + drawer restructure cover the same ground.
- No GitHub push until user gives the green light (project is local-only).
