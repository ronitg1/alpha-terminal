/** Thin fetch wrapper for the /pnl/* endpoints. */

import { API_BASE_URL } from '@/lib/api-base';
import type {
  FidelityImportResult,
  PnlMark,
  PnlPosition,
  PnlSummary,
  PositionCreatePayload,
} from '@/types/pnl';

const BASE = `${API_BASE_URL}/pnl`;

export interface PaperAccount {
  readonly starting_cash: number;
  readonly cash: number;
  readonly buying_power: number;
  readonly positions_value: number;
  readonly equity: number;
  readonly realized: number;
  readonly unrealized: number;
  readonly total_pnl: number;
  readonly total_pnl_pct: number | null;
  readonly asof: string;
}

async function _req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    signal: AbortSignal.timeout(60_000),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`${init?.method ?? 'GET'} ${path} failed (${res.status}): ${text.slice(0, 160)}`);
  }
  return res.json() as Promise<T>;
}

function _json(method: string, body: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

export const pnlApi = {
  listPositions: () => _req<{ positions: PnlPosition[] }>('/positions'),

  createPosition: (payload: PositionCreatePayload) =>
    _req<PnlPosition>('/positions', _json('POST', payload)),

  patchPosition: (id: string, fields: Partial<PnlPosition>) =>
    _req<PnlPosition>(`/positions/${id}`, _json('PATCH', fields)),

  closePosition: (id: string, exitPrice: number, exitDate?: string) =>
    _req<PnlPosition>(`/positions/${id}/close`, _json('POST', { exit_price: exitPrice, exit_date: exitDate ?? null })),

  deletePosition: (id: string) =>
    _req<{ deleted: string }>(`/positions/${id}`, { method: 'DELETE' }),

  getMarks: () => _req<{ marks: Record<string, PnlMark>; asof: string }>('/marks'),

  getAccount: () => _req<PaperAccount>('/account'),

  resetAccount: () =>
    _req<{ reset: boolean; removed: number; starting_cash: number }>('/account/reset', { method: 'POST' }),

  getSummary: (withMarks = true) =>
    _req<PnlSummary>(`/summary?marks=${withMarks}`),

  importFidelity: async (file: File): Promise<FidelityImportResult> => {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`${BASE}/import/fidelity`, {
      method: 'POST',
      body: form,
      signal: AbortSignal.timeout(60_000),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`Import failed (${res.status}): ${text.slice(0, 200)}`);
    }
    return res.json() as Promise<FidelityImportResult>;
  },
};
