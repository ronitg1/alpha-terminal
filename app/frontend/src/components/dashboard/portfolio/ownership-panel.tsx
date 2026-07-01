/**
 * 13F ownership / flow tracker: for each holding, which of a curated set of famous
 * funds hold it and how they moved last quarter (opened / added / trimmed / exited).
 * SEC EDGAR data, matched by issuer name, cached a day on the backend. Quarterly and
 * lagged — for spotting when smart money enters/exits. Responsive (convention #8).
 */
import { portfolioApi, type Ownership } from '@/services/portfolio-api';
import type { PortfolioAccount } from '@/types/portfolio';
import { cn } from '@/lib/utils';
import { Building2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

const CHANGE: Record<string, { label: string; cls: string }> = {
  new: { label: 'New', cls: 'bg-emerald-500/15 text-emerald-500' },
  added: { label: 'Added', cls: 'bg-emerald-500/15 text-emerald-500' },
  trimmed: { label: 'Trimmed', cls: 'bg-amber-500/15 text-amber-500' },
  exited: { label: 'Exited', cls: 'bg-rose-500/15 text-rose-500' },
  held: { label: 'Held', cls: 'bg-muted text-muted-foreground' },
};

function fmtShares(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

export function OwnershipPanel({ account }: { account: PortfolioAccount }) {
  const [data, setData] = useState<Ownership | null>(null);
  const [loading, setLoading] = useState(true);

  const tickers = useMemo(
    () => Array.from(new Set(account.positions.filter((p) => p.kind === 'stock' && p.underlying).map((p) => p.underlying))).slice(0, 25) as string[],
    [account],
  );

  useEffect(() => {
    let alive = true;
    setLoading(true);
    portfolioApi
      .getOwnership(tickers)
      .then((r) => { if (alive) setData(r); })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [tickers.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  const names = data?.names ?? [];

  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="mb-1 flex items-center gap-2">
        <Building2 className="h-4 w-4 text-muted-foreground" />
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Institutional ownership (13F)</span>
      </div>
      <p className="mb-3 text-[10px] text-muted-foreground">
        How well-known funds moved in your names last quarter (13F is quarterly + lagged).
      </p>

      {loading && !data ? (
        <p className="text-xs text-muted-foreground">Pulling 13F filings…</p>
      ) : names.length === 0 ? (
        <p className="text-xs italic text-muted-foreground">No tracked funds hold your names — or filings weren't reachable.</p>
      ) : (
        <div className="max-h-96 space-y-3 overflow-y-auto">
          {names.map((n) => (
            <div key={n.ticker}>
              <div className="mb-1 font-mono text-xs font-semibold">{n.ticker}</div>
              <div className="space-y-1">
                {n.holders.map((h, i) => {
                  const c = CHANGE[h.change] ?? CHANGE.held;
                  return (
                    <div key={i} className="flex items-center gap-2 text-xs">
                      <span className={cn('w-16 shrink-0 rounded px-1.5 py-0.5 text-center text-[9px] font-medium', c.cls)}>{c.label}</span>
                      <span className="min-w-0 truncate text-muted-foreground">{h.institution}</span>
                      <span className="ml-auto shrink-0 tabular-nums">{fmtShares(h.shares)}</span>
                      {h.delta_pct != null && h.change !== 'held' && (
                        <span className={cn('w-12 shrink-0 text-right text-[10px] tabular-nums', h.delta_pct >= 0 ? 'text-emerald-500' : 'text-rose-500')}>
                          {h.delta_pct >= 0 ? '+' : ''}{h.delta_pct}%
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
