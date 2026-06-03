/**
 * Thin fetch wrapper for all /patterns/* endpoints.
 * Uses the same base URL convention as the rest of the app (direct localhost:8000).
 */

import type {
  ScanResult,
  ChartData,
  SignalAnalysisData,
  PatternsListResponse,
} from '@/types/patterns';

const BASE = 'http://localhost:8000/patterns';

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
  lookbackDays: number
): Promise<ScanResult[]> {
  return _post<ScanResult[]>('/scan', {
    tickers,
    patterns,
    lookback_days: lookbackDays,
  });
}

export function scanWatchlist(
  patterns: string[],
  lookbackDays: number
): Promise<ScanResult[]> {
  const params = new URLSearchParams({ lookback_days: String(lookbackDays) });
  if (patterns.length) params.set('patterns', patterns.join(','));
  return _get<ScanResult[]>(`/watchlist/scan?${params}`);
}

export function getChart(ticker: string, lookbackDays = 365): Promise<ChartData> {
  return _get<ChartData>(`/chart/${ticker}?lookback_days=${lookbackDays}`);
}

export function getSignalAnalysis(
  ticker: string,
  pattern: string
): Promise<SignalAnalysisData> {
  const encoded = pattern.replace(/ /g, '-');
  return _get<SignalAnalysisData>(`/signal-analysis/${ticker}/${encoded}`);
}

export function listPatterns(): Promise<PatternsListResponse> {
  return _get<PatternsListResponse>('/patterns');
}
