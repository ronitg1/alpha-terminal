/**
 * Thin fetch wrapper for all /patterns/* endpoints.
 * Uses the same base URL convention as the rest of the app.
 */

import type {
  ScanResult,
  ChartData,
  PatternTimeframe,
  SignalAnalysisData,
  PatternsListResponse,
} from '@/types/patterns';

import { API_BASE_URL } from '@/lib/api-base';

const BASE = `${API_BASE_URL}/patterns`;

async function _get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal: AbortSignal.timeout(120_000) });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`GET ${path} failed (${res.status}): ${text.slice(0, 120)}`);
  }
  return res.json() as Promise<T>;
}

async function _post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(120_000),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`POST ${path} failed (${res.status}): ${text.slice(0, 120)}`);
  }
  return res.json() as Promise<T>;
}

export function scanTickers(
  tickers: string[],
  patterns: string[],
  lookbackDays: number,
  timeframe: PatternTimeframe = 'day'
): Promise<ScanResult[]> {
  return _post<ScanResult[]>('/scan', {
    tickers,
    patterns,
    lookback_days: lookbackDays,
    timeframe,
  });
}

export function scanWatchlist(
  patterns: string[],
  lookbackDays: number,
  timeframe: PatternTimeframe = 'day'
): Promise<ScanResult[]> {
  const params = new URLSearchParams({ lookback_days: String(lookbackDays), timeframe });
  if (patterns.length) params.set('patterns', patterns.join(','));
  return _get<ScanResult[]>(`/watchlist/scan?${params}`);
}

export function getChart(
  ticker: string,
  lookbackDays = 365,
  timeframe: PatternTimeframe = 'day'
): Promise<ChartData> {
  return _get<ChartData>(`/chart/${ticker}?lookback_days=${lookbackDays}&timeframe=${timeframe}`);
}

export function getSignalAnalysis(
  ticker: string,
  pattern: string,
  timeframe: PatternTimeframe = 'day'
): Promise<SignalAnalysisData> {
  const encoded = pattern.replace(/ /g, '-');
  return _get<SignalAnalysisData>(`/signal-analysis/${ticker}/${encoded}?timeframe=${timeframe}`);
}

export function listPatterns(): Promise<PatternsListResponse> {
  return _get<PatternsListResponse>('/patterns');
}
