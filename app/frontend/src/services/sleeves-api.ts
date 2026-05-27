/**
 * Sleeves Dashboard API client.
 *
 * Thin wrapper around the `/sleeves/*` routes in `app/backend/routes/sleeves.py`.
 * Lives alongside the existing `api.ts` / `backtest-api.ts` services.
 *
 * Phase 1 covers read-only endpoints. Mutation + SSE endpoints
 * (`POST /sleeves/scan/run`, `PUT /sleeves/watchlist`) land in Phase 2/3.
 */

import { ScanListItem, ScanSummary, SleevesConfig } from '@/types/sleeves';

// Same base URL convention as the other services. Override at build time via
// VITE_API_URL if you ever expose the backend off-host.
const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`);
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`GET ${path} failed: ${res.status} ${res.statusText} ${body}`);
  }
  return (await res.json()) as T;
}

export const sleevesApi = {
  getConfig: () => getJSON<SleevesConfig>('/sleeves/config'),
  getLatestScan: () => getJSON<ScanSummary>('/sleeves/scans/latest'),
  listScans: (limit = 30) => getJSON<{ scans: ScanListItem[] }>(`/sleeves/scans?limit=${limit}`),
  getScanByDate: (date: string) => getJSON<ScanSummary>(`/sleeves/scans/${date}`),
};
