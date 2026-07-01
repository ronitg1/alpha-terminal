/**
 * Notable earnings this week: curated market-movers + the watchlist, split into
 * upcoming (with EPS estimate) and already-reported (beat/miss vs estimate + the
 * post-print price reaction). Broader than the watchlist calendar. Best-effort —
 * populates during earnings season; a quiet week shows an empty note. Responsive.
 */
import { marketApi, type EarningsWeek } from '@/services/market-api';
import { cn } from '@/lib/utils';
import { CalendarCheck2 } from 'lucide-react';
import { useEffect, useState } from 'react';

const HOUR: Record<string, string> = { bmo: 'pre', amc: 'post', dmh: 'mid' };
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function fmtDate(iso: string): string {
  const [, m, d] = iso.split('-');
  return `${MON[Number(m) - 1] ?? m} ${Number(d)}`;
}
function tone(v: number | null): string {
  if (v == null) return 'text-muted-foreground';
  return v >= 0 ? 'text-emerald-500' : 'text-rose-500';
}
function signed(v: number | null, suffix = '%'): string {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)}${suffix}`;
}

export function EarningsThisWeek({ tickers, onTicker }: { tickers: string[]; onTicker: (t: string) => void }) {
  const [data, setData] = useState<EarningsWeek | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    const h = setTimeout(() => {
      marketApi
        .getEarningsWeek(tickers)
        .then((r) => { if (alive) setData((prev) => (r.upcoming.length || r.reported.length ? r : prev ?? r)); })
        .catch(() => {})
        .finally(() => { if (alive) setLoading(false); });
    }, 400);
    return () => { alive = false; clearTimeout(h); };
  }, [tickers.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  const upcoming = data?.upcoming ?? [];
  const reported = data?.reported ?? [];
  const empty = upcoming.length === 0 && reported.length === 0;

  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="mb-3 flex items-center gap-2">
        <CalendarCheck2 className="h-4 w-4 text-muted-foreground" />
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Notable earnings this week</span>
      </div>

      {loading && !data ? (
        <p className="text-xs text-muted-foreground">Loading earnings…</p>
      ) : empty ? (
        <p className="text-xs italic text-muted-foreground">No notable earnings this week — most report later in the quarter.</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {/* Upcoming */}
          <div>
            <div className="mb-1 text-[10px] font-medium uppercase text-muted-foreground">Upcoming</div>
            {upcoming.length === 0 ? (
              <p className="text-xs text-muted-foreground">None left this week.</p>
            ) : (
              <div className="space-y-1">
                {upcoming.map((e) => (
                  <button key={e.ticker} type="button" onClick={() => onTicker(e.ticker)} className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-muted/40">
                    <span className="font-mono text-xs font-semibold">{e.ticker}</span>
                    <span className="text-[11px] text-muted-foreground">{fmtDate(e.date)}{e.hour ? ` · ${HOUR[e.hour] ?? e.hour}` : ''}</span>
                    {e.eps_estimate != null && <span className="ml-auto text-[11px] tabular-nums text-muted-foreground">est ${e.eps_estimate.toFixed(2)}</span>}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Reported */}
          <div>
            <div className="mb-1 text-[10px] font-medium uppercase text-muted-foreground">Reported</div>
            {reported.length === 0 ? (
              <p className="text-xs text-muted-foreground">None yet this week.</p>
            ) : (
              <div className="space-y-1">
                {reported.map((e) => (
                  <button key={e.ticker} type="button" onClick={() => onTicker(e.ticker)} className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-muted/40">
                    <span className="font-mono text-xs font-semibold">{e.ticker}</span>
                    {e.surprise_pct != null && (
                      <span className={cn('rounded px-1 py-0.5 text-[9px] font-medium', e.surprise_pct >= 0 ? 'bg-emerald-500/15 text-emerald-500' : 'bg-rose-500/15 text-rose-500')}>
                        {e.surprise_pct >= 0 ? 'beat' : 'miss'} {signed(e.surprise_pct)}
                      </span>
                    )}
                    <span className={cn('ml-auto text-[11px] tabular-nums', tone(e.reaction_pct))} title="Post-print price reaction">
                      {signed(e.reaction_pct)}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
