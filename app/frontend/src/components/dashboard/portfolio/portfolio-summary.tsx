/**
 * Summary tab for the Portfolio view — helpful holdings-derived cards (no
 * retirement/brokerage-branding). M1 ships Totals + Allocation + Top/bottom
 * movers; Markets, Market movers, and Portfolio events arrive in M2/M3.
 * Responsive: one column on iOS, multi-column on desktop (convention #8).
 */
import type { PortfolioAccount, PortfolioPosition } from '@/types/portfolio';
import { cn } from '@/lib/utils';
import { AlertTriangle, ChevronDown, ChevronRight, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { maskMoney, maskSigned, pct, toneClass } from './format';
import { MarketCards } from './market-cards';
import { NewsCard } from './news-card';
import { PortfolioEvents } from './portfolio-events';

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={cn('text-sm font-semibold tabular-nums', tone)}>{value}</div>
    </div>
  );
}

function Totals({ account, masked }: { account: PortfolioAccount; masked: boolean }) {
  // Cash shown at the top = settled cash + money-market positions (SPAXX etc.),
  // which classify into the "Cash" bucket but are held as positions.
  const moneyMarket = account.positions
    .filter((p) => p.sector === 'Cash')
    .reduce((s, p) => s + (p.current_value ?? 0), 0);
  const cashTotal = (account.cash ?? 0) + moneyMarket;
  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {account.label}
      </div>
      <div className="mt-1 text-2xl font-bold tabular-nums">{maskMoney(account.total_value, masked)}</div>
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Today" value={`${maskSigned(account.day_change, masked)} (${pct(account.day_change_pct)})`} tone={toneClass(account.day_change)} />
        <Stat label="Total gain/loss" value={`${maskSigned(account.total_gain, masked)} (${pct(account.total_gain_pct)})`} tone={toneClass(account.total_gain)} />
        <Stat label="Cash" value={maskMoney(cashTotal, masked)} />
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
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  if (total <= 0) return null;
  const pctOf = (v: number) => (v / total) * 100;
  const toggle = (label: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(label) ? next.delete(label) : next.add(label);
      return next;
    });

  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Allocation by sector</div>
      <div className="mt-3 flex h-3 w-full overflow-hidden rounded-full bg-muted">
        {groups.map((g, i) => (
          <div key={g.label} className={ALLOC_COLORS[i % ALLOC_COLORS.length]} style={{ width: `${pctOf(g.value)}%` }} title={`${g.label} ${pct(pctOf(g.value), false)}`} />
        ))}
      </div>
      <div className="mt-3 space-y-1">
        {groups.map((g, i) => {
          const isOpen = expanded.has(g.label);
          const canExpand = g.names.length > 0;
          return (
            <div key={g.label}>
              <button
                type="button"
                onClick={() => canExpand && toggle(g.label)}
                className={cn('flex w-full items-center gap-2 rounded px-1 py-1 text-xs font-medium', canExpand && 'hover:bg-muted/40')}
              >
                {canExpand ? (
                  isOpen ? <ChevronDown className="h-3 w-3 text-muted-foreground" /> : <ChevronRight className="h-3 w-3 text-muted-foreground" />
                ) : (
                  <span className="w-3" />
                )}
                <span className={cn('h-2.5 w-2.5 shrink-0 rounded-sm', ALLOC_COLORS[i % ALLOC_COLORS.length])} />
                <span>{g.label}</span>
                <span className="ml-auto tabular-nums text-muted-foreground">{maskMoney(g.value, masked)}</span>
                <span className="w-12 text-right tabular-nums">{pct(pctOf(g.value), false)}</span>
              </button>
              {isOpen && (
                <div className="mb-1 space-y-0.5 pl-[26px]">
                  {g.names.map((n) => (
                    <div key={n.symbol} className="flex items-center gap-2 text-[11px] text-muted-foreground">
                      <span className="font-mono">{n.symbol}</span>
                      <span className="ml-auto tabular-nums">{maskMoney(n.value, masked)}</span>
                      <span className="w-12 text-right tabular-nums">{pct(pctOf(n.value), false)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Concentration / risk flags ──────────────────────────────────────────────
// Single-name and sector concentration matter more for risk than the allocation
// breakdown alone: one 15%+ position, or half the book in two sectors, is the
// thing to surface. Cash/Market-Index buckets are excluded from the risk view.

interface Flag { level: 'warn' | 'info'; text: string }

function Concentration({ account }: { account: PortfolioAccount }) {
  const { groups, total } = buildGroups(account);
  if (total <= 0) return null;

  const positions = groups
    .filter((g) => g.label !== 'Cash')
    .flatMap((g) => g.names.map((n) => ({ symbol: n.symbol, pct: (n.value / total) * 100 })))
    .filter((p) => p.symbol)
    .sort((a, b) => b.pct - a.pct);

  const sectors = groups
    .filter((g) => g.label !== 'Cash' && g.label !== 'Market Index')
    .map((g) => ({ label: g.label, pct: (g.value / total) * 100 }))
    .sort((a, b) => b.pct - a.pct);

  const top5 = positions.slice(0, 5).reduce((s, p) => s + p.pct, 0);
  const top2Sectors = sectors.slice(0, 2);
  const top2Sum = top2Sectors.reduce((s, x) => s + x.pct, 0);

  const flags: Flag[] = [];
  for (const p of positions.filter((p) => p.pct >= 15)) {
    flags.push({ level: 'warn', text: `${p.symbol} is ${p.pct.toFixed(1)}% of the book — single-name concentration` });
  }
  if (top2Sum >= 45 && top2Sectors.length === 2) {
    flags.push({
      level: 'warn',
      text: `${top2Sectors.map((s) => `${s.label} ${s.pct.toFixed(1)}%`).join(' + ')} = ${top2Sum.toFixed(0)}% in two sectors — consider diversifying`,
    });
  } else if (sectors[0]?.pct >= 35) {
    flags.push({ level: 'warn', text: `${sectors[0].label} is ${sectors[0].pct.toFixed(1)}% of the book` });
  }
  if (top5 >= 60) {
    flags.push({ level: 'info', text: `Top 5 positions are ${top5.toFixed(0)}% of the book` });
  }

  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="mb-3 flex items-center gap-2">
        {flags.some((f) => f.level === 'warn') ? (
          <AlertTriangle className="h-4 w-4 text-amber-500" />
        ) : (
          <ShieldCheck className="h-4 w-4 text-emerald-500" />
        )}
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Concentration &amp; risk</span>
        <span className="ml-auto text-[11px] text-muted-foreground">
          Top 5: <span className="font-medium text-foreground tabular-nums">{top5.toFixed(0)}%</span>
        </span>
      </div>

      {flags.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No single name above 15% and no sector above 35% — reasonably diversified.
        </p>
      ) : (
        <div className="space-y-1.5">
          {flags.map((f, i) => (
            <div key={i} className="flex items-start gap-2 text-xs">
              <span className={cn('mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full', f.level === 'warn' ? 'bg-amber-500' : 'bg-sky-500')} />
              <span className={f.level === 'warn' ? 'text-foreground' : 'text-muted-foreground'}>{f.text}</span>
            </div>
          ))}
        </div>
      )}

      {/* Largest positions bar */}
      {positions.length > 0 && (
        <div className="mt-3 space-y-1">
          <div className="text-[10px] font-medium uppercase text-muted-foreground">Largest positions</div>
          {positions.slice(0, 5).map((p) => (
            <div key={p.symbol} className="flex items-center gap-2">
              <span className="w-14 shrink-0 font-mono text-[11px] font-semibold">{p.symbol}</span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                <div className={cn('h-full rounded-full', p.pct >= 15 ? 'bg-amber-500' : 'bg-primary/60')} style={{ width: `${Math.min(100, p.pct)}%` }} />
              </div>
              <span className="w-10 text-right text-[11px] tabular-nums text-muted-foreground">{p.pct.toFixed(1)}%</span>
            </div>
          ))}
        </div>
      )}
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
    .filter((p) => p.kind === 'stock' && p.day_change_pct !== null)
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
        <Concentration account={account} />
      </div>
      <Movers account={account} masked={masked} />
      <div className="grid gap-3 lg:grid-cols-2">
        <PortfolioEvents account={account} />
        <NewsCard account={account} />
      </div>
      <MarketCards />
    </div>
  );
}
