/**
 * Catalyst calendar for the Market summary: per-ticker earnings merged with the
 * curated macro/policy events (Fed, CPI/PCE/jobs, IRA/45X/FEOC/ITC) from the
 * backend, chronological and grouped by date. Earnings rows are clickable → open
 * that ticker's research card. Responsive (convention #8).
 */
import { marketApi, type Catalyst } from '@/services/market-api';
import { cn } from '@/lib/utils';
import { CalendarClock } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

const CAT_META: Record<string, { label: string; cls: string }> = {
  earnings: { label: 'Earnings', cls: 'bg-sky-500/15 text-sky-500' },
  fed: { label: 'Fed', cls: 'bg-amber-500/15 text-amber-500' },
  inflation: { label: 'Inflation', cls: 'bg-orange-500/15 text-orange-500' },
  jobs: { label: 'Jobs', cls: 'bg-violet-500/15 text-violet-500' },
  tax_policy: { label: 'Tax/ITC', cls: 'bg-emerald-500/15 text-emerald-500' },
  energy_policy: { label: 'Energy/IRA', cls: 'bg-teal-500/15 text-teal-500' },
};

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function fmtDate(iso: string): string {
  const [, m, d] = iso.split('-');
  return `${MONTHS[Number(m) - 1] ?? m} ${Number(d)}`;
}
function weekday(iso: string): string {
  return DAYS[new Date(`${iso}T00:00:00`).getDay()] ?? '';
}

export function CatalystCalendar({ tickers, onTicker }: { tickers: string[]; onTicker: (t: string) => void }) {
  const [items, setItems] = useState<Catalyst[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    // Debounced: the watchlist tickers flip [] → full → [] transiently while the
    // sidebar loads, and without this an empty-ticker fetch can resolve last and
    // wipe the earnings. The debounce lets tickers settle before we fetch once.
    const h = setTimeout(() => {
      marketApi
        .getCatalysts(tickers)
        .then((r) => { if (alive) setItems(r.catalysts); })
        .catch(() => {})
        .finally(() => { if (alive) setLoading(false); });
    }, 400);
    return () => { alive = false; clearTimeout(h); };
  }, [tickers.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  const groups = useMemo(() => {
    const map = new Map<string, Catalyst[]>();
    for (const c of items) {
      const list = map.get(c.date) ?? map.set(c.date, []).get(c.date)!;
      list.push(c);
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b)).slice(0, 40);
  }, [items]);

  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="mb-3 flex items-center gap-2">
        <CalendarClock className="h-4 w-4 text-muted-foreground" />
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Catalyst calendar</span>
      </div>
      {loading && items.length === 0 ? (
        <p className="text-xs text-muted-foreground">Loading catalysts…</p>
      ) : groups.length === 0 ? (
        <p className="text-xs italic text-muted-foreground">No upcoming catalysts in the window.</p>
      ) : (
        <div className="max-h-96 space-y-2.5 overflow-y-auto pr-1">
          {groups.map(([date, evs]) => (
            <div key={date} className="flex gap-3">
              <div className="w-11 flex-shrink-0 pt-0.5 text-right">
                <div className="text-xs font-semibold tabular-nums">{fmtDate(date)}</div>
                <div className="text-[9px] uppercase text-muted-foreground">{weekday(date)}</div>
              </div>
              <div className="min-w-0 flex-1 space-y-1 border-l border-border/60 pl-3">
                {evs.map((c, i) => {
                  const meta = CAT_META[c.category] ?? { label: c.category, cls: 'bg-muted text-muted-foreground' };
                  return (
                    <div key={i} className="flex items-center gap-2">
                      <span className={cn('shrink-0 rounded px-1.5 py-0.5 text-[9px] font-medium', meta.cls)}>{meta.label}</span>
                      {c.ticker ? (
                        <button type="button" onClick={() => onTicker(c.ticker as string)} className="min-w-0 truncate text-left text-xs hover:underline">
                          <span className="font-mono font-semibold">{c.ticker}</span> earnings
                          {c.hour ? <span className="text-muted-foreground"> · {c.hour}</span> : null}
                        </button>
                      ) : (
                        <span className="min-w-0 truncate text-xs">
                          {c.title}
                          {c.expected && <span className="ml-1 text-[9px] text-muted-foreground">(expected)</span>}
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
