/**
 * Portfolio Events card (M3): holdings hitting 52-week highs/lows + upcoming
 * earnings, with a drill-in earnings calendar (weekly ↔ monthly). Holdings-derived
 * + Finnhub earnings; best-effort. Responsive (convention #8).
 */
import { portfolioApi, type EarningsEvent } from '@/services/portfolio-api';
import { cn } from '@/lib/utils';
import type { PortfolioAccount, PortfolioPosition } from '@/types/portfolio';
import { CalendarDays, TrendingDown, TrendingUp, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

interface RangeFlag { symbol: string; kind: 'high' | 'low' }

function week52Flags(positions: readonly PortfolioPosition[]): RangeFlag[] {
  const seen = new Set<string>();
  const flags: RangeFlag[] = [];
  for (const p of positions) {
    if (p.kind !== 'stock' || p.last_price == null || p.week52_high == null || p.week52_low == null) continue;
    if (seen.has(p.underlying)) continue;
    if (p.last_price >= p.week52_high * 0.98) { flags.push({ symbol: p.underlying, kind: 'high' }); seen.add(p.underlying); }
    else if (p.last_price <= p.week52_low * 1.02) { flags.push({ symbol: p.underlying, kind: 'low' }); seen.add(p.underlying); }
  }
  return flags;
}

function fmtDate(iso: string | null): string {
  if (!iso) return '';
  const [, m, d] = iso.split('-');
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${months[Number(m) - 1] ?? m} ${Number(d)}`;
}

const HOUR_LABEL: Record<string, string> = { bmo: 'before open', amc: 'after close', dmh: 'during hours' };

function isoWeekKey(iso: string): string {
  // Group by the Monday of the week for a stable weekly bucket.
  const d = new Date(`${iso}T00:00:00`);
  const day = (d.getDay() + 6) % 7; // Mon=0
  d.setDate(d.getDate() - day);
  return d.toISOString().slice(0, 10);
}

function EarningsModal({ events, onClose }: { events: EarningsEvent[]; onClose: () => void }) {
  const [view, setView] = useState<'weekly' | 'monthly'>('weekly');
  const groups = useMemo(() => {
    const map = new Map<string, EarningsEvent[]>();
    for (const e of events) {
      if (!e.date) continue;
      const key = view === 'weekly' ? isoWeekKey(e.date) : e.date.slice(0, 7);
      (map.get(key) ?? map.set(key, []).get(key)!).push(e);
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [events, view]);

  const label = (key: string) => {
    if (view === 'monthly') {
      const [y, m] = key.split('-');
      const months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
      return `${months[Number(m) - 1]} ${y}`;
    }
    return `Week of ${fmtDate(key)}`;
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="flex max-h-[85dvh] w-full max-w-lg flex-col overflow-hidden rounded-lg border border-border bg-card"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
          <CalendarDays className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm font-semibold">Earnings calendar</span>
          <div className="ml-auto flex rounded-md bg-muted p-0.5 text-[11px]">
            {(['weekly', 'monthly'] as const).map((v) => (
              <button key={v} type="button" onClick={() => setView(v)}
                className={cn('rounded px-2 py-0.5 font-medium capitalize', view === v ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground')}>
                {v}
              </button>
            ))}
          </div>
          <button type="button" onClick={onClose} className="ml-1 text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          {groups.length === 0 ? (
            <p className="text-xs italic text-muted-foreground">No upcoming earnings for your holdings.</p>
          ) : (
            groups.map(([key, evs]) => (
              <div key={key} className="mb-3">
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{label(key)}</div>
                {evs.map((e, i) => (
                  <div key={`${e.ticker}-${i}`} className="flex items-center gap-2 py-1 text-xs">
                    <span className="font-mono font-medium">{e.ticker}</span>
                    <span className="text-muted-foreground">{fmtDate(e.date)}{e.hour && ` · ${HOUR_LABEL[e.hour] ?? e.hour}`}</span>
                    {e.eps_estimate != null && <span className="ml-auto text-muted-foreground">est EPS ${e.eps_estimate.toFixed(2)}</span>}
                  </div>
                ))}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

export function PortfolioEvents({ account }: { account: PortfolioAccount }) {
  const [earnings, setEarnings] = useState<EarningsEvent[]>([]);
  const [showCal, setShowCal] = useState(false);
  const flags = useMemo(() => week52Flags(account.positions), [account]);
  const tickers = useMemo(
    () => Array.from(new Set(account.positions.filter((p) => p.kind === 'stock').map((p) => p.underlying).filter(Boolean))) as string[],
    [account],
  );

  useEffect(() => {
    let alive = true;
    void portfolioApi.getEarnings(tickers).then((e) => alive && setEarnings(e)).catch(() => {});
    return () => { alive = false; };
  }, [tickers]);

  if (flags.length === 0 && earnings.length === 0) return null;
  const upcoming = earnings.slice(0, 5);

  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Portfolio events</div>

      {flags.length > 0 && (
        <div className="mt-3 space-y-1">
          {flags.map((f) => (
            <div key={f.symbol} className="flex items-center gap-2 text-xs">
              {f.kind === 'high' ? <TrendingUp className="h-3.5 w-3.5 text-emerald-500" /> : <TrendingDown className="h-3.5 w-3.5 text-rose-500" />}
              <span className="font-mono font-medium">{f.symbol}</span>
              <span className="text-muted-foreground">at 52-week {f.kind}</span>
            </div>
          ))}
        </div>
      )}

      {upcoming.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 flex items-center gap-2">
            <span className="text-[11px] font-medium text-muted-foreground">Upcoming earnings</span>
            <button type="button" onClick={() => setShowCal(true)} className="ml-auto text-[11px] text-primary hover:underline">
              Full calendar →
            </button>
          </div>
          {upcoming.map((e, i) => (
            <div key={`${e.ticker}-${i}`} className="flex items-center gap-2 py-0.5 text-xs">
              <span className="font-mono font-medium">{e.ticker}</span>
              <span className="ml-auto text-muted-foreground">{fmtDate(e.date)}{e.hour && ` · ${HOUR_LABEL[e.hour] ?? e.hour}`}</span>
            </div>
          ))}
        </div>
      )}

      {showCal && <EarningsModal events={earnings} onClose={() => setShowCal(false)} />}
    </div>
  );
}
