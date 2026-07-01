import { API_BASE_URL } from '@/lib/api-base';
import type { PortfolioOverview } from '@/types/portfolio';

const BASE = `${API_BASE_URL}/portfolio`;

export const portfolioApi = {
  getOverview: async (): Promise<PortfolioOverview> => {
    const res = await fetch(`${BASE}/overview`, { signal: AbortSignal.timeout(60_000) });
    if (!res.ok) throw new Error(`Portfolio request failed (${res.status})`);
    return res.json() as Promise<PortfolioOverview>;
  },
};
