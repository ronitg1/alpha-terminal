/**
 * Capture the onboarding walkthrough screenshots.
 *
 * Drives a headless Chrome through each section of the dashboard and saves one
 * PNG per welcome slide into `public/onboarding/`. Used to refresh the
 * first-login walkthrough images whenever the UI changes. Slide -> file mapping
 * mirrors WELCOME_SLIDES in src/components/onboarding/onboarding-steps.tsx:
 *
 *   01-welcome           Portfolio Summary (totals + allocation + movers)
 *   02-layout            Market dashboard with the AI panel open (3 panes)
 *   03-market            Market dashboard scrolled to the S&P 500 heatmap
 *   04-patterns          Pattern Scanner with a real custom scan
 *   05-screening         Options Screener
 *   06-assistant         Single-ticker research page with the AI panel open
 *   07-portfolio         Portfolio Summary scrolled to the 13F ownership panel
 *   07b-paper-trading    Paper Trading (simulated options account)
 *   08-settings          The Settings dialog (API keys)
 *
 * Prerequisites:
 *   1. The backend running with auth off, e.g. on :8123:
 *        AUTH_ENABLED=false DATA_PROVIDER=massive STORAGE_BACKEND=file \
 *          python -m uvicorn app.backend.main:app --port 8123
 *   2. A dev server with auth off AND capture mode on (capture mode renders the
 *      Help/Settings buttons that are otherwise auth-only):
 *        VITE_API_URL=http://localhost:8123 VITE_CAPTURE_MODE=1 npm run dev -- --port 4321
 *   3. Run:  CAPTURE_URL=http://localhost:4321 node scripts/capture-onboarding.mjs
 *
 * There is no brokerage connected locally, so `/portfolio/overview` (and the
 * Settings dialog's per-user endpoints) are mocked in-page with realistic demo
 * data; everything else (heatmap, scans, chains, 13F, Paper Trading) renders
 * live from the backend. First run is slow — the S&P 500 heatmap and the 13F
 * EDGAR pulls warm cold caches.
 *
 * Env overrides:
 *   CAPTURE_URL   — dev server URL (default http://localhost:4321)
 *   CHROME_PATH   — path to a Chrome/Edge executable
 */
import { mkdir, rm } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = resolve(__dirname, '..', 'public', 'onboarding');
const BASE_URL = process.env.CAPTURE_URL ?? 'http://localhost:4321';
const CHROME_PATH =
  process.env.CHROME_PATH ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ─── Mock data ──────────────────────────────────────────────────────────────
 * No brokerage is connected on the capture machine, so /portfolio/overview is
 * served from this demo book (shape mirrors app/backend/services/
 * portfolio_overview.py / src/types/portfolio.ts). The Settings dialog's
 * per-user endpoints are mocked too so the slide shows clean badges instead of
 * error toasts. Everything else hits the real auth-off backend.
 */

/** Build one stock position with internally consistent derived fields. */
function pos(symbol, name, sector, qty, price, avgCost, dayPct, w52lo, w52hi, total) {
  const value = qty * price;
  const basis = qty * avgCost;
  return {
    symbol,
    underlying: symbol,
    name,
    kind: 'stock',
    quantity: qty,
    last_price: price,
    day_change: +((value * dayPct) / 100).toFixed(2),
    day_change_pct: dayPct,
    current_value: +value.toFixed(2),
    pct_of_account: +((value / total) * 100).toFixed(2),
    avg_cost: avgCost,
    cost_basis_total: +basis.toFixed(2),
    total_gain: +(value - basis).toFixed(2),
    total_gain_pct: +(((value - basis) / basis) * 100).toFixed(2),
    sector,
    week52_low: w52lo,
    week52_high: w52hi,
    option_type: null,
    strike: null,
    expiration: null,
  };
}

const TOTAL = 248_733;
const POSITIONS = [
  pos('NVDA', 'NVIDIA Corp', 'Technology', 220, 172.4, 96.1, 1.21, 86.62, 195.62, TOTAL),
  pos('AAPL', 'Apple Inc', 'Technology', 150, 214.3, 168.4, 0.42, 169.21, 237.49, TOTAL),
  pos('MSFT', 'Microsoft Corp', 'Technology', 60, 452.1, 371.0, -0.35, 385.58, 468.35, TOTAL),
  pos('GOOGL', 'Alphabet Inc Class A', 'Communication Services', 110, 186.7, 141.2, 0.88, 142.66, 201.42, TOTAL),
  pos('AMZN', 'Amazon.com Inc', 'Consumer Discretionary', 90, 208.5, 172.3, -0.62, 151.61, 230.08, TOTAL),
  pos('TSLA', 'Tesla Inc', 'Consumer Discretionary', 45, 262.4, 244.6, 2.31, 138.8, 488.54, TOTAL),
  pos('FSLR', 'First Solar Inc', 'Energy', 120, 168.9, 152.2, 1.74, 135.88, 306.77, TOTAL),
  pos('ENPH', 'Enphase Energy Inc', 'Energy', 160, 42.15, 61.8, -1.18, 32.41, 141.63, TOTAL),
  pos('JPM', 'JPMorgan Chase & Co', 'Financials', 70, 244.6, 187.3, 0.29, 179.2, 254.31, TOTAL),
  pos('XOM', 'Exxon Mobil Corp', 'Energy', 100, 112.8, 104.5, -0.44, 97.8, 126.34, TOTAL),
  {
    symbol: 'NVDA 07/17/2026 200.00 C',
    underlying: 'NVDA',
    name: 'NVDA $200 Call 7/17/26',
    kind: 'option',
    quantity: 2,
    last_price: 4.85,
    day_change: 42.0,
    day_change_pct: 4.52,
    current_value: 970.0,
    pct_of_account: 0.39,
    avg_cost: 4.8,
    cost_basis_total: 960.0,
    total_gain: 10.0,
    total_gain_pct: 1.04,
    sector: 'Options',
    week52_low: null,
    week52_high: null,
    option_type: 'call',
    strike: 200,
    expiration: '2026-07-17',
  },
];

const DEMO_ACCOUNT = {
  id: 'snaptrade:demo',
  label: 'Fidelity Individual',
  source: 'snaptrade',
  institution: 'Fidelity',
  cash: 12_450.22,
  total_value: TOTAL,
  day_change: 1_830.45,
  day_change_pct: 0.74,
  total_gain: 46_210.35,
  total_gain_pct: 22.82,
  positions: [...POSITIONS].sort((a, b) => (b.current_value ?? 0) - (a.current_value ?? 0)),
};

/** pathname (exact match) -> JSON body served instead of the network. */
const MOCK_ROUTES = {
  '/portfolio/overview': {
    connected: true,
    sources: ['snaptrade'],
    accounts: [DEMO_ACCOUNT],
    combined: null,
  },
  '/api-keys': [
    { provider: 'deepseek', has_key: true },
    { provider: 'openrouter', has_key: false },
    { provider: 'massive', has_key: false },
    { provider: 'finnhub', has_key: false },
    { provider: 'robinhood', has_key: false },
  ],
  '/access/me': { is_owner: false, shared_data_approved: true, request_status: null },
  '/user-settings/model': {
    model_provider: 'DeepSeek',
    model_name: 'deepseek-reasoner',
    preference_saved: true,
  },
  '/language-models': { models: [] },
};

/* ─── Page helpers ─────────────────────────────────────────────────────────── */

/** Click a top-level section button (Market / Screening / Portfolio / Paper Trading / ...). */
async function clickNav(page, label) {
  await page.evaluate((lbl) => {
    const nav = document.querySelector('[data-tour="nav-sections"]');
    const btn = [...(nav?.querySelectorAll('button') ?? [])].find(
      (b) => b.textContent.trim() === lbl,
    );
    btn?.click();
  }, label);
}

/** Click a button anywhere on the page by its exact text (used for sub-tabs). */
async function clickByText(page, label) {
  await page.evaluate((lbl) => {
    const btn = [...document.querySelectorAll('button')].find(
      (b) => b.textContent.trim() === lbl,
    );
    btn?.click();
  }, label);
}

/**
 * Wait until the page's visible text contains `needle`. Matched lowercased —
 * several panel headers are CSS-uppercased and innerText reflects that.
 */
async function waitForText(page, needle, timeout = 30_000) {
  await page
    .waitForFunction(
      (t) => document.body.innerText.toLowerCase().includes(t),
      { timeout },
      needle.toLowerCase(),
    )
    .catch(() => console.warn(`  (text "${needle}" not seen within ${timeout / 1000}s)`));
}

/** Wait until a loading placeholder ("Loading news…", "loading strategies…") clears. */
async function waitForGone(page, needle, timeout = 60_000) {
  await page
    .waitForFunction(
      (t) => !document.body.innerText.toLowerCase().includes(t),
      { timeout },
      needle.toLowerCase(),
    )
    .catch(() => console.warn(`  ("${needle}" still on screen after ${timeout / 1000}s)`));
}

/** Scroll the first element whose innerText contains `needle` into view. */
async function scrollToText(page, needle, block = 'start') {
  await page.evaluate(
    (t, blk) => {
      const all = [...document.querySelectorAll('span, div, h1, h2, h3, p')];
      const el = all.find((e) => e.innerText?.toLowerCase().startsWith(t));
      el?.scrollIntoView({ block: blk });
    },
    needle.toLowerCase(),
    block,
  );
  await sleep(600);
}

/** Open or close the right-hand AI assistant panel. */
async function setChatOpen(page, want) {
  await page.evaluate((w) => {
    const btn = document.querySelector('[data-tour="ai-assistant"]');
    const open = btn?.className.includes('text-primary');
    if (!!open !== w) btn?.click();
  }, want);
  await sleep(600);
}

/** The Market dashboard's heatmap has rendered ≥30 tiles (buttons with a "·" title). */
async function waitForHeatmap(page, timeout = 120_000) {
  await page
    .waitForFunction(
      () => document.querySelectorAll('button[title*="·"]').length >= 30,
      { timeout },
    )
    .catch(() => console.warn('  heatmap tiles did not render in time'));
}

/** Expand a watchlist and select its first ticker so Market shows detail. */
async function selectFirstTicker(page) {
  await page
    .waitForFunction(
      () => {
        const wl = document.querySelector('[data-tour="watchlists"]');
        return !!wl && [...wl.querySelectorAll('button')].some((b) =>
          /\b(Market Cap Leaders|QQQ|Semi|Financials|Energy)\b/.test(b.textContent),
        );
      },
      { timeout: 20_000 },
    )
    .catch(() => {});

  await page.evaluate(() => {
    const wl = document.querySelector('[data-tour="watchlists"]');
    const btns = [...(wl?.querySelectorAll('button') ?? [])];
    const group =
      btns.find((b) => /Market Cap Leaders/i.test(b.textContent)) ||
      btns.find((b) => /\b(QQQ|Semi|Financials|Energy)\b/.test(b.textContent));
    group?.click();
  });

  // Ticker rows are indented (class "pl-6"); they render before quotes load.
  await page
    .waitForFunction(
      () => {
        const wl = document.querySelector('[data-tour="watchlists"]');
        return (
          !!wl &&
          [...wl.querySelectorAll('button')].some((b) => b.className.includes('pl-6'))
        );
      },
      { timeout: 15_000 },
    )
    .catch(() => {});

  const ticker = await page.evaluate(() => {
    const wl = document.querySelector('[data-tour="watchlists"]');
    const row = [...(wl?.querySelectorAll('button') ?? [])].find((b) =>
      b.className.includes('pl-6'),
    );
    if (row) {
      row.click();
      const sym = row.querySelector('.font-mono')?.textContent ?? row.textContent;
      return sym.trim().slice(0, 6);
    }
    return null;
  });
  return ticker;
}

async function shoot(page, file) {
  const path = resolve(OUT_DIR, file);
  await page.screenshot({ path, type: 'png' });
  console.log('  saved', file);
}

/* ─── Main ─────────────────────────────────────────────────────────────────── */

async function main() {
  await mkdir(OUT_DIR, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME_PATH,
    headless: 'new',
    defaultViewport: { width: 1280, height: 720, deviceScaleFactor: 1.5 },
    args: ['--hide-scrollbars', '--force-color-profile=srgb'],
  });
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(30_000);

    // Serve the mock routes from inside the page: intercept window.fetch by
    // pathname (exact match, trailing slash ignored) and pass everything else
    // through to the real backend.
    await page.evaluateOnNewDocument((routes) => {
      const realFetch = window.fetch.bind(window);
      window.fetch = (input, init) => {
        try {
          const raw = typeof input === 'string' ? input : input.url;
          const path = new URL(raw, location.origin).pathname.replace(/\/$/, '');
          if (Object.prototype.hasOwnProperty.call(routes, path)) {
            return Promise.resolve(
              new Response(JSON.stringify(routes[path]), {
                status: 200,
                headers: { 'Content-Type': 'application/json' },
              }),
            );
          }
        } catch {
          /* fall through to the real fetch */
        }
        return realFetch(input, init);
      };
    }, MOCK_ROUTES);

    console.log('Loading', BASE_URL);
    await page.goto(BASE_URL, { waitUntil: 'networkidle2' });
    await page.waitForSelector('[data-tour="nav-sections"]', { timeout: 30_000 });
    await sleep(2500); // let quotes + initial data settle

    console.log('Capturing slides...');

    // 02 layout — Market dashboard with all three panes visible. The S&P 500
    // heatmap is slow on a cold cache, so this doubles as the warm-up wait.
    await clickNav(page, 'Market');
    await waitForHeatmap(page);
    await waitForGone(page, 'Loading...', 60_000); // top performers / laggards
    await setChatOpen(page, true);
    await sleep(1500);
    await shoot(page, '02-layout.png');

    // 03 market — the dashboard at full width, heatmap front and center.
    await setChatOpen(page, false);
    await scrollToText(page, 'market heatmap');
    await sleep(800);
    await shoot(page, '03-market.png');

    // 04 patterns — Screening / Pattern Scanner, with a real (small, fast)
    // custom scan so the slide shows detected patterns instead of an empty form.
    await clickNav(page, 'Screening');
    await sleep(800);
    await clickByText(page, 'Pattern Scanner');
    await sleep(1500);
    await clickByText(page, 'Custom');
    try {
      await page.waitForSelector('textarea[placeholder^="AAPL"]', { timeout: 8000 });
      await page.type(
        'textarea[placeholder^="AAPL"]',
        'AAPL MSFT NVDA GOOGL AMZN META TSLA AVGO AMD NFLX CRM ORCL',
      );
      await sleep(300);
      await page.evaluate(() => {
        const btn = [...document.querySelectorAll('button')].find((b) =>
          b.textContent.trim().startsWith('Run Scan'),
        );
        btn?.click();
      });
      await page.waitForFunction(() => document.body.innerText.includes('Total Signals'), {
        timeout: 90_000,
      });
      await waitForGone(page, 'Scanning...', 90_000);
      await waitForGone(page, 'Loading win rates', 60_000);
      await sleep(1500);
    } catch {
      console.warn('  scan produced no results in time — capturing the scanner form');
    }
    await shoot(page, '04-patterns.png');

    // 05 screening — Options Screener (wait for strategies + ranked candidates)
    await clickByText(page, 'Options Screener');
    await waitForGone(page, 'loading strategies', 60_000);
    await sleep(4000);
    await shoot(page, '05-screening.png');

    // 06 assistant — single-ticker research page with the AI panel open. The
    // first ticker fetch pulls ~500 daily bars on a cold cache, so wait for the
    // chart canvas to actually render.
    await clickNav(page, 'Market');
    await sleep(800);
    const ticker = await selectFirstTicker(page);
    console.log('  selected ticker:', ticker ?? '(none found)');
    try {
      await page.waitForSelector('main canvas, .max-w-4xl canvas, canvas', { timeout: 30_000 });
    } catch {
      console.warn('  chart canvas did not appear within 30s');
    }
    await setChatOpen(page, true);
    await sleep(2000);
    await shoot(page, '06-assistant.png');
    await setChatOpen(page, false);

    // 01 welcome + 07 portfolio — the mocked demo book. The Summary top (totals,
    // allocation, movers) is the welcome hero; the 13F ownership panel (live
    // EDGAR, slow on a cold cache) is the portfolio slide.
    await clickNav(page, 'Portfolio');
    await waitForText(page, 'Top & bottom movers', 30_000);
    await waitForGone(page, 'Loading news', 60_000);
    await sleep(2000);
    await shoot(page, '01-welcome.png');

    await waitForText(page, 'Institutional ownership', 30_000);
    await page
      .waitForFunction(
        () => !document.body.innerText.toLowerCase().includes('pulling 13f filings'),
        { timeout: 180_000 },
      )
      .catch(() => console.warn('  13F pull did not finish in time'));
    await scrollToText(page, 'institutional ownership', 'end');
    await sleep(800);
    await shoot(page, '07-portfolio.png');

    // 07b paper trading — the simulated options account (local pnl data).
    await clickNav(page, 'Paper Trading');
    await sleep(3000);
    await shoot(page, '07b-paper-trading.png');

    // 08 settings — the Settings dialog (needs VITE_CAPTURE_MODE=1, which
    // renders the auth-only Settings button with the per-user endpoints mocked).
    const hasSettings = await page.$('[data-tour="settings"]');
    if (hasSettings) {
      await page.click('[data-tour="settings"]');
      await waitForText(page, 'API keys', 10_000);
      await sleep(1200);
      await shoot(page, '08-settings.png');
    } else {
      console.warn(
        '  [data-tour="settings"] not found — is the dev server running with VITE_CAPTURE_MODE=1?',
      );
    }

    // The pre-1.14 pipeline wrote 07-portfolio-pnl.png; it is superseded by
    // 07-portfolio.png + 07b-paper-trading.png.
    await rm(resolve(OUT_DIR, '07-portfolio-pnl.png'), { force: true });

    console.log('Done. Screenshots in', OUT_DIR);
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
