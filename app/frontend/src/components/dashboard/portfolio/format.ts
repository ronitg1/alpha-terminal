// Shared formatters for the Portfolio tab. Null-safe: missing data renders "—".

export function money(v: number | null | undefined, opts?: { compact?: boolean }): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return v.toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: opts?.compact ? 0 : 2,
    notation: opts?.compact ? 'compact' : 'standard',
  });
}

export function signedMoney(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const s = money(Math.abs(v));
  return v < 0 ? `-${s}` : `+${s}`;
}

export function pct(v: number | null | undefined, signed = true): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const body = `${Math.abs(v).toFixed(2)}%`;
  if (!signed) return body;
  return v < 0 ? `-${body}` : `+${body}`;
}

export function num(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

// Tailwind text color for a gain/loss value (null => muted, no color implied).
export function toneClass(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || v === 0) return 'text-muted-foreground';
  return v > 0 ? 'text-emerald-500' : 'text-rose-500';
}
