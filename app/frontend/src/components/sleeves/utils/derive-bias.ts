/**
 * Pure helpers that derive bias / conviction / signal counts from
 * latestScan rows. Kept here so the new dashboard widgets don't
 * re-implement the same reductions (and so they can be unit-tested
 * in isolation if we add tests later).
 */

import type { ScanSummary, SleeveConfig, TickerRow } from '@/types/sleeves';

export type Bias = 'bullish' | 'bearish' | 'mixed' | 'neutral' | 'unscanned';

export interface BiasReadout {
  bias: Bias;
  /** Net-long fraction in [-1, +1]. >0 = more bull than bear. */
  net: number;
  /** Mean of avg_confidence across scanned rows, 0-100. 0 if nothing scanned. */
  weightedConv: number;
  bullish: number;
  bearish: number;
  neutral: number;
  scanned: number;
  highConv: number;
  /** Count of rows with has_variant_perception=true. */
  variant: number;
}

/** Empty-but-safe readout for use before any scan exists. */
export const EMPTY_READOUT: BiasReadout = {
  bias: 'unscanned',
  net: 0,
  weightedConv: 0,
  bullish: 0,
  bearish: 0,
  neutral: 0,
  scanned: 0,
  highConv: 0,
  variant: 0,
};

/** ≥ this avg_confidence counts as a "high-conviction" signal. Matches the
 *  threshold the old HighConvictionStrip filters at implicitly. */
const HIGH_CONV_THRESHOLD = 60;

function classifyBias(bullish: number, bearish: number, neutral: number): Bias {
  const total = bullish + bearish + neutral;
  if (total === 0) return 'unscanned';
  // Net-leaning bias: the dominant direction must beat the other by at least
  // 30% of total or it counts as "mixed" (intentionally a soft threshold to
  // avoid flipping bias on every marginal scan).
  const gap = Math.abs(bullish - bearish);
  if (gap / total < 0.30) return bullish + bearish > 0 ? 'mixed' : 'neutral';
  if (bullish > bearish) return 'bullish';
  return 'bearish';
}

export function readoutForRows(rows: TickerRow[]): BiasReadout {
  if (rows.length === 0) return EMPTY_READOUT;

  let bullish = 0;
  let bearish = 0;
  let neutral = 0;
  let highConv = 0;
  let variant = 0;
  let confSum = 0;

  for (const r of rows) {
    if (r.consensus === 'bullish') bullish += 1;
    else if (r.consensus === 'bearish') bearish += 1;
    else neutral += 1;
    if (r.avg_confidence >= HIGH_CONV_THRESHOLD) highConv += 1;
    if (r.has_variant_perception) variant += 1;
    confSum += r.avg_confidence;
  }

  const scanned = rows.length;
  const net = (bullish - bearish) / scanned;
  const weightedConv = scanned > 0 ? confSum / scanned : 0;

  return {
    bias: classifyBias(bullish, bearish, neutral),
    net,
    weightedConv,
    bullish,
    bearish,
    neutral,
    scanned,
    highConv,
    variant,
  };
}

/** Sleeve-scoped readout: filters rows where r.sleeve === sleeve.name. */
export function readoutForSleeve(
  sleeve: SleeveConfig,
  scan: ScanSummary | null,
): BiasReadout {
  const rows = (scan?.rows ?? []).filter((r) => r.sleeve === sleeve.name);
  return readoutForRows(rows);
}

/** Portfolio-level: all rows, but the weighted conviction averages across
 *  sleeves using their allocation_pct, not the flat ticker mean. This is the
 *  number that should headline the dashboard. */
export function readoutForPortfolio(
  sleeves: SleeveConfig[],
  scan: ScanSummary | null,
): BiasReadout {
  if (!scan || scan.rows.length === 0) return EMPTY_READOUT;
  const flat = readoutForRows(scan.rows);
  // Replace weightedConv with allocation-weighted mean across sleeves.
  let weighted = 0;
  let totalAlloc = 0;
  for (const s of sleeves) {
    const sleeveRows = scan.rows.filter((r) => r.sleeve === s.name);
    if (sleeveRows.length === 0) continue;
    const r = readoutForRows(sleeveRows);
    weighted += r.weightedConv * s.allocation_pct;
    totalAlloc += s.allocation_pct;
  }
  return {
    ...flat,
    weightedConv: totalAlloc > 0 ? weighted / totalAlloc : flat.weightedConv,
  };
}

export function biasLabel(b: Bias): string {
  switch (b) {
    case 'bullish':
      return 'Bullish';
    case 'bearish':
      return 'Bearish';
    case 'mixed':
      return 'Mixed';
    case 'neutral':
      return 'Neutral';
    case 'unscanned':
      return 'Not scanned';
  }
}

export function biasColorClass(b: Bias): string {
  switch (b) {
    case 'bullish':
      return 'text-emerald-600 dark:text-emerald-400';
    case 'bearish':
      return 'text-rose-600 dark:text-rose-400';
    case 'mixed':
      return 'text-amber-600 dark:text-amber-400';
    case 'neutral':
      return 'text-muted-foreground';
    case 'unscanned':
      return 'text-muted-foreground italic';
  }
}
