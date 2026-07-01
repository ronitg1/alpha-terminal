import { API_BASE_URL } from '@/lib/api-base';
import type { PortfolioOverview } from '@/types/portfolio';

const BASE = `${API_BASE_URL}/portfolio`;

export interface EarningsEvent {
  readonly ticker: string;
  readonly date: string | null;
  readonly hour: string | null; // bmo | amc | dmh
  readonly eps_estimate: number | null;
  readonly revenue_estimate: number | null;
}

export const portfolioApi = {
  getOverview: async (): Promise<PortfolioOverview> => {
    const res = await fetch(`${BASE}/overview`, { signal: AbortSignal.timeout(60_000) });
    if (!res.ok) throw new Error(`Portfolio request failed (${res.status})`);
    return res.json() as Promise<PortfolioOverview>;
  },
  getEarnings: async (tickers: readonly string[], days = 45): Promise<EarningsEvent[]> => {
    if (tickers.length === 0) return [];
    const res = await fetch(`${BASE}/earnings?tickers=${encodeURIComponent(tickers.join(','))}&days=${days}`, {
      signal: AbortSignal.timeout(30_000),
    });
    if (!res.ok) return [];
    const body = await res.json();
    return (body.earnings ?? []) as EarningsEvent[];
  },
};
