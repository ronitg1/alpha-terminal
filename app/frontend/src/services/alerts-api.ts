/**
 * Telegram alert config API client. The bot token is write-only from the
 * client's perspective — the server never returns it, only a `has_token` flag.
 */
import { API_BASE_URL } from '@/lib/api-base';

export interface AlertSettings {
  chat_id: string | null;
  enabled: boolean;
  min_confidence: number;
  timeframes: string[];
  has_token: boolean;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const alertsApi = {
  getSettings: () => req<AlertSettings>('/alerts/settings'),

  saveSettings: (patch: { enabled?: boolean; min_confidence?: number; timeframes?: string[] }) =>
    req<AlertSettings>('/alerts/settings', { method: 'PUT', body: JSON.stringify(patch) }),

  setToken: (token: string) =>
    req<{ ok: boolean; has_token: boolean }>('/alerts/token', {
      method: 'POST',
      body: JSON.stringify({ token }),
    }),

  pair: (code: string) =>
    req<{ paired: boolean; chat_id?: string; error?: string }>('/alerts/pair', {
      method: 'POST',
      body: JSON.stringify({ code }),
    }),

  test: () => req<{ ok: boolean }>('/alerts/test', { method: 'POST' }),

  disconnect: () => req<{ ok: boolean }>('/alerts/config', { method: 'DELETE' }),
};
