/**
 * Catalyst calendar for the Market summary, rendered as a real calendar grid with
 * a Week / Month toggle. Each day cell shows category-colored markers for its
 * catalysts (earnings + curated macro/policy events); tapping a day lists that
 * day's events below (earnings rows click through to research). Prev/next steps by
 * week or month. Responsive (convention #8) — 7 columns, detail stacks below.
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

/** Compact label shown inside a day cell: the ticker for earnings, else a keyword
 *  pulled from the macro title (FOMC/CPI/PCE/45X/FEOC/ITC/Jobs), else the category. */
function shortLabel(c: Catalyst): string {
  if (c.ticker) return c.ticker;
  const kw = (c.title || '').match(/FOMC|CPI|PCE|45X|FEOC|ITC|PTC|GDP|jobs|payrolls/i);
  if (kw) {
    const w = kw[0].toLowerCase();
    return w === 'payrolls' || w === 'jobs' ? 'Jobs' : kw[0].toUpperCase();
  }
  return meta(c.category).label;
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const MON_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const WD = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];

function isoOf(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function addDays(d: Date, n: number): Date {
  const r = new Date(d);
  r.setDate(d.getDate() + n);
  return r;
}

type Mode = 'week' | 'month';
interface Cell { date: Date | null }

export function CatalystCalendar({ tickers, onTicker }: { tickers: string[]; onTicker: (t: string) => void }) {
  const [items, setItems] = useState<Catalyst[]>([]);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<Mode>('week');
  const [anchor, setAnchor] = useState<Date>(() => new Date());
  const [selected, setSelected] = useState<string | null>(null);
  const [pinned, setPinned] = useState(false);

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

  // Open on the period containing the earliest upcoming catalyst (once).
  useEffect(() => {
    if (pinned || items.length === 0) return;
    const earliest = items.map((c) => c.date).filter(Boolean).sort()[0];
    if (earliest) {
      const [y, mo, d] = earliest.split('-').map(Number);
      setAnchor(new Date(y, mo - 1, d));
    }
  }, [items, pinned]);

  const today = isoOf(new Date());

  // Build the visible cells for the current mode.
  const cells: Cell[] = useMemo(() => {
    if (mode === 'week') {
      const start = addDays(anchor, -anchor.getDay()); // Sunday
      return Array.from({ length: 7 }, (_, i) => ({ date: addDays(start, i) }));
    }
    const y = anchor.getFullYear();
    const m = anchor.getMonth();
    const firstWeekday = new Date(y, m, 1).getDay();
    const daysInMonth = new Date(y, m + 1, 0).getDate();
    const out: Cell[] = [...Array(firstWeekday).fill(null).map(() => ({ date: null }))];
    for (let d = 1; d <= daysInMonth; d++) out.push({ date: new Date(y, m, d) });
    while (out.length % 7 !== 0) out.push({ date: null });
    return out;
  }, [mode, anchor]);

  const step = (delta: number) => {
    setPinned(true);
    setSelected(null);
    setAnchor((a) => (mode === 'week' ? addDays(a, delta * 7) : new Date(a.getFullYear(), a.getMonth() + delta, 1)));
  };

  const periodLabel = useMemo(() => {
    if (mode === 'month') return `${MONTHS[anchor.getMonth()]} ${anchor.getFullYear()}`;
    const start = addDays(anchor, -anchor.getDay());
    const end = addDays(start, 6);
    const left = `${MON_ABBR[start.getMonth()]} ${start.getDate()}`;
    const right = start.getMonth() === end.getMonth() ? `${end.getDate()}` : `${MON_ABBR[end.getMonth()]} ${end.getDate()}`;
    return `${left} – ${right}`;
  }, [mode, anchor]);

  const selectedEvents = selected ? (byDate.get(selected) ?? []) : [];
  const periodEventCount = cells.reduce<number>((n, c) => n + (c.date ? (byDate.get(isoOf(c.date))?.length ?? 0) : 0), 0);

  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <CalendarClock className="h-4 w-4 text-muted-foreground" />
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Catalyst calendar</span>
        {/* Week / Month toggle */}
        <div className="flex rounded-md bg-muted p-0.5 text-[11px]">
          {(['week', 'month'] as const).map((mo) => (
            <button
              key={mo}
              type="button"
              onClick={() => { setMode(mo); setSelected(null); }}
              className={cn('rounded px-2 py-0.5 font-medium capitalize', mode === mo ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground')}
            >
              {mo}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-1">
          <button type="button" onClick={() => step(-1)} className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground">
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="min-w-[6.5rem] text-center text-xs font-medium">{periodLabel}</span>
          <button type="button" onClick={() => step(1)} className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground">
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Weekday header */}
      <div className="grid grid-cols-7 gap-1 text-center text-[10px] font-medium text-muted-foreground">
        {WD.map((d, i) => <div key={i}>{d}</div>)}
      </div>

      {/* Day grid (week = 1 row, month = full grid) */}
      <div className="mt-1 grid grid-cols-7 gap-1">
        {cells.map((c, i) => {
          if (!c.date) return <div key={i} className={mode === 'week' ? 'min-h-[5rem]' : 'min-h-[3.5rem]'} />;
          const dayIso = isoOf(c.date);
          const evs = byDate.get(dayIso) ?? [];
          const isToday = dayIso === today;
          const isSelected = dayIso === selected;
          const shown = mode === 'week' ? 5 : 2; // chips to show inline before "+N"
          return (
            <button
              key={i}
              type="button"
              onClick={() => setSelected(isSelected ? null : dayIso)}
              className={cn(
                'flex flex-col items-stretch rounded-md border p-0.5 text-left transition-colors',
                mode === 'week' ? 'min-h-[5rem]' : 'min-h-[3.5rem]',
                isSelected ? 'border-primary bg-primary/10' : evs.length ? 'border-border/60 hover:bg-muted/50' : 'border-transparent',
                isToday && !isSelected && 'ring-1 ring-inset ring-primary/50',
              )}
            >
              <span className={cn('px-0.5 text-[11px] tabular-nums', isToday ? 'font-bold text-primary' : evs.length ? 'font-medium' : 'text-muted-foreground')}>{c.date.getDate()}</span>
              {evs.length > 0 && (
                <span className="mt-0.5 flex flex-col gap-0.5 overflow-hidden">
                  {evs.slice(0, shown).map((e, j) => (
                    <span key={j} className={cn('truncate rounded px-1 text-[8px] font-medium leading-[1.3]', meta(e.category).chip)}>
                      {shortLabel(e)}
                    </span>
                  ))}
                  {evs.length > shown && <span className="px-1 text-[8px] text-muted-foreground">+{evs.length - shown}</span>}
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
              {MON_ABBR[Number(selected.split('-')[1]) - 1]} {Number(selected.split('-')[2])}
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
            {periodEventCount > 0
              ? 'Tap a day to open its earnings for research.'
              : `No catalysts this ${mode} — use the arrows to browse.`}
          </p>
        )}
      </div>
    </div>
  );
}
