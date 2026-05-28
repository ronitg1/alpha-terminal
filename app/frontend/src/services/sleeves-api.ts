/**
 * Sleeves Dashboard API client.
 *
 * Thin wrapper around the `/sleeves/*` routes in `app/backend/routes/sleeves.py`.
 * Lives alongside the existing `api.ts` / `backtest-api.ts` services.
 *
 * Phase 1 covers read-only endpoints. Mutation + SSE endpoints
 * (`POST /sleeves/scan/run`, `PUT /sleeves/watchlist`) land in Phase 2/3.
 */

import {
  AnalystMetadata,
  ScanListItem,
  ScanSummary,
  SleevesConfig,
  WatchlistEntry,
} from '@/types/sleeves';

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

async function putJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`PUT ${path} failed: ${res.status} ${res.statusText} ${text}`);
  }
  return (await res.json()) as T;
}

export const sleevesApi = {
  getConfig: () => getJSON<SleevesConfig>('/sleeves/config'),
  getAnalysts: () => getJSON<{ analysts: AnalystMetadata[] }>('/sleeves/analysts'),
  getLatestScan: () => getJSON<ScanSummary>('/sleeves/scans/latest'),
  listScans: (limit = 30) => getJSON<{ scans: ScanListItem[] }>(`/sleeves/scans?limit=${limit}`),
  getScanByDate: (date: string) => getJSON<ScanSummary>(`/sleeves/scans/${date}`),
  getWatchlist: () => getJSON<{ entries: WatchlistEntry[] }>('/sleeves/watchlist'),
  putWatchlist: (entries: WatchlistEntry[]) =>
    putJSON<{ entries: WatchlistEntry[] }>('/sleeves/watchlist', { entries }),
};
