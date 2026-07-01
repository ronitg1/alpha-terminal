import { API_BASE_URL } from '@/lib/api-base';
import type {
  SnapTradeConnectResponse,
  SnapTradePortfolioResponse,
  SnapTradeStatus,
} from '@/types/snaptrade';

const BASE = `${API_BASE_URL}/snaptrade`;

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal: AbortSignal.timeout(60_000), ...init });
  if (!res.ok) {
    let detail = `SnapTrade request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      // non-JSON error body — keep the status-based message
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const snaptradeApi = {
  getStatus: () => req<SnapTradeStatus>('/status'),
  connect: (customRedirect?: string) =>
    req<SnapTradeConnectResponse>('/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(customRedirect ? { custom_redirect: customRedirect } : {}),
    }),
  getPortfolio: () => req<SnapTradePortfolioResponse>('/portfolio'),
  disconnect: () => req<{ disconnected: boolean }>('/connection', { method: 'DELETE' }),
};
