# CLAUDE.md — operating notes for AI coding sessions in this repo

Read this before doing any work in this codebase. It captures the conventions baked into the existing 2,700 lines so new edits stay coherent.

**If you are starting a fresh session, read [HANDOFF.md](HANDOFF.md) FIRST** — it has the current status, the dirty git state, what was in flight when the prior session ended, and exactly where to pick up.

## What this project is

`rg-alpha-engine` is a customized fork of `virattt/ai-hedge-fund` for retail alpha generation. Signals only — no execution. Primary stack:

- **LLM:** DeepSeek (R1 for reasoning, V3 for cheap parsing) — defaults set in [`src/utils/llm.py`](src/utils/llm.py); task routing in [`src/utils/llm_router.py`](src/utils/llm_router.py).
- **Data:** Massive (Polygon.io rebrand) via the adapter in [`src/tools/massive/`](src/tools/massive); legacy fallback is `financialdatasets.ai`. Switch with `DATA_PROVIDER=massive|fds`.
- **Custom agents:** `alpha_seeker`, `energy_transition`, `emerging_tech` in [`src/agents/`](src/agents/).
- **Sleeves + scan + attribution:** [`src/config/portfolio_config.py`](src/config/portfolio_config.py), [`src/run_morning_scan.py`](src/run_morning_scan.py), [`src/backtesting/sleeve_attribution.py`](src/backtesting/sleeve_attribution.py).
- **UI (Sleeves Dashboard):** React 18 + Vite + Shadcn/UI in [`app/frontend/src/components/sleeves/`](app/frontend/src/components/sleeves/); FastAPI SSE backend in [`app/backend/routes/sleeves.py`](app/backend/routes/sleeves.py). One context (`sleeves-context.tsx`) owns scan/watchlist/history state.

## Run / test cheat sheet

```powershell
# always activate poetry env
$env:Path += ";C:\Users\rdpadmin\AppData\Roaming\Python\Scripts"
Set-Location "C:\Users\rdpadmin\Desktop\rg-alpha-engine"

# tests (43 currently, all passing) — runs in <2s
poetry run pytest tests/

# morning scan
poetry run python -m src.run_morning_scan                       # all sleeves
poetry run python -m src.run_morning_scan --sleeve mega_tech    # one sleeve
poetry run python -m src.run_morning_scan --watchlist           # ad-hoc tickers

# legacy main entry (full LangGraph with risk + portfolio manager)
poetry run python src/main.py --tickers NVDA --analysts alpha_seeker --show-reasoning

# Sleeves Dashboard UI — two terminals
# 1) backend with hot reload
poetry run uvicorn app.backend.main:app --host 127.0.0.1 --port 8000 --reload
# 2) frontend dev server
cd app/frontend; npm run dev
# Then open http://localhost:5173 — Sleeves tab auto-opens on first load.
```

## Coding conventions enforced in this repo

These aren't preferences — the existing code follows them. If you add code that violates these, you're creating drift.

1. **Type hints on every public function.** `from __future__ import annotations` at the top of every module so we can use `|` syntax on Python 3.12.
2. **Docstrings on every public function and module.** Module docstrings explain the *why*, not the *what*. Function docstrings cover non-obvious behavior.
3. **No silently swallowed exceptions.** Either re-raise, log with `logger.warning`/`logger.exception`, or convert to a domain-specific error (see `MassiveError` in `src/tools/massive/client.py`).
4. **Retries are explicit, not hidden.** Exponential backoff with jitter for external APIs (DeepSeek, Massive). Pattern: see `_sleep_for_retry` in `src/tools/massive/client.py` and `call_with_backoff` in `src/utils/llm_router.py`.
5. **Validate at import time.** Config files (e.g. `src/config/portfolio_config.py`) call their validator at module load so a bad edit fails loudly instead of at runtime two hours into a scan.
6. **Tests pin behavior, not implementation.** Schema construction tests catch typos in `Literal` enum values. Aggregation tests use hand-crafted fixtures rather than mocks. See `tests/test_morning_scan.py` for the pattern.
7. **No emojis in code or docs.** The user did not ask for them.
8. **Every UI change MUST work on iOS / mobile, not just desktop.** This is a hard requirement, not a nice-to-have. The app is a real phone-usable web app. For any frontend change: verify it at a phone width (default breakpoint = mobile, `md:` = desktop, `max-md:` for mobile-only overrides), avoid horizontal overflow, use `dvh`/safe-area insets where height/edges matter (see `.app-vh` in `index.css`), and ensure tables/wide layouts scroll or reflow into cards on narrow screens. When you touch a component, leave it working on iOS Safari — no exceptions.

## Provider gotchas (Massive)

Two coverage gaps to remember when reasoning about agent behavior:

- **Insider trades** — Massive/Polygon doesn't publish bulk Form 4 data. `get_insider_trades()` returns `[]` and logs once. Agents that use it (Burry, Sentiment) handle the empty list. Switch `DATA_PROVIDER=fds` if real insider data is required.
- **Growth-rate and turnover ratios** — Massive's `/ratios` endpoint omits revenue/earnings/FCF growth, asset/inventory turnover, DSO. The adapter leaves those as `None`. Agents that need growth read it from `search_line_items()` across multiple periods and compute it themselves.

## Editing the agent registry

If you add a new agent under `src/agents/`:

1. Add the function + Pydantic schema following the pattern in `src/agents/alpha_seeker.py`.
2. Register it in `src/utils/analysts.py` (import + `ANALYST_CONFIG` entry with a unique `order` number).
3. Add a schema-construction test in `tests/test_custom_agents.py` mirroring the existing tests.
4. If it should appear in a sleeve, edit `src/config/portfolio_config.py` and `pytest tests/test_portfolio_config.py` will catch agent-key typos via the registry cross-check.

## Editing the Massive adapter

The line-item field mapping in [`src/tools/massive/converters.py`](src/tools/massive/converters.py) is a dict (`LINE_ITEM_MAP`). To support a new field name an agent asks for, add one entry to that dict — no other changes needed. If the field has to be computed from multiple statements, add a branch in `_compute_field()` and use the `"computed"` kind.

## Regulatory updates (energy_transition agent)

The IRA and FEOC rule notes live in module-level dicts `IRA_RULE_NOTES` and `FEOC_RULE_NOTES` in [`src/agents/energy_transition.py`](src/agents/energy_transition.py). When Treasury issues a new notice (e.g. 45X clarification, FEOC threshold change), edit those dicts. Each value is one canonical sentence — keep it under one line so the LLM prompt stays cache-friendly.

## Model routing playbook

The project default in `.claude/settings.json` is **Sonnet** + `effortLevel: high`. This balances cost against the high-frequency work (file edits, scaffolding, test runs, type checks) that dominates a typical session in this repo. Opus is reserved for explicit reach-for moments.

**Default to Sonnet for:**
- File edits, refactors, scaffolding new components or routes
- Running tests, lint, type checks; reading their output
- API client wiring, type definitions, glue code
- Diagnostic work where the answer is one or two `Grep` calls away
- Anything the existing patterns in this repo already cover (look at neighboring files first)

**Switch to Opus (`/model opus`) for:**
- Designing a new agent prompt or substantially reworking an existing one (alpha_seeker / energy_transition / emerging_tech)
- Cross-cutting refactors that touch the data adapter + agents + UI in one pass
- Hard debugging where the failure mode isn't obvious from logs (e.g. SSE race conditions, threading issues across `asyncio.to_thread` boundaries)
- Architectural decisions about phases that haven't been planned yet

**Delegation pattern (the bigger win):**

The main session model handles whatever it's set to. Most of the cost optimization comes from **delegating routine work to model-pinned subagents** so the main agent can stay focused. Two are defined in this repo:

| Subagent | Model | Use for |
| --- | --- | --- |
| `explorer` ([.claude/agents/explorer.md](.claude/agents/explorer.md)) | Sonnet | File maps, "where is X defined", grep-style searches, directory summaries. Read-only. Use liberally — it's cheap. |
| `architect` ([.claude/agents/architect.md](.claude/agents/architect.md)) | Opus | Plans only (not code). Multi-file design, prompt rework, hard debug recommendations. Returns trade-offs + recommended approach + self-skepticism. |

**Rule of thumb:** if you'd be tempted to do five `Grep` + `Read` calls in a row to map something out, delegate to `explorer` instead and continue with just the summary. If you're about to spend a turn weighing two architectures, delegate to `architect`, then execute its recommendation yourself.

**What auto-routing does NOT exist:** the harness will never pick a model for you based on task complexity. Routing is your responsibility (`/model` mid-session, the `model:` parameter on `Agent` tool calls, or these subagent definitions).

**Tracking usage:** `/usage` shows total session cost + breakdown by subagent. There is no per-model attribution within a single conversation, but you can infer it from which subagents got called heavily.

## Using ralph-wiggum for iterative work

This repo benefits from the [ralph-wiggum plugin](https://github.com/anthropics/claude-code/tree/main/plugins/ralph-wiggum) for tasks that have a clear pass/fail signal. Pattern:

```
/ralph-loop "Run pytest. If any test fails, fix the implementation (not the test) and re-run. When all 43 tests pass and no new test files have been created, output COMPLETE." --max-iterations 20 --completion-promise "COMPLETE"
```

**Good fits in this repo:**
- "Make tests pass after refactor X" — automated pass/fail signal via `pytest`.
- "Tighten a custom agent's prompt until the schema parses 5/5 runs on a fixture ticker" — runnable verification.
- "Add a new field to `LINE_ITEM_MAP` and prove a Damodaran-style agent now sees it" — adapter + agent verifiable end-to-end.
- "Fix the live NVDA smoke test until it returns a structured signal without errors" — single ticker, cheap to iterate, clear success criterion.

**Bad fits (do not use ralph-loop for):**
- Designing new agent frameworks or prompt structures — needs human taste, not iteration.
- Anything where the success criterion is "does the user like this output?" — subjective, no machine-checkable signal.
- Live runs that cost money per iteration without a hard `--max-iterations` cap.

**Always set:**
- `--max-iterations` (default to 10-20 unless the task is trivially bounded).
- `--completion-promise` with a specific phrase the model only emits when it has actually finished.
- A clear success criterion in the prompt itself — "tests pass" or "scan returns ≥1 high-conviction signal", not "looks good".

## Sleeves Dashboard — what's where

The UI is a single new tab type (`'sleeves'`) that auto-opens on first load. All new dashboard code lives in:

- **Backend**: [`app/backend/routes/sleeves.py`](app/backend/routes/sleeves.py) — config/scan-list/scan-by-date/scan-run/watchlist endpoints. SSE pattern mirrors `/hedge-fund/run`.
- **Backend service**: [`app/backend/services/watchlist_service.py`](app/backend/services/watchlist_service.py) — atomic file rewrite + `importlib.reload` for `src/config/watchlist.py`.
- **Frontend types**: [`app/frontend/src/types/sleeves.ts`](app/frontend/src/types/sleeves.ts) — wire-format mirror of backend responses.
- **Frontend API client**: [`app/frontend/src/services/sleeves-api.ts`](app/frontend/src/services/sleeves-api.ts) — thin `fetch` wrapper; SSE consumer lives in the context.
- **Frontend context**: [`app/frontend/src/contexts/sleeves-context.tsx`](app/frontend/src/contexts/sleeves-context.tsx) — single source of truth for `config / latestScan / scanStatus / liveActivity / watchlist / scanHistory / selectedTicker`. Owns `runScan / stopScan / loadScanByDate / saveWatchlist`.
- **Frontend components**: [`app/frontend/src/components/sleeves/`](app/frontend/src/components/sleeves/) — atomic to composite. `signal-pill` and `traffic-light` are reusable atoms; everything else is a one-place component.

Scan output is dual-written: `outputs/YYYY-MM-DD_morning_scan.csv` (always, for CLI compat) plus `outputs/YYYY-MM-DD_morning_scan.json` (when run via the UI; carries full per-agent raw fields the drill drawer needs). The backend prefers JSON when both exist.

Under `STORAGE_BACKEND=db` the UI scan payload lives in the `scan_results` Postgres table (the source of truth for the dashboard), not the JSON sidecar. The CSV is still written to `outputs/` for CLI compat, but on cloud that directory is ephemeral — it is NOT the source of truth there.

## Storage backend (Phase 2 cutover)

`STORAGE_BACKEND` (default `file`) selects where live app state is stored. `file` = today's local JSON/config files (local app 100% unchanged). `db` = the multi-tenant Postgres repositories in `app/backend/repositories/`. The cloud deploy sets `STORAGE_BACKEND=db`. The dispatch seam is [`app/backend/services/_storage.py`](app/backend/services/_storage.py) — read its module docstring for the cutover recipe before touching a store. Each file service branches on `use_db()` and returns the SAME dict shapes either way (proven by `tests/test_storage_cutover.py`, which exercises every cut-over store under both backends). All rows are owned by `DEFAULT_USER_ID` until Clerk auth (Phase 3).

## What's deferred / known follow-ups

- Sparkline of weighted_score per sleeve over time (needs ≥3 historical scans before it's meaningful).
- Diff highlighting between consecutive scans (needs ≥2 historical scans).
- Wiring `sleeve_attribution.py` to the upstream backtester output format.
- GitHub push (project is local-only on `main` branch).
