/**
 * Markets + Market-movers cards for the Portfolio Summary tab. Market-wide data
 * (not holdings-derived): index levels and top gainers/losers. Best-effort — each
 * card hides itself if its fetch returns nothing. Responsive for iOS (convention #8).
 */
import { marketApi, type IndexQuote, type Mover } from '@/services/market-api';
import { cn } from '@/lib/utils';
import { useEffect, useState } from 'react';
import { pct, toneClass } from './format';

function fmtLevel(v: number | null): string {
  if (v === null || Number.isNaN(v)) return '—';
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function MarketsCard({ indices }: { indices: readonly IndexQuote[] }) {
  if (indices.length === 0) return null;
  return (
    <div className="min-w-0 overflow-hidden rounded-lg border border-border/60 bg-card p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Markets</div>
      {/* Compact 2-up on phones (value + % on one line) so 10 instruments don't
          push the user's own numbers off-screen; 4-up from sm. */}
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        {indices.map((ix) => (
          <div key={ix.symbol} className="min-w-0">
            <div className="truncate text-[11px] text-muted-foreground">{ix.label}</div>
            <div className="flex items-baseline gap-1.5">
              <span className="text-sm font-semibold tabular-nums">{fmtLevel(ix.last)}</span>
              <span className={cn('text-[10px] tabular-nums', toneClass(ix.change_pct))}>{pct(ix.change_pct)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function MoverList({ title, rows, tone }: { title: string; rows: readonly Mover[]; tone: string }) {
  return (
    <div className="min-w-0 space-y-1.5">
      <div className={cn('text-[10px] font-medium uppercase', tone)}>{title}</div>
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">—</p>
      ) : (
        rows.map((m) => (
          <div key={m.ticker} className="flex items-center gap-2 text-xs">
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="font-mono font-medium leading-tight">{m.ticker}</span>
              {m.name && (
                <span className="block truncate text-[10px] leading-tight text-muted-foreground">{m.name}</span>
              )}
            </div>
            <span className={cn('w-14 shrink-0 text-right tabular-nums', toneClass(m.change_pct))}>{pct(m.change_pct)}</span>
          </div>
        ))
      )}
    </div>
  );
}

function MoversCard({ gainers, losers }: { gainers: readonly Mover[]; losers: readonly Mover[] }) {
  if (gainers.length === 0 && losers.length === 0) return null;
  return (
    <div className="min-w-0 overflow-hidden rounded-lg border border-border/60 bg-card p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Market movers today</div>
      <div className="mt-3 grid gap-4 sm:grid-cols-2 [&>*]:min-w-0">
        <MoverList title="Gainers" rows={gainers} tone="text-emerald-500" />
        <MoverList title="Losers" rows={losers} tone="text-rose-500" />
      </div>
    </div>
  );
}

export function MarketCards() {
  const [indices, setIndices] = useState<IndexQuote[]>([]);
  const [gainers, setGainers] = useState<Mover[]>([]);
  const [losers, setLosers] = useState<Mover[]>([]);

  useEffect(() => {
    let alive = true;
    void marketApi.getIndices().then((r) => alive && setIndices(r.indices)).catch(() => {});
    void marketApi.getMovers().then((r) => { if (alive) { setGainers(r.gainers); setLosers(r.losers); } }).catch(() => {});
    return () => { alive = false; };
  }, []);

  if (indices.length === 0 && gainers.length === 0 && losers.length === 0) return null;
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <MarketsCard indices={indices} />
      <MoversCard gainers={gainers} losers={losers} />
    </div>
  );
}
