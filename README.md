<div align="center">

# Alpha Terminal

**A research dashboard for retail investors. AI agent panels score your stocks, a real-fill options backtester checks your strategies, and you run it all from your laptop.**

[![Status: Beta](https://img.shields.io/badge/status-beta-orange.svg)](#roadmap)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Node 18+](https://img.shields.io/badge/node-18+-green.svg)](https://nodejs.org/)
[![Tests](https://img.shields.io/badge/tests-108%20passing-brightgreen.svg)](tests/)
[![Signals only](https://img.shields.io/badge/execution-none-lightgrey.svg)](#what-this-is-not)

</div>

> [!WARNING]
> **🚧 Beta — actively under development.** Core features (Sleeves dashboard, options screener, backtest engine, My Stocks) are working and tested, but the project is on `v0.1.0` and the roadmap is open. Expect rough edges, breaking changes between versions, and missing pieces. See the [Roadmap](#roadmap) for what's coming. Pin a specific commit if you need stability.

> **Signals only — no trading execution.** Alpha Terminal generates ideas. You decide what to do with them.

---

## What it does

Alpha Terminal sits between your watchlist and your brokerage. It runs a panel of LLM-based "agent" analysts on your stocks, organizes them into themed sleeves, and gives you the tools to pressure-test ideas before risking capital.

```
┌────────────────────┐    ┌────────────────────┐    ┌────────────────────┐
│  Your tickers      │ ─► │  Agent panel       │ ─► │  Visual dashboard  │
│  • sleeves         │    │  • alpha_seeker    │    │  • bias + conv     │
│  • watchlist       │    │  • damodaran       │    │  • LLM PM memo     │
│  • my stocks       │    │  • burry, graham…  │    │  • per-ticker      │
└────────────────────┘    └────────────────────┘    └────────────────────┘
                                                              │
                                                              ▼
                          ┌────────────────────┐    ┌────────────────────┐
                          │  Backtest before   │ ◄─ │  11 options        │
                          │  you buy           │    │  strategy screener │
                          │  • real fills      │    │  • adaptive strikes│
                          │  • per-strategy    │    │  • per-card chart  │
                          └────────────────────┘    └────────────────────┘
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

# 4. Get API keys (free tiers work):
#    DeepSeek         https://platform.deepseek.com/  (LLM, ~$0.05 / agent call)
#    Polygon Stocks   https://polygon.io/  (free tier: 5 req/min, paid: ~$30/mo for full features)
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

The Sleeves dashboard auto-loads on first open. Use the watchlist button (top-right) to add tickers, then click **Run portfolio** to score them.

---

## The dashboard at a glance

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

Click any ticker row to expand inline: 90-day price chart with timeframe selector
(1W / 1M / 3M / 6M / YTD / 1Y / 2Y), a 2-sentence company overview from
Polygon's reference data, the full key-financials TTM grid (P/E, margins, ROE,
debt/equity, …), and the full per-agent thesis with each analyst's reasoning,
catalysts, kill-switch, and variant perception.

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

Each strategy ships a **strike + expiry recommendation** that **adapts to your picked expiry** — a +2% OTM call at 7d becomes ~+5% OTM at 50d via √-time strike scaling, same statistical reach across maturities. Click any row to drop into the chain viewer with the recommended contract highlighted.

### 🧪 Backtest engine (two modes)

**Strategy mode** — run any of the 10 backtestable options strategies against the screener's historical signals. Two pricing modes:

- **Real fills (Polygon)** — fetches the actual listed contract closest to the strategy's target strike + expiry (~2.5× hold-days out), then entry/exit at the actual daily close. Falls back to BSM per-trade if the contract or bar is missing.
- **BSM proxy** — Black-Scholes against the underlying's trailing 30-day realized vol. Deterministic, no API calls. Useful for ranking strategies.

Plus a **per-contract stop-loss** (toggleable off / −25% / −40% / −50% / −75%) that scans daily closes and exits early on the first breach.

**Sleeves mode** — wraps the LLM agent panel into a backtest. Each trading day, the full agent panel votes; portfolio positions follow the consensus. Equity curve with amber-entry / blue-exit trade markers, closed-trades table with per-agent attribution.

### 📊 My Stocks dashboard

Editable per-card price + sparkline + timeframe selector. Each card persists its own timeframe in localStorage so you can have NVDA on 2Y while MSFT shows 1M. Add tickers via a single input; reorder via up/down arrows; remove via trash icon. Click "Overview" on any card to reveal the 2-sentence company description + key financials grid.

### 🛠 Custom agents

Three custom agents written specifically for this project, in addition to the 19 upstream investor-persona agents:

- **`alpha_seeker`** — sector-agnostic alpha generation. Two-tier framing: STRONG EDGE requires a full variant perception ("Consensus is wrong because X"); DIRECTIONAL LEAN allows lower-conviction reads grounded in momentum + fundamentals + news flow.
- **`energy_transition`** — IRA tax-credit + FEOC compliance scorecard. Allowed to use industry knowledge to infer FEOC status when news flow is silent (e.g., FSLR thin-film → clean; Chinese-cell inverter shops → amber/red).
- **`emerging_tech`** — moat + S-curve + AI-tailwind + valuation scorecard. Calibrated confidence anchors (70-90 for full alignment, 30-50 for thin data).

---

## Architecture

```mermaid
flowchart LR
  subgraph User["User · Browser"]
    UI[React + Vite UI<br/>localhost:5173]
  end

  subgraph Backend["FastAPI Backend · localhost:8000"]
    Routes[/sleeves/* routes<br/>SSE event streams/]
    ThesisSvc[Thesis Service<br/>cache by scan signature]
    BacktestSvc[Backtest Service<br/>real-fill + BSM]
  end

  subgraph LLM["LLM Layer"]
    DS[DeepSeek R1 + V3]
    Agents[Custom + upstream<br/>investor-persona agents]
  end

  subgraph Data["Data Providers"]
    PG[Polygon · Stocks + Options]
    FDS[(financialdatasets.ai<br/>fallback for ratios)]
  end

  UI -->|SSE + REST| Routes
  Routes --> ThesisSvc
  Routes --> BacktestSvc
  Routes --> Agents
  Agents --> DS
  ThesisSvc --> DS
  Agents -->|prices + news| PG
  Agents -->|fundamentals| FDS
  BacktestSvc -->|historical chain| PG
  Routes -->|reference + market cap| PG

  classDef user fill:#1e3a5f,stroke:#3b82f6,color:#fff
  classDef backend fill:#3f1d38,stroke:#a855f7,color:#fff
  classDef llm fill:#0a4d3a,stroke:#10b981,color:#fff
  classDef data fill:#5c2a0b,stroke:#f59e0b,color:#fff
  class UI user
  class Routes,ThesisSvc,BacktestSvc backend
  class DS,Agents llm
  class PG,FDS data
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
| Company news | Polygon | FDS | Polygon's news feed is fresher |
| Market cap | Polygon reference | FDS company facts | Polygon has it on every ticker |
| Fundamentals | FDS | Polygon ratios | FDS covers ratios cheaply; Polygon needs an add-on |
| Line items | FDS | Polygon financials | Same reason |
| Insider trades | FDS only | — | Polygon doesn't publish them |
| Options chain | Polygon | — | Polygon Options plan only |

Set neither `DATA_PROVIDER` nor both keys and the routing degrades gracefully — whichever provider you have, the dashboard still renders the data it can.

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
│   │   └── massive/                 Polygon REST client
│   └── run_morning_scan.py          CLI entry point
│
├── app/
│   ├── backend/                 FastAPI
│   │   ├── routes/sleeves.py        every /sleeves/* endpoint
│   │   ├── services/                thesis, watchlist, sleeve config
│   │   └── models/                  events + schemas
│   └── frontend/                React + Vite
│       └── src/components/sleeves/  the entire UI lives here
│
├── tests/                   ← 108 tests, pytest
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

Track via [GitHub issues](https://github.com/ronitg1/alpha-terminal/issues). The shortlist:

- [ ] Earnings calendar integration (12th screener strategy)
- [ ] Trailing / peak drawdown stop-loss mode (currently only fixed-% from entry)
- [ ] Sleeve sparkline history (needs ≥3 historical scans before it's meaningful)
- [ ] Diff highlight vs previous scan
- [ ] Cost meter — running tally of LLM credits per session
- [ ] Per-agent backtest customization

---

## Credits

Built on the shoulders of [`virattt/ai-hedge-fund`](https://github.com/virattt/ai-hedge-fund) (MIT). The 19 upstream investor-persona agents (Warren Buffett, Aswath Damodaran, Stanley Druckenmiller, Ben Graham, Charlie Munger, Michael Burry, Phil Fisher, etc.) come from there essentially unchanged. The custom analysts, sleeves dashboard, options screener, real-fill backtest engine, My Stocks tab, and per-type data routing are new in this project.

See [ATTRIBUTION.md](ATTRIBUTION.md) for the full diff.

---

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, change it, sell it. Just don't blame me if your trades lose money.
