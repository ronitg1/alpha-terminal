# rg-alpha-engine

Retail alpha-generation engine — a forked, customized take on
[virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund). Signals
only; **no trade execution**.

## What's different from upstream

| Area              | Upstream default                     | rg-alpha-engine                                  |
| ----------------- | ------------------------------------ | ------------------------------------------------ |
| Primary LLM       | GPT-4.1 / OpenAI                     | DeepSeek R1 for reasoning, V3 for cheap parsing  |
| Financial data    | financialdatasets.ai                 | Massive (Polygon.io rebrand), with FDS fallback  |
| Custom agents     | —                                    | (planned) alpha_seeker · energy_transition · emerging_tech |
| Portfolio sleeves | —                                    | (planned) energy 50 / mega-tech 20 / emerging 20 / opportunistic 10 |

This session delivers the **foundation** — the data + LLM plumbing the
custom agents will sit on top of. Custom agents, sleeves, morning scan,
and backtester customization land in follow-up sessions.

## Setup

```powershell
# 1. Python 3.12 + Poetry (already on this machine if you ran the setup)
py -3.12 --version
poetry --version

# 2. Install deps
cd C:\Users\rdpadmin\Desktop\rg-alpha-engine
poetry install --no-root

# 3. Configure env
Copy-Item .env.example .env
# Edit .env and fill in MASSIVE_API_KEY and DEEPSEEK_API_KEY

# 4. Smoke-test imports
poetry run python -c "from src.tools import api; from src.tools.massive import MassiveClient; print('ok')"
```

## Architecture

```
src/
├─ tools/
│  ├─ api.py                   ← public surface used by every agent. Dispatches
│  │                             to Massive or FDS based on DATA_PROVIDER env.
│  └─ massive/
│     ├─ client.py             ← HTTP client (auth, exp backoff, pagination)
│     └─ converters.py         ← Polygon JSON → FDS-shape pydantic models
├─ utils/
│  ├─ llm.py                   ← call_llm + JSON parsing; DeepSeek defaults
│  └─ llm_router.py            ← task-aware model picker + backoff helper
├─ llm/
│  ├─ models.py                ← LLM provider factory (unchanged from upstream)
│  └─ api_models.json          ← model registry; R1/V3 added at the top
├─ agents/                     ← 19 upstream investor-persona agents
├─ data/                       ← pydantic models + cache (unchanged)
└─ graph/                      ← LangGraph state (unchanged)
```

### Data provider switching

The active data backend is chosen by `DATA_PROVIDER`:

* `DATA_PROVIDER=massive` (default) — uses Massive/Polygon. Requires
  `MASSIVE_API_KEY`.
* `DATA_PROVIDER=fds` — uses financialdatasets.ai. Requires
  `FINANCIAL_DATASETS_API_KEY`.

If `DATA_PROVIDER` is unset, the client picks based on which key is in
`.env`.

### Known coverage gaps under Massive

* **Insider trades** — Massive does not publish bulk Form 4 data, so
  `get_insider_trades()` returns `[]`. Agents that depend on it (Burry,
  Sentiment) will still run; they just see no insider data. Switch
  `DATA_PROVIDER=fds` if you need this.
* **Growth-rate and turnover ratios** — Massive's `/ratios` endpoint
  doesn't precompute revenue/earnings/FCF growth, asset/inventory turnover,
  DSO, etc. Those fields land as `None` and agents already handle that.
  Agents that need growth rates compute them from `search_line_items()`
  results across multiple periods.

### DeepSeek routing

`src/utils/llm_router.py` defines a `TaskType` enum and `pick_model(task)`:

* `TaskType.REASONING` → `deepseek-reasoner` (R1) — agent theses, valuation walks
* `TaskType.PARSING` / `TaskType.CHEAP` → `deepseek-chat` (V3) — JSON parsing, summarization

`call_with_backoff(fn, ...)` wraps a callable in exponential backoff with
jitter for DeepSeek's rate-limit behavior under load. The existing
`src/utils/llm.py` `call_llm` already has retry logic; use
`call_with_backoff` when you call the LLM client directly (outside
`call_llm`).

## What's next

Planned phases:

1. ✅ **Foundation** — data layer, LLM defaults, project structure
2. ⬜ **Custom agents** — alpha_seeker, energy_transition, emerging_tech
3. ⬜ **Portfolio config** — sleeves, agent weights, ticker lists
4. ⬜ **Morning scan** — ranked signal table, CSV output, highlights
5. ⬜ **Backtester** — sleeve attribution, win-rate warnings
6. ⬜ **Opportunistic watchlist** — CLI hook for ad-hoc tickers
