/**
 * Timeframe slicing for the interactive PriceSparkline.
 *
 * The backend's /sleeves/ticker/{ticker} returns ~2 years of daily bars.
 * The user picks one of these windows and we keep only the last N bars
 * (or bars on/after a date for YTD). % change is then recomputed against
 * the first bar in the sliced window so it stays consistent with what
 * the user is looking at.
 */
import type { PriceBar } from '@/types/sleeves';

export type Timeframe = '1W' | '1M' | '3M' | '6M' | 'YTD' | '1Y' | '2Y';

export const TIMEFRAMES: { label: Timeframe; days: number | 'ytd' }[] = [
  { label: '1W', days: 7 },
  { label: '1M', days: 30 },
  { label: '3M', days: 90 },
  { label: '6M', days: 180 },
  { label: 'YTD', days: 'ytd' },
  { label: '1Y', days: 365 },
  { label: '2Y', days: 730 },
];

/** Return the trailing window of bars for the given timeframe. */
export function slicePrices(
  prices: PriceBar[],
  timeframe: Timeframe,
): PriceBar[] {
  if (!prices || prices.length === 0) return [];
  const def = TIMEFRAMES.find((t) => t.label === timeframe);
  if (!def) return prices;

  if (def.days === 'ytd') {
    const year = new Date().getFullYear();
    const cutoff = `${year}-01-01`;
    return prices.filter((p) => p.time >= cutoff);
  }

  // Bars are daily, but the array indexes are trading days. We compare on
  // ISO date so a "90 calendar day" window correctly captures all bars in
  // that span regardless of weekends/holidays.
  const cutoffMs = Date.now() - (def.days as number) * 24 * 60 * 60 * 1000;
  const cutoff = new Date(cutoffMs).toISOString().slice(0, 10);
  return prices.filter((p) => p.time >= cutoff);
}

/** % change across the sliced window. Returns null if fewer than 2 bars. */
export function pctChange(prices: PriceBar[]): number | null {
  if (!prices || prices.length < 2) return null;
  const first = prices[0].close;
  const last = prices[prices.length - 1].close;
  if (first === 0) return null;
  return ((last - first) / first) * 100;
}
