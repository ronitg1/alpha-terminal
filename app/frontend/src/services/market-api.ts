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
  readonly change: number | null;
  readonly change_pct: number | null;
  readonly price: number | null;
}

const BASE = `${API_BASE_URL}/market`;

async function req<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal: AbortSignal.timeout(30_000) });
  if (!res.ok) throw new Error(`Market request failed (${res.status})`);
  return res.json() as Promise<T>;
}

export const marketApi = {
  getIndices: () => req<{ indices: IndexQuote[] }>('/indices'),
  getMovers: () => req<{ gainers: Mover[]; losers: Mover[] }>('/movers'),
};
