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

export interface OwnershipHolder {
  readonly institution: string;
  readonly shares: number;
  readonly prev_shares: number;
  readonly value: number | null;
  readonly change: 'new' | 'added' | 'trimmed' | 'exited' | 'held' | string;
  readonly delta_pct: number | null;
}
export interface OwnershipName {
  readonly ticker: string;
  readonly holders: OwnershipHolder[];
}
export interface Ownership {
  readonly names: OwnershipName[];
  readonly institutions: string[];
}

/** GET /portfolio/stats — approximate risk stats for the combined book. */
export interface PortfolioStats {
  readonly available: boolean;
  readonly reason?: string; // no_brokerage | insufficient_history | no_price_data | error
  readonly sharpe?: number;
  readonly annualized_return_pct?: number;
  readonly annualized_vol_pct?: number;
  readonly rf_pct?: number;
  readonly days?: number;
  readonly coverage_pct?: number | null;
  readonly method?: string;
}

// Last successful overview, persisted so the tab + left nav render instantly on
// the next load while a fresh copy fetches in the background (the server also
// caches; this just kills the client-side blank-screen wait).
const OVERVIEW_CACHE_KEY = 'portfolio-overview-cache-v1';

function readOverviewCache(): PortfolioOverview | null {
  try {
    const raw = localStorage.getItem(OVERVIEW_CACHE_KEY);
    return raw ? (JSON.parse(raw) as PortfolioOverview) : null;
  } catch {
    return null;
  }
}

function writeOverviewCache(o: PortfolioOverview): void {
  try {
    if (o?.connected) localStorage.setItem(OVERVIEW_CACHE_KEY, JSON.stringify(o));
  } catch {
    /* quota / disabled storage — non-fatal */
  }
}

export const portfolioApi = {
  /** Read the last cached overview synchronously (no network) for instant paint. */
  getCachedOverview: readOverviewCache,
  getOverview: async (opts?: { force?: boolean }): Promise<PortfolioOverview> => {
    const url = `${BASE}/overview${opts?.force ? '?refresh=true' : ''}`;
    const res = await fetch(url, { signal: AbortSignal.timeout(60_000) });
    if (!res.ok) throw new Error(`Portfolio request failed (${res.status})`);
    const data = (await res.json()) as PortfolioOverview;
    writeOverviewCache(data);
    return data;
  },
  getOwnership: async (tickers: readonly string[]): Promise<Ownership> => {
    if (tickers.length === 0) return { names: [], institutions: [] };
    const res = await fetch(`${BASE}/ownership?tickers=${encodeURIComponent(tickers.join(','))}`, {
      signal: AbortSignal.timeout(60_000),
    });
    if (!res.ok) return { names: [], institutions: [] };
    return res.json() as Promise<Ownership>;
  },
  getStats: async (): Promise<PortfolioStats> => {
    const res = await fetch(`${BASE}/stats`, { signal: AbortSignal.timeout(60_000) });
    if (!res.ok) return { available: false, reason: 'error' };
    return res.json() as Promise<PortfolioStats>;
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
