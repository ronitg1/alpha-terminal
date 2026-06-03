<div align="center">

# Alpha Terminal

**A research terminal for retail investors. AI agent panels score your book, a realistic options backtester pressure-tests your strategies, and a market-news + earnings-call desk keeps you on top of every name — all from your laptop.**

[![Status: Beta](https://img.shields.io/badge/status-beta-orange.svg)](#roadmap)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Node 18+](https://img.shields.io/badge/node-18+-green.svg)](https://nodejs.org/)
[![Tests](https://img.shields.io/badge/tests-156%20passing-brightgreen.svg)](tests/)
[![Signals only](https://img.shields.io/badge/execution-none-lightgrey.svg)](#what-this-is-not)

</div>

> [!WARNING]
> **🚧 Beta — actively under development.** The five tabs (Market, Screening, Portfolio, News, Calls), the options screener + realistic backtester, and the Finnhub fundamentals integration are working and tested, but the project is pre-`1.0` and the roadmap is open. Expect rough edges and breaking changes between versions. See the [Roadmap](#roadmap). Pin a specific commit if you need stability.

> **Signals only — no trading execution.** Alpha Terminal generates ideas. You decide what to do with them.

---

## Contents

[What it does](#what-it-does) · [Why](#why-this-exists) · [Quick start](#quick-start-5-minutes) · [The dashboard](#the-dashboard-at-a-glance) · [Features](#features) · [Architecture](#architecture) · [Repo layout](#repository-layout) · [Setup](#detailed-setup) · [Troubleshooting](#troubleshooting) · [What it is NOT](#what-this-is-not) · [Roadmap](#roadmap) · [Credits](#credits)

---

## What it does

Alpha Terminal sits between your watchlist and your brokerage. It runs a panel of LLM-based "agent" analysts on your stocks, organizes them into themed sleeves, and gives you the tools to pressure-test ideas before risking capital.

```
┌────────────────────┐    ┌────────────────────┐    ┌────────────────────┐
│  Your tickers      │ ─► │  Agent panel       │ ─► │  Portfolio Pulse   │
│  • sleeves         │    │  • alpha_seeker    │    │  • per-agent cards │
│  • watchlists      │    │  • damodaran       │    │  • Finnhub snapshot│
│  • sector ETFs     │    │  • burry, graham…  │    │  • LLM idea synth  │
└────────────────────┘    └────────────────────┘    └────────────────────┘
       │                                                      │
       ▼                                                      ▼
┌────────────────────┐  ┌────────────────────┐    ┌────────────────────┐
│  Market News +     │  │  11 options        │ ─► │  Realistic options │
│  Earnings Calls    │  │  strategy screener │    │  backtester        │
│  • macro buckets   │  │  • adaptive strikes│    │  • profit/stop/DTE │
│  • 9-section call  │  │  • spread legs     │    │  • slippage model  │
│    breakdown       │  │  • real chains     │    │  • real or BSM     │
└────────────────────┘  └────────────────────┘    └────────────────────┘
```

## Why this exists

Most retail tools fall in two camps:
1. **Charts + indicators** (Thinkorswim, TradingView) — beautiful price data, zero conviction synthesis.
2. **Stock screeners + AI chatbots** — generic summaries, no portfolio context, no backtest.

Alpha Terminal is built for one specific job: **"I'm a serious retail investor with a thesis. Score my book, tell me what's working, let me test a new strategy before I commit."**

---

## Quick start (5 minutes)

```bash
# 1. Clone
git clone https://github.com/ronitg1/alpha-terminal.git
cd alpha-terminal

# 2. Python deps (3.12 + Poetry)
poetry install --no-root

# 3. Frontend deps
cd app/frontend && npm install && cd ../..

# 4. Get API keys:
#    DeepSeek         https://platform.deepseek.com/  (required — LLM, ~$0.05 / agent call)
#    Polygon Stocks   https://polygon.io/  (required — free: 5 req/min, Starter ~$29/mo: unlimited)
#    Finnhub          https://finnhub.io/register  (optional but recommended — free 60/min;
#                     powers the News tab, earnings-beat/insider data, and fills Polygon's
#                     insider + growth/turnover gaps)
#    (optional) Financialdatasets.ai  https://financialdatasets.ai/  for richer ratios
#
# 5. Configure
cp .env.example .env
# edit .env with your keys

# 6. Run (two terminals)
poetry run uvicorn app.backend.main:app --host 127.0.0.1 --port 8000 --reload
cd app/frontend && npm run dev

# 7. Open http://localhost:5173
```

The dashboard opens on the **Market** tab. The left rail lists your sleeves, watchlists, and sector ETFs with live quotes; the top tabs switch between **Market · Screening · Portfolio · News · Calls**. Run a morning scan (`poetry run python -m src.run_morning_scan`) to populate Portfolio Pulse with agent verdicts.

---

## The dashboard at a glance

A three-pane terminal: a **left rail** (sleeves, watchlists, and sector ETFs with live quotes + sparklines + company names), a **main pane** that switches across five tabs, and a context-aware **AI chat** drawer.

| Tab | What it's for |
| --- | --- |
| **Market** | Per-ticker chart (price + volume), company overview, key financials, and a Finnhub fundamentals panel (growth/turnover, analyst consensus, earnings beat/miss, peers, insider flow). |
| **Screening** | Pattern Scanner · 11-strategy Options Screener (with chain viewer + spread-leg highlighting) · the realistic options Backtester. |
| **Portfolio** | Portfolio Pulse — bias/conviction rollup, high-conviction names, LLM memo, and per-name deep dives. |
| **News** | Three-column market-news desk (your book · ticker search · auto-categorized macro) with per-article AI summaries. |
| **Calls** | Earnings-call analysis — paste text / URL / PDF → a 9-section structured breakdown. |

**Portfolio Pulse** is the home base:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  PORTFOLIO PULSE                              2026-05-28  [Run portfolio ▶] │
│  Bullish bias · Net +18% · Conv 67 · 3 high-conviction · ✨ 2 variant         │
├──────────────────────────────────────────────────────────────────────────────┤
│ Allocation       Weighted Conv     High Conv         Watchlist               │
│   100% ✓          67/100             3                12                     │
│   4 sleeves       12 tickers scanned  +2 today        3 unscanned            │
├──────────────────────────────────────────────────────────────────────────────┤
│ 📝 PORTFOLIO THESIS                              [Generate LLM memo] [Expand]│
│ We see a bullish read across the book this scan · 7 bullish vs 2 bearish     │
│ out of 12 scanned. Strongest cluster is Mega Tech (conv 71); softest is      │
│ Emerging Tech (conv 39). ✨ 2 names flagged variant perception · NVDA, CEG    │
├──────────────────────────────────────────────────────────────────────────────┤
│ ⚡ MEGA TECH    🌱 ENERGY TRANS.   🚀 EMERGING TECH   🎯 OPPORTUNISTIC        │
│ 20% Bullish 71  50% Mixed 48      20% Bearish 39    10% — 12 names           │
│ 4↑ · 0↓ · 0=    2↑ · 1↓ · 4=      0↑ · 2↓ · 4=      [Run sleeve ▶] [Edit]    │
├──────────────────────────────────────────────────────────────────────────────┤
│ ⭐ HIGH-CONVICTION SIGNALS                                                   │
│ ┌──────────┬──────────┬──────────┬──────────┐                                │
│ │ NVDA  ✨ │ MSFT     │ CEG  ✨   │ FSLR     │                                │
│ │ $222.50  │ $415.20  │ $241.80  │ $186.40  │                                │
│ │ ▁▂▃▆█ +5%│ ▂▃▄▅▆ +2%│ ▁▃▆█▇+12%│ ▆▅▄▃▂ -3%│                                │
│ │ BUY 78%  │ BUY 64%  │ BUY 81%  │ SELL 58% │                                │
│ └──────────┴──────────┴──────────┴──────────┘                                │
├──────────────────────────────────────────────────────────────────────────────┤
│ POSITIONS                                                                    │
│ ▼ MEGA TECH · 20% · Bullish · 4 tickers           [Run sleeve ▶] [Memo]      │
│   ├ NVDA  $222.50  +3.2% 1D ▁▂▃▆█ BUY 78% ✨        [▶ Run] [▼]              │
│   ├ MSFT  $415.20  +0.8% 1D ▂▃▄▅▆ BUY 64%          [▶ Run] [▼]              │
│   ├ GOOGL $173.40  -1.2% 1D ▆▅▄▃▂ HOLD 41%         [▶ Run] [▼]              │
│   └ META  $585.10  +2.1% 1D ▁▃▄▆█ HOLD 52%         [▶ Run] [▼]              │
│                                                                              │
│ ▶ ENERGY TRANSITION · 50% · Mixed · 11 tickers                               │
│ ▶ EMERGING TECH      · 20% · Bearish · 6 tickers                             │
│ ▶ OPPORTUNISTIC      · 10% · 12 tickers                                      │
└──────────────────────────────────────────────────────────────────────────────┘
```

Click any ticker row to expand its **deep dive** — built for idea generation, not just numbers:

1. **Finnhub snapshot strip** — analyst consensus, earnings beat/miss, growth, margin, P/E, insider flow.
2. **Agent verdict cards** — one per analyst, each with a bull/bear/neutral pill, confidence bar, full reasoning, and (for the custom agents) the edge thesis, catalysts, and kill-switch.
3. **Idea synthesis** — a one-click **Quick take** (fast DeepSeek thesis) or **Deep analysis** (richer, multi-section, pulls recent news), both grounded in the agent signals *and* the Finnhub fundamentals.

---

## Features

### 🎯 Sleeves dashboard

Themed portfolio buckets ("Mega Tech 20% / Energy Transition 50% / Emerging
Tech 20% / Opportunistic 10%"), each scored by its own agent panel. Two-level
synthesis — **deterministic readout** always available, plus a one-click **LLM
PM memo** that writes a 3-paragraph "view of the book" in first-person plural,
PM voice. Cached server-side by scan signature so re-fetching is free.

### 📈 Options screener (11 strategies)

| Strategy | Setup |
|---|---|
| **Weakness** | Lagging QQQ + oversold (bounce calls or continuation puts) |
| **Strength** | Leading QQQ + overbought (breakout calls or mean-reversion puts) |
| **Momentum** | Absolute trend follow, no benchmark |
| **Mean Reversion** | Z-score from 20d mean |
| **Breakout** | Near 52w high + volume confirm |
| **Breakdown** | Near 52w low + volume confirm |
| **Volume Spike** | Unusual volume + big move + close-at-wick |
| **Pullback** | Buy-the-dip-in-uptrend |
| **Trend Bias** | Golden/Death cross context |
| **Vol Expansion** | Realized-vol regime change |
| **Unusual Options Activity** | Live chain vol/OI extremes |

Each strategy ships a **strike + expiry recommendation** that **adapts to your picked expiry** — a +2% OTM call at 7d becomes ~+5% OTM at 50d via √-time strike scaling, same statistical reach across maturities. Click any candidate to drop into the chain viewer (calls/puts, ATM-highlighted) with the recommended contract starred; multi-leg structures (e.g. debit spreads) highlight **both** legs with BUY/SELL tags, and the "Plays" pills jump the chain to each expiry tier.

### 🧪 Backtest engine (two modes)

**Strategy mode** — run any of the 10 backtestable options strategies against the screener's historical signals. Two pricing modes:

- **Real fills (Polygon)** — fetches the actual listed contract closest to the strategy's target strike + expiry (~2.5× hold-days out), then entry/exit at the actual daily close. Falls back to BSM per-trade if the contract or bar is missing.
- **BSM proxy** — Black-Scholes against the underlying's trailing 30-day realized vol. Deterministic, no API calls. Useful for ranking strategies.

**Realistic exit model** — every trade is checked each day and closes on the first trigger: **profit target** (default +50%), **stop-loss** (default −50%), **DTE roll-out** (default 21 DTE, to step out before the gamma/theta cliff), or the **hold-days backstop**. A **slippage** model (default 5% round-trip spread) crosses half the bid/ask on each side, so frictionless win rates don't mislead. The conviction gate is **percentage-based** (magnitude-weighted, not a 0–3 count), the trades table shows the **exact entered contract** (strike + expiry) with entry/exit dates, and the summary breaks trades down by how they closed (target / stop / DTE / expiry / time). A "reality check" banner flags when BSM or frictionless settings are inflating results.

**Sleeves mode** — wraps the LLM agent panel into a backtest. Each trading day, the full agent panel votes; portfolio positions follow the consensus. Equity curve with amber-entry / blue-exit trade markers, closed-trades table with per-agent attribution.

### 📊 Market tab

Click any ticker (left rail, sleeve, or search) to open its detail view: a price + volume chart with a timeframe selector (1W → 2Y), a company overview, the key-financials grid, and a **Finnhub fundamentals panel** — growth/turnover metrics, an analyst-consensus bar, EPS beat/miss history, peers, and recent insider flow. Falls back gracefully (price-only, "—" placeholders) when a data provider is slow or down.

### 🛠 Custom agents

Three custom agents written specifically for this project, in addition to the 19 upstream investor-persona agents:

- **`alpha_seeker`** — sector-agnostic alpha generation. Two-tier framing: STRONG EDGE requires a full variant perception ("Consensus is wrong because X"); DIRECTIONAL LEAN allows lower-conviction reads grounded in momentum + fundamentals + news flow.
- **`energy_transition`** — IRA tax-credit + FEOC compliance scorecard. Allowed to use industry knowledge to infer FEOC status when news flow is silent (e.g., FSLR thin-film → clean; Chinese-cell inverter shops → amber/red).
- **`emerging_tech`** — moat + S-curve + AI-tailwind + valuation scorecard. Calibrated confidence anchors (70-90 for full alignment, 30-50 for thin data).

### 📰 Market News

Three-column news desk: **your book** (headlines fanned across sleeve + watchlist tickers), **ticker search** (news for any symbol), and a **macro feed** auto-categorized into Monetary / Geopolitics / Government / Economy / Energy / Markets via keyword rules. Each article has an **AI summarize** action — 3 bullets + a "why it matters to your book" relevance read grounded in which sleeve holds the related ticker. Finnhub-primary with a Polygon fallback for per-ticker news.

### 🎙 Earnings Call Analysis

Paste a transcript, paste a URL, or upload a PDF; the analysis returns a 9-section structured read: sentiment vs prior quarter, tone delta, key themes with quotes, **hedging-language flags**, **dodged-question detection**, competitive + regulatory (IRA 45X / FEOC / tariff) mentions, and an explicit **thesis-impact verdict** (confirms / strengthens / weakens / breaks). URL extraction uses httpx + BeautifulSoup; PDF parsing uses pypdf.

### 🧠 Per-name analysis (Portfolio Pulse)

Each portfolio name has a **Quick take** (fast DeepSeek thesis) and **Deep analysis** (richer multi-section read that also pulls news). Both are grounded in the saved agent signals **and** Finnhub fundamentals — earnings beat/miss history, growth/turnover, analyst consensus, and insider flow — so the thesis reasons over fundamentals, not just price.

### 🔌 Finnhub free-tier fallback (optional)

When `FINNHUB_API_KEY` is set, Finnhub backfills the two gaps in Massive: **insider (Form 4) transactions** (Massive returns none) and the **growth / turnover / DSO ratios** its `/ratios` endpoint omits. It also enriches the Market tab's financials with a 130-metric fundamentals grid, earnings beat/miss history, analyst consensus, peers, and insider flow. Strictly additive — the app runs unchanged without the key. (Forward analyst estimates are premium-gated and not used.)

---

## Architecture

```mermaid
flowchart LR
  subgraph User["User · Browser"]
    UI[React + Vite · 5 tabs<br/>localhost:5173]
  end

  subgraph Backend["FastAPI Backend · localhost:8000"]
    Routes[/sleeves · news · transcripts<br/>routes · SSE streams/]
    ThesisSvc[Thesis Service<br/>portfolio · sleeve · ticker]
    BacktestSvc[Backtest Service<br/>real-fill + BSM]
  end

  subgraph LLM["LLM Layer"]
    DS[DeepSeek R1 + V3]
    Agents[Custom + upstream<br/>investor-persona agents]
  end

  subgraph Data["Data Providers"]
    PG[Polygon · stocks + options]
    FH[Finnhub · news · fundamentals<br/>insider · rate-limited]
    FDS[(financialdatasets.ai<br/>fallback for ratios)]
  end

  UI -->|SSE + REST| Routes
  Routes --> ThesisSvc
  Routes --> BacktestSvc
  Routes --> Agents
  Agents --> DS
  ThesisSvc --> DS
  Agents -->|prices| PG
  Agents -->|fundamentals| FDS
  Agents -->|insider + growth| FH
  Routes -->|news + enrichment| FH
  BacktestSvc -->|historical chain| PG
  Routes -->|reference + market cap| PG

  classDef user fill:#1e3a5f,stroke:#3b82f6,color:#fff
  classDef backend fill:#3f1d38,stroke:#a855f7,color:#fff
  classDef llm fill:#0a4d3a,stroke:#10b981,color:#fff
  classDef data fill:#5c2a0b,stroke:#f59e0b,color:#fff
  class UI user
  class Routes,ThesisSvc,BacktestSvc backend
  class DS,Agents llm
  class PG,FH,FDS data
```

### Data flow for one ticker scan

```mermaid
sequenceDiagram
  participant U as User
  participant UI as Frontend
  participant BE as Backend
  participant A as Agent Panel
  participant DS as DeepSeek
  participant PG as Polygon
  participant FDS as FDS

  U->>UI: Click "Run portfolio"
  UI->>BE: POST /sleeves/scan/run
  BE-->>UI: SSE: start
  loop For each ticker
    BE->>PG: get_prices, get_news
    PG-->>BE: 2y bars + headlines
    BE->>FDS: get_financial_metrics
    FDS-->>BE: ratios (or fallback to Polygon)
    BE->>A: agent.analyze(ticker, data)
    A->>DS: LLM call (reasoning)
    DS-->>A: structured signal
    A-->>BE: signal + confidence + reasoning
    BE-->>UI: SSE: sleeve_complete (one ticker row)
  end
  BE-->>UI: SSE: complete (full scan)
  UI->>UI: render dashboard
```

### Per-data-type provider routing

The two data providers have different sweet spots. Alpha Terminal routes each data type to the right one, with bidirectional fallbacks:

| Data | Primary | Fallback | Why |
|---|---|---|---|
| Prices | Polygon | FDS | Polygon covers full US universe |
| Company news (tab) | Finnhub | Polygon | Finnhub's per-ticker + macro feeds are richer |
| Market cap | Polygon reference | FDS company facts | Polygon has it on every ticker |
| Fundamentals | FDS | Polygon ratios | FDS covers ratios cheaply; Polygon needs an add-on |
| Growth / turnover ratios | FDS | **Finnhub** `metric/all` | Polygon `/ratios` omits these |
| Insider trades | FDS | **Finnhub** insider-transactions | Polygon doesn't publish them |
| Analyst consensus / beat-miss | Finnhub | — | Free-tier `recommendation` + `earnings` |
| Options chain | Polygon | — | Polygon Options plan only |

Finnhub access is gated behind a single process-wide token-bucket limiter (≈50/min, under the free-tier 60/min ceiling) shared across every caller, so heavy navigation never trips a 429. Forward analyst estimates are premium-gated and intentionally unused. Set neither `DATA_PROVIDER` nor both keys and the routing degrades gracefully — whichever provider you have, the dashboard still renders the data it can.

---

## Repository layout

```
alpha-terminal/
├── README.md                ← you are here
├── ARCHITECTURE.md          ← contributors' deep dive
├── CONTRIBUTING.md
├── ATTRIBUTION.md           ← what came from virattt/ai-hedge-fund
├── LICENSE                  ← MIT
├── .env.example             ← copy + fill in
│
├── src/                     ← Python core
│   ├── agents/                  19 upstream + 3 custom analysts
│   │   ├── alpha_seeker.py          (custom) sector-agnostic alpha
│   │   ├── energy_transition.py     (custom) IRA + FEOC scorecard
│   │   ├── emerging_tech.py         (custom) moat + S-curve + AI
│   │   └── …
│   ├── backtesting/
│   │   ├── options_historical.py    real Polygon fills
│   │   ├── options_proxy.py         BSM walk-forward
│   │   └── sleeve_attribution.py    per-agent + per-sleeve attribution
│   ├── config/
│   │   ├── portfolio_config.py      sleeve definitions
│   │   └── watchlist.py             opportunistic queue
│   ├── tools/
│   │   ├── api.py                   per-type provider routing
│   │   ├── massive/                 Polygon REST client
│   │   └── finnhub/                 Finnhub client (rate-limited) + converters
│   └── run_morning_scan.py          CLI entry point
│
├── app/
│   ├── backend/                 FastAPI
│   │   ├── routes/
│   │   │   ├── sleeves.py               /sleeves/* (config, quotes, screener,
│   │   │   │                            backtest, chat, ticker enrichment, thesis)
│   │   │   ├── news.py                  /news/* (feed, ticker search, summarize)
│   │   │   └── transcripts.py           /transcripts/* (extract, upload, analyze)
│   │   ├── services/                    thesis, sleeve config, watchlists,
│   │   │                                portfolio settings, finnhub_news,
│   │   │                                transcript_analysis
│   │   └── models/                      events + schemas
│   └── frontend/                React + Vite
│       └── src/
│           ├── components/dashboard/    3-pane shell: left-nav, main-content,
│           │                            market-view, portfolio-section, finnhub panels
│           ├── components/sleeves/      screener, chain viewer, backtest
│           ├── components/news/         Market News tab
│           ├── components/transcripts/  Earnings Calls tab
│           ├── contexts/                sleeves + dashboard state
│           └── services/                typed API clients
│
├── tests/                   ← 156 tests, pytest
└── outputs/                 ← scan CSVs + JSON sidecars (gitignored)
```

---

## Detailed setup

### Required: Python 3.12 + Poetry

```bash
# macOS
brew install python@3.12 pipx
pipx install poetry

# Windows
choco install python --version=3.12
pip install pipx
pipx install poetry

# Linux
sudo apt install python3.12 python3.12-venv
curl -sSL https://install.python-poetry.org | python3 -
```

### Required: Node 18+

```bash
# macOS
brew install node

# Windows
choco install nodejs

# Linux
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

### API keys — where to get each

| Key | Required? | What for | How to get |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | ✅ Yes | Agent reasoning (R1) + structured output parsing (V3) | https://platform.deepseek.com → API Keys |
| `MASSIVE_API_KEY` | ✅ Yes | Prices, news, market cap, options chain | https://polygon.io → Dashboard → API Keys |
| `FINANCIAL_DATASETS_API_KEY` | ⚪ Optional | Better fundamentals coverage than Polygon's default | https://financialdatasets.ai → Settings |
| `ANTHROPIC_API_KEY` | ⚪ Optional | Use Claude for the portfolio thesis instead of DeepSeek | https://console.anthropic.com → API Keys |

`DEEPSEEK_API_KEY` + `MASSIVE_API_KEY` is the minimum viable setup. Without FDS, the company-overview cards still render via Polygon's reference endpoint — you just lose the FDS ratio grid for smaller-cap tickers.

### Configuring sleeves

Sleeves are defined in [`src/config/portfolio_config.py`](src/config/portfolio_config.py). The default split is:

```python
PORTFOLIO_SLEEVES = {
    "energy_transition": {
        "allocation_pct": 50.0,
        "agents": ["energy_transition", "aswath_damodaran", "michael_burry"],
        "agent_weights": {"energy_transition": 0.5, "aswath_damodaran": 0.3, "michael_burry": 0.2},
        "tickers": ["FSLR", "CSIQ", "ENPH", "..."],
    },
    "mega_tech": {"...": "..."},
    "emerging_tech": {"...": "..."},
    "opportunistic": {"...": "..."},
}
```

You can edit this file directly, or use the **Manage** button in the dashboard's top bar — it rewrites the file atomically and live-reloads the backend. Allocations must sum to 100%.

---

## Troubleshooting

<details>
<summary><strong>Vite or uvicorn shows "Application startup complete" but new routes 404</strong></summary>

uvicorn's `--reload` is fragile after many rapid file edits. Restart the process:

```bash
# Find the PID, kill, restart
netstat -ano | findstr :8000    # Windows
lsof -i :8000                    # macOS/Linux
```

</details>

<details>
<summary><strong>Blank screen after a tab crashes</strong></summary>

The `<TabErrorBoundary>` should catch this and show "This tab failed to render". If you see a fully white page, it's a pre-mount crash. Reset persisted tab state:

```javascript
// In browser DevTools console
localStorage.clear()
location.reload()
```

</details>

<details>
<summary><strong>Agents say "no momentum, no fundamentals, no news"</strong></summary>

That's the data layer failing, not the agents. Check:
1. `DATA_PROVIDER` in `.env` — if set to `fds`, smaller-cap tickers will return empty. Either unset it or set to `massive`.
2. Polygon plan tier — you need at least **Stocks Advanced** for aggregates + news.
3. The ticker symbol — Polygon uses class-share suffixes for some names (`BRK.B`, `GOOG` vs `GOOGL`).
</details>

<details>
<summary><strong>Options backtest in "real fills" mode shows all trades as synthetic</strong></summary>

You don't have a Polygon Options plan, or Polygon doesn't have historical chain data for the ticker in your date window. The dashboard logs a per-trade fallback to BSM. Switch the toggle to **BSM proxy** for a cleaner result, or upgrade to **Polygon Options Starter** (~$30/mo).
</details>

<details>
<summary><strong>Manage Sleeves dialog: tickers I removed come back</strong></summary>

Fixed in the current version. If you see it on an older build, the cause was a stale auto-open `useEffect` re-injecting from the watchlist. Pull the latest code.
</details>

---

## What this is NOT

- **Not a brokerage.** No trade execution. The agents tell you what they think; you trade through your own broker.
- **Not financial advice.** Open source software written by one person. Use it as a research tool. Backtest your strategies. Risk-manage your positions.
- **Not a guarantee of returns.** The LLMs are pattern-matchers. They are wrong sometimes. Read the `kill_switch` field on every agent verdict for what would invalidate the trade.
- **Not a production multi-user app.** Designed to run on your laptop. The sleeves config + watchlist + scan history are all local files. No auth, no multi-tenancy.

---

## Roadmap

Track via [GitHub issues](https://github.com/ronitg1/alpha-terminal/issues).

**Recently shipped**

- [x] Market News tab (Finnhub-backed, macro auto-categorization, AI summaries)
- [x] Earnings-call analysis tab (paste/URL/PDF → 9-section breakdown)
- [x] Finnhub free-tier integration — insider + growth/turnover backfill, fundamentals enrichment, shared rate limiter
- [x] Realistic options backtester — profit-target / stop / DTE exits + slippage model
- [x] Per-name, per-sleeve, and whole-portfolio LLM thesis in Portfolio Pulse
- [x] Removed the legacy IDE-shell components inherited from the upstream fork

**Up next**

- [ ] 📈 **P&L tracker** — log entries/exits per idea, mark-to-market against live quotes, realized + unrealized P&L per sleeve, and a portfolio equity curve
- [ ] 🗓️ **Earnings calendar** — upcoming report dates across your book (Finnhub `/calendar/earnings`), with pre/post-earnings flags on each ticker
- [ ] 🔔 **Price + signal alerts** — threshold + conviction-change notifications
- [ ] 📊 **Sector heatmap** — relative-strength grid across sleeves and the SPDR sectors
- [ ] 🧾 **Trade journal** — attach notes/rationale to each idea, linked to its agent thesis
- [ ] Trailing / peak-drawdown stop-loss mode in the backtester (currently fixed-% from entry)
- [ ] Sleeve sparkline history + diff highlight vs the previous scan
- [ ] Cost meter — running tally of LLM credits per session
- [ ] Consolidate the two runtime-data dirs (`app/data/` + `app/backend/data/`)

---

## Credits

Built on the shoulders of [`virattt/ai-hedge-fund`](https://github.com/virattt/ai-hedge-fund) (MIT). The 19 upstream investor-persona agents (Warren Buffett, Aswath Damodaran, Stanley Druckenmiller, Ben Graham, Charlie Munger, Michael Burry, Phil Fisher, etc.) come from there essentially unchanged. The custom analysts, the five-tab dashboard, the options screener, the realistic options backtester, the Market News + earnings-call desks, the Finnhub integration, and the per-type data routing are new in this project.

See [ATTRIBUTION.md](ATTRIBUTION.md) for the full diff.

---

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, change it, sell it. Just don't blame me if your trades lose money.
