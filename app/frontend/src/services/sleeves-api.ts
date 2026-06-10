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
  ChatMessage,
  FinnhubFundamentals,
  OptionsChainResponse,
  OptionsScreenerResponse,
  OptionsStrategyMeta,
  PortfolioSettings,
  Quote,
  ScanListItem,
  ScanSummary,
  SleevesConfig,
  Thesis,
  TickerData,
  TickerThesis,
  WatchlistEntry,
} from '@/types/sleeves';

import { API_BASE_URL } from '@/lib/api-base';

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

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`POST ${path} failed: ${res.status} ${res.statusText} ${text}`);
  }
  return (await res.json()) as T;
}

async function patchJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`PATCH ${path} failed: ${res.status} ${res.statusText} ${text}`);
  }
  return (await res.json()) as T;
}

async function deleteRequest<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, { method: 'DELETE' });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`DELETE ${path} failed: ${res.status} ${res.statusText} ${text}`);
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

  // Multi-watchlist endpoints
  getWatchlists: () =>
    getJSON<{ watchlists: { name: string; tickers: WatchlistEntry[] }[] }>('/sleeves/watchlists'),

  createWatchlist: (name: string, tickers: WatchlistEntry[] = []) =>
    postJSON<{ name: string; tickers: WatchlistEntry[] }>('/sleeves/watchlists', { name, tickers }),

  updateWatchlist: (name: string, tickers: WatchlistEntry[]) =>
    putJSON<{ name: string; tickers: WatchlistEntry[] }>(`/sleeves/watchlists/${encodeURIComponent(name)}`, { tickers }),

  renameWatchlist: (oldName: string, newName: string) =>
    patchJSON<{ name: string }>(`/sleeves/watchlists/${encodeURIComponent(oldName)}/rename`, { new_name: newName }),

  deleteWatchlist: (name: string) =>
    deleteRequest<{ deleted: string }>(`/sleeves/watchlists/${encodeURIComponent(name)}`),
  getTickerData: (ticker: string) =>
    getJSON<TickerData>(`/sleeves/ticker/${encodeURIComponent(ticker)}`),
  getTickerFinnhub: (ticker: string) =>
    getJSON<FinnhubFundamentals>(`/sleeves/ticker/${encodeURIComponent(ticker)}/finnhub`),
  getTickerThesis: (ticker: string, depth: 'quick' | 'deep') =>
    postJSON<TickerThesis>(
      `/sleeves/thesis/ticker/${encodeURIComponent(ticker)}?depth=${depth}`,
      {},
    ),
  getPortfolioThesis: () => postJSON<Thesis>('/sleeves/thesis/portfolio', {}),
  getSleeveThesis: (name: string) =>
    postJSON<Thesis>(`/sleeves/thesis/sleeve/${encodeURIComponent(name)}`, {}),
  getOptionsStrategies: () =>
    getJSON<{ strategies: OptionsStrategyMeta[] }>('/sleeves/options/strategies'),
  getOptionsScreener: (sleeve = 'mega_tech', strategy = 'weakness') =>
    getJSON<OptionsScreenerResponse>(
      `/sleeves/options/screener?sleeve=${encodeURIComponent(sleeve)}&strategy=${encodeURIComponent(strategy)}`
    ),
  getOptionsChain: (
    ticker: string,
    opts: { expiration?: string; horizonDays?: number } = {},
  ) => {
    const params = new URLSearchParams();
    if (opts.expiration) params.set('expiration', opts.expiration);
    if (opts.horizonDays) params.set('horizon_days', String(opts.horizonDays));
    const qs = params.toString();
    return getJSON<OptionsChainResponse>(
      `/sleeves/options/chain/${encodeURIComponent(ticker)}${qs ? `?${qs}` : ''}`,
    );
  },

  // ─── Sleeve config CRUD ─────────────────────────────────────────────────
  // Bulk replace: pass the full {name: SleeveDefinition} map. Single-sleeve
  // create/update/delete also exposed for when a single edit fits cleanly
  // (no allocation rebalance needed).

  replaceAllSleeves: (sleeves: Record<string, {
    allocation_pct: number;
    agents: string[];
    agent_weights: Record<string, number>;
    tickers: string[];
  }>) => putJSON<SleevesConfig>('/sleeves/config', { sleeves }),

  createSleeve: (
    name: string,
    body: {
      allocation_pct: number;
      agents: string[];
      agent_weights: Record<string, number>;
      tickers: string[];
    },
  ) => postJSON<SleevesConfig>(`/sleeves/config/sleeve/${encodeURIComponent(name)}`, body),

  updateSleeve: (
    name: string,
    body: {
      allocation_pct: number;
      agents: string[];
      agent_weights: Record<string, number>;
      tickers: string[];
    },
  ) => putJSON<SleevesConfig>(`/sleeves/config/sleeve/${encodeURIComponent(name)}`, body),

  deleteSleeve: (name: string) =>
    deleteRequest<SleevesConfig>(`/sleeves/config/sleeve/${encodeURIComponent(name)}`),

  renameSleeve: (oldName: string, newName: string) =>
    patchJSON<SleevesConfig>(`/sleeves/config/sleeve/${encodeURIComponent(oldName)}/rename`, { new_name: newName }),

  getPortfolioSettings: () =>
    getJSON<{ settings: PortfolioSettings }>('/sleeves/portfolio/settings'),

  putPortfolioSettings: (settings: PortfolioSettings) =>
    putJSON<{ settings: PortfolioSettings }>('/sleeves/portfolio/settings', { settings }),

  getQuotes: (tickers: string[]) =>
    getJSON<{ quotes: Record<string, Quote> }>(
      `/sleeves/quotes?tickers=${tickers.map(encodeURIComponent).join(',')}`,
    ),
};

// ─── SSE streaming helper (used by backtest endpoints) ─────────────────────
// Lightweight fetch+stream wrapper; deliberately not in sleevesApi above so
// the simple object stays one-shot getters.

export type SseHandler = (event: string, data: unknown) => void;

// ─── Chat stream ─────────────────────────────────────────────────────────────

export interface ChatContext {
  section: string;
  selectedTicker?: string | null;
  screenerSnapshot?: Record<string, unknown> | null;
  patternSnapshot?: Record<string, unknown> | null;
  scanSnapshot?: Record<string, unknown> | null;
}

/** Stream a chat response token-by-token.
 *  `onToken` receives each partial string; `onDone` fires when the stream ends. */
export async function streamChat(
  messages: ChatMessage[],
  context: ChatContext,
  onToken: (token: string) => void,
  onDone: () => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/sleeves/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, context }),
    signal,
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '');
    throw new Error(`Chat stream failed: ${res.status} ${text.slice(0, 120)}`);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder('utf-8');
  let buf = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const frames = buf.split('\n\n');
    buf = frames.pop() ?? '';
    for (const frame of frames) {
      const dataLine = frame.split('\n').find((l) => l.startsWith('data:'));
      if (!dataLine) continue;
      const raw = dataLine.slice(5).trim();
      if (raw === '[DONE]') { onDone(); return; }
      try {
        const parsed = JSON.parse(raw) as { token?: string; error?: string };
        if (parsed.token) onToken(parsed.token);
        if (parsed.error) throw new Error(parsed.error);
      } catch { /* non-JSON frame, skip */ }
    }
  }
  onDone();
}

export async function postSse(
  path: string,
  body: unknown,
  handler: SseHandler,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '');
    throw new Error(`POST ${path} failed: ${res.status} ${res.statusText} ${text}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buf = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const frames = buf.split('\n\n');
    buf = frames.pop() ?? '';
    for (const frame of frames) {
      let event = 'message';
      const dataLines: string[] = [];
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length === 0) continue;
      const raw = dataLines.join('\n');
      try {
        handler(event, JSON.parse(raw));
      } catch {
        handler(event, raw);
      }
    }
  }
}
