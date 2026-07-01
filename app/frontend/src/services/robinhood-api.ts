import { API_BASE_URL } from '@/lib/api-base';
import type { RobinhoodPortfolioResponse } from '@/types/robinhood';

const BASE = `${API_BASE_URL}/robinhood`;

async function req<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal: AbortSignal.timeout(60_000) });
  if (!res.ok) {
    throw new Error(`Robinhood request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export const robinhoodApi = {
  getPortfolio: () => req<RobinhoodPortfolioResponse>('/portfolio'),
};
