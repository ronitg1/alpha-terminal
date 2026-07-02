/**
 * Scheduled pre-scan API client — manage the times a user wants automatic
 * background pattern scans, and read the latest pre-computed results.
 *
 * Requests hit the backend origin, so the global fetch interceptor
 * (auth-fetch.ts) attaches the Clerk bearer token automatically.
 */
import { API_BASE_URL } from '@/lib/api-base';
import type { ScanResult } from '@/types/patterns';

export interface ScanSchedule {
  id: number;
  time_of_day: string; // "HH:MM" 24-hour, local to `timezone`
  timezone: string;
  enabled: boolean;
  last_run_on: string | null;
  timeframe: string; // week | day | 1h | 15m
  lookback_days: number;
}

export interface PrescanResult {
  results: ScanResult[];
  timeframe: string;
  ticker_count: number;
  computed_at: string | null;
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

/** The browser's IANA timezone, e.g. "America/New_York". */
export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'America/New_York';
  } catch {
    return 'America/New_York';
  }
}

export const scheduledApi = {
  listSchedules: () =>
    req<{ schedules: ScanSchedule[] }>('/scheduled/schedules').then((r) => r.schedules),

  addSchedule: (timeOfDay: string, timezone: string, timeframe: string, lookbackDays: number) =>
    req<ScanSchedule>('/scheduled/schedules', {
      method: 'POST',
      body: JSON.stringify({
        time_of_day: timeOfDay,
        timezone,
        timeframe,
        lookback_days: lookbackDays,
      }),
    }),

  updateSchedule: (id: number, timeframe: string, lookbackDays: number) =>
    req<ScanSchedule>(`/scheduled/schedules/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ timeframe, lookback_days: lookbackDays }),
    }),

  toggleSchedule: (id: number, enabled: boolean) =>
    req<ScanSchedule>(`/scheduled/schedules/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled }),
    }),

  deleteSchedule: (id: number) =>
    req<{ ok: boolean }>(`/scheduled/schedules/${id}`, { method: 'DELETE' }),

  /** The pre-scan for a timeframe, or the most recent one when omitted. */
  getPrescan: (timeframe?: string) =>
    req<{ prescan: PrescanResult | null }>(
      `/scheduled/prescan${timeframe ? `?timeframe=${encodeURIComponent(timeframe)}` : ''}`,
    ).then((r) => r.prescan),
};
