/**
 * Catalyst calendar for the Market summary, rendered as an actual month grid.
 * Each day cell shows category-colored markers for its catalysts (earnings +
 * curated macro/policy events from the backend); tapping a day lists that day's
 * events below (earnings rows click through to research). Prev/next month nav.
 * Responsive (convention #8) — the grid stays 7 columns and the detail stacks.
 */
import { marketApi, type Catalyst } from '@/services/market-api';
import { cn } from '@/lib/utils';
import { CalendarClock, ChevronLeft, ChevronRight } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

const CAT_META: Record<string, { label: string; dot: string; chip: string }> = {
  earnings: { label: 'Earnings', dot: 'bg-sky-500', chip: 'bg-sky-500/15 text-sky-500' },
  fed: { label: 'Fed', dot: 'bg-amber-500', chip: 'bg-amber-500/15 text-amber-500' },
  inflation: { label: 'Inflation', dot: 'bg-orange-500', chip: 'bg-orange-500/15 text-orange-500' },
  jobs: { label: 'Jobs', dot: 'bg-violet-500', chip: 'bg-violet-500/15 text-violet-500' },
  tax_policy: { label: 'Tax/ITC', dot: 'bg-emerald-500', chip: 'bg-emerald-500/15 text-emerald-500' },
  energy_policy: { label: 'Energy/IRA', dot: 'bg-teal-500', chip: 'bg-teal-500/15 text-teal-500' },
};
const meta = (c: string) => CAT_META[c] ?? { label: c, dot: 'bg-muted-foreground', chip: 'bg-muted text-muted-foreground' };

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const WD = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];

function iso(y: number, m: number, d: number): string {
  return `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}
function localToday(): string {
  const n = new Date();
  return iso(n.getFullYear(), n.getMonth(), n.getDate());
}

export function CatalystCalendar({ tickers, onTicker }: { tickers: string[]; onTicker: (t: string) => void }) {
  const [items, setItems] = useState<Catalyst[]>([]);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<{ y: number; m: number }>(() => {
    const n = new Date();
    return { y: n.getFullYear(), m: n.getMonth() };
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [monthPinned, setMonthPinned] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    const h = setTimeout(() => {
      marketApi
        .getCatalysts(tickers)
        .then((r) => { if (alive) setItems(r.catalysts); })
        .catch(() => {})
        .finally(() => { if (alive) setLoading(false); });
    }, 400);
    return () => { alive = false; clearTimeout(h); };
  }, [tickers.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  const byDate = useMemo(() => {
    const map = new Map<string, Catalyst[]>();
    for (const c of items) {
      if (!c.date) continue;
      const list = map.get(c.date) ?? map.set(c.date, []).get(c.date)!;
      list.push(c);
    }
    return map;
  }, [items]);

  // Open on the month of the earliest upcoming catalyst (once), so the calendar
  // isn't blank if today's month has none.
  useEffect(() => {
    if (monthPinned || items.length === 0) return;
    const earliest = items.map((c) => c.date).filter(Boolean).sort()[0];
    if (earliest) {
      const [y, m] = earliest.split('-').map(Number);
      setView({ y, m: m - 1 });
    }
  }, [items, monthPinned]);

  const today = localToday();
  const { y, m } = view;
  const firstWeekday = new Date(y, m, 1).getDay();
  const daysInMonth = new Date(y, m + 1, 0).getDate();
  const cells: (number | null)[] = [...Array(firstWeekday).fill(null), ...Array.from({ length: daysInMonth }, (_, i) => i + 1)];
  while (cells.length % 7 !== 0) cells.push(null);

  const step = (delta: number) => {
    setMonthPinned(true);
    setSelected(null);
    setView((v) => {
      const d = new Date(v.y, v.m + delta, 1);
      return { y: d.getFullYear(), m: d.getMonth() };
    });
  };

  const selectedEvents = selected ? (byDate.get(selected) ?? []) : [];
  const monthEventCount = cells.reduce<number>((n, d) => n + (d ? (byDate.get(iso(y, m, d))?.length ?? 0) : 0), 0);

  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="mb-3 flex items-center gap-2">
        <CalendarClock className="h-4 w-4 text-muted-foreground" />
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Catalyst calendar</span>
        <div className="ml-auto flex items-center gap-1">
          <button type="button" onClick={() => step(-1)} className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground">
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="min-w-[7.5rem] text-center text-xs font-medium">{MONTHS[m]} {y}</span>
          <button type="button" onClick={() => step(1)} className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground">
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Weekday header */}
      <div className="grid grid-cols-7 gap-1 text-center text-[10px] font-medium text-muted-foreground">
        {WD.map((d, i) => <div key={i}>{d}</div>)}
      </div>

      {/* Day grid */}
      <div className="mt-1 grid grid-cols-7 gap-1">
        {cells.map((d, i) => {
          if (d === null) return <div key={i} className="aspect-square" />;
          const dayIso = iso(y, m, d);
          const evs = byDate.get(dayIso) ?? [];
          const isToday = dayIso === today;
          const isSelected = dayIso === selected;
          const cats = [...new Set(evs.map((e) => e.category))].slice(0, 4);
          return (
            <button
              key={i}
              type="button"
              onClick={() => setSelected(isSelected ? null : dayIso)}
              className={cn(
                'flex aspect-square flex-col items-center justify-start rounded-md border p-0.5 text-[11px] transition-colors',
                isSelected ? 'border-primary bg-primary/10' : evs.length ? 'border-border/60 hover:bg-muted/50' : 'border-transparent',
                isToday && !isSelected && 'ring-1 ring-inset ring-primary/50',
              )}
            >
              <span className={cn('tabular-nums', isToday ? 'font-bold text-primary' : evs.length ? 'font-medium' : 'text-muted-foreground')}>{d}</span>
              {cats.length > 0 && (
                <span className="mt-auto flex flex-wrap items-center justify-center gap-0.5 pb-0.5">
                  {cats.map((c, j) => <span key={j} className={cn('h-1.5 w-1.5 rounded-full', meta(c).dot)} />)}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Detail: selected day, else a hint */}
      <div className="mt-3 border-t border-border/60 pt-3">
        {loading && items.length === 0 ? (
          <p className="text-xs text-muted-foreground">Loading catalysts…</p>
        ) : selected && selectedEvents.length > 0 ? (
          <div className="space-y-1.5">
            <div className="text-[11px] font-medium text-muted-foreground">
              {MONTHS[Number(selected.split('-')[1]) - 1]} {Number(selected.split('-')[2])}
            </div>
            {selectedEvents.map((c, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className={cn('shrink-0 rounded px-1.5 py-0.5 text-[9px] font-medium', meta(c.category).chip)}>{meta(c.category).label}</span>
                {c.ticker ? (
                  <button type="button" onClick={() => onTicker(c.ticker as string)} className="min-w-0 truncate text-left text-xs hover:underline">
                    <span className="font-mono font-semibold">{c.ticker}</span> earnings{c.hour ? <span className="text-muted-foreground"> · {c.hour}</span> : null}
                  </button>
                ) : (
                  <span className="min-w-0 truncate text-xs">
                    {c.title}{c.expected && <span className="ml-1 text-[9px] text-muted-foreground">(expected)</span>}
                  </span>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            {monthEventCount > 0
              ? 'Tap a highlighted day to see its catalysts.'
              : 'No catalysts this month — use the arrows to browse.'}
          </p>
        )}
      </div>
    </div>
  );
}
