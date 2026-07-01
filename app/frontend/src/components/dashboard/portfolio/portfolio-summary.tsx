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

const ALLOC_COLORS = ['bg-sky-500', 'bg-violet-500', 'bg-emerald-500', 'bg-amber-500', 'bg-rose-500', 'bg-teal-500', 'bg-indigo-500', 'bg-orange-500'];

function Allocation({ account, masked }: { account: PortfolioAccount; masked: boolean }) {
  const withValue = account.positions.filter((p) => (p.current_value ?? 0) > 0);
  const top = [...withValue].sort((a, b) => (b.current_value ?? 0) - (a.current_value ?? 0)).slice(0, 8);
  const shownTotal = top.reduce((s, p) => s + (p.current_value ?? 0), 0);
  const other = (account.total_value ?? 0) - shownTotal - (account.cash ?? 0);

  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Allocation</div>
      <div className="mt-3 flex h-3 w-full overflow-hidden rounded-full bg-muted">
        {top.map((p, i) => (
          <div key={p.symbol} className={ALLOC_COLORS[i % ALLOC_COLORS.length]} style={{ width: `${p.pct_of_account ?? 0}%` }} title={`${p.symbol} ${pct(p.pct_of_account, false)}`} />
        ))}
      </div>
      <div className="mt-3 space-y-1.5">
        {top.map((p, i) => (
          <div key={p.symbol} className="flex items-center gap-2 text-xs">
            <span className={cn('h-2.5 w-2.5 shrink-0 rounded-sm', ALLOC_COLORS[i % ALLOC_COLORS.length])} />
            <span className="font-mono">{p.symbol}</span>
            <span className="ml-auto tabular-nums text-muted-foreground">{maskMoney(p.current_value, masked)}</span>
            <span className="w-12 text-right tabular-nums">{pct(p.pct_of_account, false)}</span>
          </div>
        ))}
        {other > 1 && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm bg-muted-foreground/40" />
            <span>Cash & other</span>
            <span className="ml-auto tabular-nums">{maskMoney(other + (account.cash ?? 0), masked)}</span>
          </div>
        )}
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
