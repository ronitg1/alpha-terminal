# Attribution

Alpha Terminal is a derivative work of [`virattt/ai-hedge-fund`](https://github.com/virattt/ai-hedge-fund) (MIT, © 2024 Virat Singh). This file lists what came from upstream and what's net-new in this project.

---

## Inherited from upstream

The following code is from the upstream project, used essentially unchanged (light modifications to fit the new data layer):

### Investor-persona agents

19 of the 22 agents come from upstream. Each one is a self-contained pydantic-schema + LangGraph node modeled after a famous investor's style.

- Aswath Damodaran
- Bill Ackman
- Cathie Wood
- Charlie Munger
- Fundamentals analyst
- Michael Burry
- Mohnish Pabrai
- Peter Lynch
- Phil Fisher
- Rakesh Jhunjhunwala
- Stanley Druckenmiller
- Sentiment analyst
- Technical analyst
- Valuation analyst
- Warren Buffett
- … and a few smaller utility agents

Files: [`src/agents/*.py`](src/agents/) (except the three custom ones below).

### LLM plumbing

- [`src/utils/llm.py`](src/utils/llm.py) — `call_llm` retry + structured-output helper
- [`src/llm/models.py`](src/llm/models.py) — model factory
- [`src/utils/progress.py`](src/utils/progress.py) — colored CLI progress reporting
- [`src/cli/input.py`](src/cli/input.py) — interactive CLI prompts

### Data models

- [`src/data/models.py`](src/data/models.py) — pydantic models (Price, FinancialMetrics, LineItem, …)
- [`src/data/cache.py`](src/data/cache.py) — in-process LRU cache

### Backtest engine core

- [`src/backtesting/`](src/backtesting/) — the LangGraph-style daily-loop backtest engine
- [`app/backend/services/backtest_service.py`](app/backend/services/backtest_service.py) — async wrapper around the engine

### Frontend chrome

- [`app/frontend/src/components/Layout.tsx`](app/frontend/src/components/Layout.tsx) — sidebar + tab-bar shell
- [`app/frontend/src/components/tabs/`](app/frontend/src/components/tabs/) — tab bar primitives
- [`app/frontend/src/components/ui/`](app/frontend/src/components/ui/) — Shadcn primitives
- [`app/frontend/src/contexts/{flow,layout,node,tabs}-context.tsx`](app/frontend/src/contexts/) — workspace plumbing

### Upstream agent flow UI

- [`app/frontend/src/components/Flow.tsx`](app/frontend/src/components/Flow.tsx) — agent graph editor
- [`app/frontend/src/nodes/`](app/frontend/src/nodes/) — graph node types

---

## New in Alpha Terminal

The following are written specifically for this project:

### Custom agents

- [`src/agents/alpha_seeker.py`](src/agents/alpha_seeker.py) — sector-agnostic alpha generation with two-tier framing (STRONG EDGE / DIRECTIONAL LEAN), explicit confidence calibration anchors.
- [`src/agents/energy_transition.py`](src/agents/energy_transition.py) — IRA tax-credit + FEOC compliance scorecard. Industry-knowledge FEOC inference when news flow is silent.
- [`src/agents/emerging_tech.py`](src/agents/emerging_tech.py) — moat + S-curve + AI-tailwind + valuation scorecard.

### Sleeves dashboard

The entire Sleeves dashboard is new:

- Backend endpoints: `/sleeves/*` in [`app/backend/routes/sleeves.py`](app/backend/routes/sleeves.py) (~3,400 lines, all new)
- Backend services: [`thesis_service.py`](app/backend/services/thesis_service.py), [`sleeve_config_service.py`](app/backend/services/sleeve_config_service.py), [`watchlist_service.py`](app/backend/services/watchlist_service.py)
- Frontend components: [`app/frontend/src/components/sleeves/`](app/frontend/src/components/sleeves/) — ~25 new components

### Options screener + backtest

- Backend: 11-strategy registry (`_STRATEGY_REGISTRY` in routes/sleeves.py), live chain endpoint, real-fill backtest engine
- Python: [`src/backtesting/options_historical.py`](src/backtesting/options_historical.py) (Polygon historical fills) + [`src/backtesting/options_proxy.py`](src/backtesting/options_proxy.py) (BSM walk-forward)
- Frontend: [`app/frontend/src/components/sleeves/options/`](app/frontend/src/components/sleeves/options/) and [`backtest/`](app/frontend/src/components/sleeves/backtest/)

### My Stocks dashboard

- [`app/frontend/src/components/stocks/`](app/frontend/src/components/stocks/) — editable per-card list with localStorage persistence

### Polygon (Massive) data adapter

- [`src/tools/massive/`](src/tools/massive/) — REST client + JSON → FDS-shape converters
- [`src/tools/api.py`](src/tools/api.py) — `_provider_for(data_type)` per-type routing (this file was upstream; the routing layer is new)

### Config files

- [`src/config/portfolio_config.py`](src/config/portfolio_config.py) — sleeve definitions
- [`src/config/watchlist.py`](src/config/watchlist.py) — opportunistic queue
- [`src/utils/analysts.py`](src/utils/analysts.py) — analyst metadata registry (extended from upstream stub)

### Documentation

- [`README.md`](README.md), [`ARCHITECTURE.md`](ARCHITECTURE.md), [`CONTRIBUTING.md`](CONTRIBUTING.md), this file

### Tests

The custom-agent + sleeve-config + Massive client + options backtest tests are new. The rate-limiting tests are upstream (with one mock fixup for the renamed `_make_fds_request`).

---

## License compliance

Both upstream and this project are MIT-licensed. The [`LICENSE`](LICENSE) file in this repo carries:

1. The Alpha Terminal copyright + MIT terms
2. A clear note that upstream MIT terms apply to the portions of code that came from `virattt/ai-hedge-fund`

This satisfies the upstream MIT requirement to retain the copyright + license notice for the inherited code. If you fork or redistribute Alpha Terminal, keep both notices intact.

---

## Maintenance philosophy

Upstream code is updated rarely — only when a bug fix or schema change in `virattt/ai-hedge-fund` is needed. Most new investment happens in:

1. **Custom agents** (`src/agents/alpha_seeker.py`, `energy_transition.py`, `emerging_tech.py`)
2. **Sleeves dashboard** (`app/backend/routes/sleeves.py` + `app/frontend/src/components/sleeves/`)
3. **Backtest engines** (`src/backtesting/options_*.py`)
4. **Data adapter** (`src/tools/api.py` + `src/tools/massive/`)

If you're contributing, focus PRs on those four areas. Upstream-inherited files should change rarely and only when there's a clear reason.
