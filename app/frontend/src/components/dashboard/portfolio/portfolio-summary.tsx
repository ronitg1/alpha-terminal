/**
 * Summary tab for the Portfolio view — helpful holdings-derived cards (no
 * retirement/brokerage-branding). M1 ships Totals + Allocation + Top/bottom
 * movers; Markets, Market movers, and Portfolio events arrive in M2/M3.
 * Responsive: one column on iOS, multi-column on desktop (convention #8).
 */
import type { PortfolioAccount, PortfolioPosition } from '@/types/portfolio';
import { cn } from '@/lib/utils';
import { maskMoney, maskSigned, pct, toneClass } from './format';

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={cn('text-sm font-semibold tabular-nums', tone)}>{value}</div>
    </div>
  );
}

function Totals({ account, masked }: { account: PortfolioAccount; masked: boolean }) {
  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {account.label}
      </div>
      <div className="mt-1 text-2xl font-bold tabular-nums">{maskMoney(account.total_value, masked)}</div>
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Today" value={`${maskSigned(account.day_change, masked)} (${pct(account.day_change_pct)})`} tone={toneClass(account.day_change)} />
        <Stat label="Total gain/loss" value={`${maskSigned(account.total_gain, masked)} (${pct(account.total_gain_pct)})`} tone={toneClass(account.total_gain)} />
        <Stat label="Cash" value={maskMoney(account.cash, masked)} />
        <Stat label="Positions" value={String(account.positions.length)} />
      </div>
    </div>
  );
}

const ALLOC_COLORS = ['bg-sky-500', 'bg-violet-500', 'bg-emerald-500', 'bg-amber-500', 'bg-rose-500', 'bg-teal-500', 'bg-indigo-500', 'bg-orange-500', 'bg-lime-500', 'bg-fuchsia-500'];

interface AllocName { symbol: string; value: number }
interface AllocGroup { label: string; value: number; names: AllocName[] }

// Sort order so Cash and Market Index sit at the bottom, sectors (by size) on top.
const GROUP_RANK = (label: string): number => (label === 'Cash' ? 2 : label === 'Market Index' ? 1 : 0);

function buildGroups(account: PortfolioAccount): { groups: AllocGroup[]; total: number } {
  // Roll every position onto its underlying (NVDA shares + NVDA calls => NVDA),
  // keyed by the classified bucket so options land in the same sector as the stock.
  const byUnderlying = new Map<string, { value: number; bucket: string }>();
  for (const p of account.positions) {
    const value = p.current_value ?? 0;
    if (value <= 0) continue;
    const key = p.underlying || p.symbol;
    const bucket = p.sector || 'Other';
    const prev = byUnderlying.get(key);
    if (prev) prev.value += value;
    else byUnderlying.set(key, { value, bucket });
  }
  const cash = account.cash ?? 0;

  const groupMap = new Map<string, AllocGroup>();
  const add = (label: string, symbol: string, value: number) => {
    const g = groupMap.get(label) ?? { label, value: 0, names: [] };
    g.value += value;
    if (symbol) g.names.push({ symbol, value });
    groupMap.set(label, g);
  };
  for (const [symbol, { value, bucket }] of byUnderlying) add(bucket, symbol, value);
  if (cash > 1) add('Cash', '', cash); // settled cash (not a position) folds in

  const groups = [...groupMap.values()]
    .map((g) => ({ ...g, names: g.names.sort((a, b) => b.value - a.value) }))
    .sort((a, b) => GROUP_RANK(a.label) - GROUP_RANK(b.label) || b.value - a.value);
  const total = groups.reduce((s, g) => s + g.value, 0);
  return { groups, total };
}

function Allocation({ account, masked }: { account: PortfolioAccount; masked: boolean }) {
  const { groups, total } = buildGroups(account);
  if (total <= 0) return null;
  const pctOf = (v: number) => (v / total) * 100;

  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Allocation by sector</div>
      <div className="mt-3 flex h-3 w-full overflow-hidden rounded-full bg-muted">
        {groups.map((g, i) => (
          <div key={g.label} className={ALLOC_COLORS[i % ALLOC_COLORS.length]} style={{ width: `${pctOf(g.value)}%` }} title={`${g.label} ${pct(pctOf(g.value), false)}`} />
        ))}
      </div>
      <div className="mt-3 space-y-2">
        {groups.map((g, i) => (
          <div key={g.label}>
            <div className="flex items-center gap-2 text-xs font-medium">
              <span className={cn('h-2.5 w-2.5 shrink-0 rounded-sm', ALLOC_COLORS[i % ALLOC_COLORS.length])} />
              <span>{g.label}</span>
              <span className="ml-auto tabular-nums text-muted-foreground">{maskMoney(g.value, masked)}</span>
              <span className="w-12 text-right tabular-nums">{pct(pctOf(g.value), false)}</span>
            </div>
            {g.names.slice(0, 3).map((n) => (
              <div key={n.symbol} className="flex items-center gap-2 pl-[18px] text-[11px] text-muted-foreground">
                <span className="font-mono">{n.symbol}</span>
                <span className="ml-auto tabular-nums">{maskMoney(n.value, masked)}</span>
                <span className="w-12 text-right tabular-nums">{pct(pctOf(n.value), false)}</span>
              </div>
            ))}
            {g.names.length > 3 && (
              <div className="pl-[18px] text-[10px] text-muted-foreground/70">+{g.names.length - 3} more</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function MoverRow({ p, masked }: { p: PortfolioPosition; masked: boolean }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="font-mono font-medium">{p.symbol}</span>
      {p.name && <span className="hidden truncate text-[11px] text-muted-foreground sm:inline">{p.name}</span>}
      <span className={cn('ml-auto tabular-nums', toneClass(p.day_change))}>{maskSigned(p.day_change, masked)}</span>
      <span className={cn('w-16 text-right tabular-nums', toneClass(p.day_change_pct))}>{pct(p.day_change_pct)}</span>
    </div>
  );
}

function Movers({ account, masked }: { account: PortfolioAccount; masked: boolean }) {
  const ranked = account.positions
    .filter((p) => p.day_change_pct !== null)
    .sort((a, b) => (b.day_change_pct ?? 0) - (a.day_change_pct ?? 0));
  const gainers = ranked.filter((p) => (p.day_change_pct ?? 0) > 0).slice(0, 5);
  const losers = ranked.filter((p) => (p.day_change_pct ?? 0) < 0).slice(-5).reverse();

  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Top &amp; bottom movers today</div>
      {ranked.length === 0 ? (
        <p className="mt-2 text-xs italic text-muted-foreground">No intraday change data yet.</p>
      ) : (
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <div className="text-[10px] font-medium uppercase text-emerald-500">Gainers</div>
            {gainers.length ? gainers.map((p) => <MoverRow key={p.symbol} p={p} masked={masked} />) : <p className="text-xs text-muted-foreground">—</p>}
          </div>
          <div className="space-y-1.5">
            <div className="text-[10px] font-medium uppercase text-rose-500">Losers</div>
            {losers.length ? losers.map((p) => <MoverRow key={p.symbol} p={p} masked={masked} />) : <p className="text-xs text-muted-foreground">—</p>}
          </div>
        </div>
      )}
    </div>
  );
}

export function PortfolioSummary({ account, masked = false }: { account: PortfolioAccount; masked?: boolean }) {
  return (
    <div className="space-y-3">
      <Totals account={account} masked={masked} />
      <div className="grid gap-3 lg:grid-cols-2">
        <Allocation account={account} masked={masked} />
        <Movers account={account} masked={masked} />
      </div>
      {/* M2/M3 placeholders — Markets, Market movers, Portfolio events land next. */}
    </div>
  );
}
