import { API_BASE_URL } from '@/lib/api-base';

export interface IndexQuote {
  readonly label: string;
  readonly symbol: string;
  readonly last: number | null;
  readonly prev_close: number | null;
  readonly change: number | null;
  readonly change_pct: number | null;
  readonly spark: readonly number[];
}

export interface Mover {
  readonly ticker: string;
  readonly name?: string;
  readonly change: number | null;
  readonly change_pct: number | null;
  readonly price: number | null;
}

export interface SymbolMatch {
  readonly ticker: string;
  readonly name: string;
  readonly type: string;
}

export interface HeatmapTile {
  readonly ticker: string;
  readonly name: string;
  readonly sector: string;
  readonly market_cap: number | null; // $ millions
  readonly pct_change: number | null;
  readonly spark: readonly number[];
}

export interface Catalyst {
  readonly date: string;
  readonly category: 'earnings' | 'fed' | 'inflation' | 'jobs' | 'tax_policy' | 'energy_policy' | string;
  readonly title: string;
  readonly ticker?: string;
  readonly hour?: string | null;
  readonly eps_estimate?: number | null;
  readonly expected?: boolean;
}

const BASE = `${API_BASE_URL}/market`;

async function req<T>(path: string, timeoutMs = 30_000): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal: AbortSignal.timeout(timeoutMs) });
  if (!res.ok) throw new Error(`Market request failed (${res.status})`);
  return res.json() as Promise<T>;
}

export const marketApi = {
  getIndices: () => req<{ indices: IndexQuote[] }>('/indices'),
  getMovers: () => req<{ gainers: Mover[]; losers: Mover[] }>('/movers'),
  search: (q: string) => req<{ results: SymbolMatch[] }>(`/search?q=${encodeURIComponent(q)}`, 8_000),
  getCatalysts: (tickers: readonly string[], days = 60) =>
    req<{ catalysts: Catalyst[]; as_of: string }>(
      `/catalysts?tickers=${encodeURIComponent(tickers.join(','))}&days=${days}`,
    ),
  getHeatmap: (tickers: readonly string[]) =>
    // Long timeout: the first (cold-cache) build does per-name Finnhub profile
    // lookups; once warmed (6h cache) it returns in a few seconds.
    req<{ tiles: HeatmapTile[] }>(`/heatmap?tickers=${encodeURIComponent(tickers.join(','))}`, 45_000),
};
