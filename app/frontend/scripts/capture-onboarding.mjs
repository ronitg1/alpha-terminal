/**
 * Capture the onboarding walkthrough screenshots.
 *
 * Drives a headless Chrome through each section of the dashboard and saves a PNG
 * per slide into `public/onboarding/`. Used to refresh the first-login
 * walkthrough images whenever the UI changes.
 *
 * Prerequisites (the npm script `capture:onboarding` wires these up):
 *   1. The backend running on :8000 (it serves owner data with auth off).
 *   2. A dev server running with auth DISABLED so the headless browser can reach
 *      the dashboard without a Clerk login:
 *        npm run dev -- --mode capture --port 5199
 *      (`.env.capture.local` sets VITE_AUTH_ENABLED=0 for that mode.)
 *
 * Env overrides:
 *   CAPTURE_URL   — dev server URL (default http://localhost:5199)
 *   CHROME_PATH   — path to a Chrome/Edge executable
 */
import { mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = resolve(__dirname, '..', 'public', 'onboarding');
const BASE_URL = process.env.CAPTURE_URL ?? 'http://localhost:5199';
const CHROME_PATH =
  process.env.CHROME_PATH ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Click a top-level section button (Market / Screening / Portfolio / ...). */
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

/** Expand a watchlist and select its first ticker so Market shows detail. */
async function selectFirstTicker(page) {
  // Wait for the watchlist groups to render (they arrive after the config fetch).
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

  // Expand a group with liquid names.
  await page.evaluate(() => {
    const wl = document.querySelector('[data-tour="watchlists"]');
    const btns = [...(wl?.querySelectorAll('button') ?? [])];
    const group =
      btns.find((b) => /Market Cap Leaders/i.test(b.textContent)) ||
      btns.find((b) => /\b(QQQ|Semi|Financials|Energy)\b/.test(b.textContent));
    group?.click();
  });

  // Ticker rows are indented (class "pl-6"); wait for them to render. This is
  // independent of price loading, so it succeeds even before quotes arrive.
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

/** Ensure the right-hand AI assistant panel is open. */
async function ensureChatOpen(page) {
  await page.evaluate(() => {
    const btn = document.querySelector('[data-tour="ai-assistant"]');
    const open = btn?.className.includes('text-primary');
    if (!open) btn?.click();
  });
}

async function shoot(page, file) {
  const path = resolve(OUT_DIR, file);
  await page.screenshot({ path, type: 'png' });
  console.log('  saved', file);
}

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
    console.log('Loading', BASE_URL);
    await page.goto(BASE_URL, { waitUntil: 'networkidle2' });
    await page.waitForSelector('[data-tour="nav-sections"]', { timeout: 30_000 });
    await sleep(2500); // let quotes + initial data settle

    console.log('Capturing slides...');

    // 01 overview — Portfolio Pulse
    await clickNav(page, 'Portfolio');
    await sleep(2500);
    await shoot(page, '01-welcome.png');

    // 02 layout — Market management panel (clean three-pane view)
    await clickNav(page, 'Market');
    await sleep(1500);
    await shoot(page, '02-layout.png');

    // 03 market — single-ticker detail with chart. The first ticker fetch pulls
    // ~500 daily bars + details from the data provider and can take many
    // seconds on a cold cache, so wait for the chart canvas to actually render.
    const ticker = await selectFirstTicker(page);
    console.log('  selected ticker:', ticker ?? '(none found)');
    try {
      await page.waitForSelector('main canvas, .max-w-4xl canvas, canvas', { timeout: 30_000 });
    } catch {
      console.warn('  chart canvas did not appear within 30s');
    }
    await sleep(2500);
    await shoot(page, '03-market.png');

    // 04 patterns — Screening / Pattern Scanner
    await clickNav(page, 'Screening');
    await sleep(800);
    await clickByText(page, 'Pattern Scanner');
    await sleep(2000);
    await shoot(page, '04-patterns.png');

    // 05 screening — Options Screener
    await clickByText(page, 'Options Screener');
    await sleep(2000);
    await shoot(page, '05-screening.png');

    // 06 assistant — Market detail with the AI panel open (ticker still cached)
    await clickNav(page, 'Market');
    await ensureChatOpen(page);
    try {
      await page.waitForSelector('main canvas, .max-w-4xl canvas, canvas', { timeout: 30_000 });
    } catch { /* show whatever rendered */ }
    await sleep(2000);
    await shoot(page, '06-assistant.png');

    // 07 portfolio-pnl — P&L section
    await clickNav(page, 'P&L');
    await sleep(2000);
    await shoot(page, '07-portfolio-pnl.png');

    console.log('Done. Screenshots in', OUT_DIR);
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
