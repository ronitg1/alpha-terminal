/**
 * Positions view for the Portfolio tab. Desktop: a dense multi-column table
 * (mirrors a brokerage positions grid). iOS / narrow: the table is hidden and
 * each holding renders as a stacked card, so nothing overflows horizontally.
 * (Convention #8 — every UI change works on iOS.)
 */
import type { PortfolioPosition } from '@/types/portfolio';
import { cn } from '@/lib/utils';
import { maskMoney, maskSigned, money, num, pct, toneClass } from './format';

function optionTag(p: PortfolioPosition): string | null {
  if (p.kind !== 'option') return null;
  const parts = [p.option_type, p.strike ? `$${p.strike}` : null, p.expiration].filter(Boolean);
  return parts.join(' ');
}

function Week52({ p }: { p: PortfolioPosition }) {
  if (p.week52_low === null || p.week52_high === null || p.last_price === null) {
    return <span className="text-muted-foreground">—</span>;
  }
  const span = p.week52_high - p.week52_low;
  const posPct = span > 0 ? Math.min(100, Math.max(0, ((p.last_price - p.week52_low) / span) * 100)) : 50;
  return (
    <div className="flex items-center gap-1">
      <span className="text-[10px] text-muted-foreground">{money(p.week52_low, { compact: true })}</span>
      <div className="relative h-1 w-14 rounded-full bg-muted">
        <div className="absolute top-1/2 h-2 w-2 -translate-y-1/2 rounded-full bg-primary" style={{ left: `calc(${posPct}% - 4px)` }} />
      </div>
      <span className="text-[10px] text-muted-foreground">{money(p.week52_high, { compact: true })}</span>
    </div>
  );
}

export function PositionsTable({ positions, masked = false }: { positions: readonly PortfolioPosition[]; masked?: boolean }) {
  if (positions.length === 0) {
    return <p className="p-4 text-sm italic text-muted-foreground">No positions in this account.</p>;
  }
  const options = positions.filter((p) => p.kind === 'option');
  const etfs = positions.filter((p) => p.kind !== 'option' && ETF_BUCKETS.has(p.sector || ''));
  const stocks = positions.filter((p) => p.kind !== 'option' && !ETF_BUCKETS.has(p.sector || ''));

  return (
    <div className="space-y-4">
      {stocks.length > 0 && <PositionsGroup title="Stocks" positions={stocks} masked={masked} />}
      {etfs.length > 0 && <PositionsGroup title="ETFs & Funds" positions={etfs} masked={masked} />}
      {options.length > 0 && <PositionsGroup title="Options" positions={options} masked={masked} />}
    </div>
  );
}

const ETF_BUCKETS = new Set(['Market Index', 'Funds & ETFs', 'Cash']);

function PositionsGroup({ title, positions, masked }: { title: string; positions: readonly PortfolioPosition[]; masked: boolean }) {
  const subtotalValue = positions.reduce((s, p) => s + (p.current_value ?? 0), 0);
  const subtotalGain = positions.reduce((s, p) => s + (p.total_gain ?? 0), 0);
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{title}</span>
        <span className="text-[11px] text-muted-foreground">({positions.length})</span>
        <span className="ml-auto text-[11px] font-semibold tabular-nums">{maskMoney(subtotalValue, masked)}</span>
        <span className={cn('text-[11px] tabular-nums', toneClass(subtotalGain))}>{maskSigned(subtotalGain, masked)}</span>
      </div>
      {/* Mobile / iOS: stacked cards */}
      <div className="space-y-2 md:hidden">
        {positions.map((p, i) => (
          <div key={`${p.symbol}-${i}`} className="rounded-lg border border-border/60 bg-card p-3">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="font-mono text-sm font-semibold">{p.symbol}</div>
                {optionTag(p) && <div className="text-[11px] text-muted-foreground">{optionTag(p)}</div>}
                {p.name && <div className="truncate text-[11px] text-muted-foreground">{p.name}</div>}
              </div>
              <div className="text-right">
                <div className="text-sm font-semibold">{maskMoney(p.current_value, masked)}</div>
                <div className={cn('text-[11px]', toneClass(p.day_change))}>
                  {maskSigned(p.day_change, masked)} ({pct(p.day_change_pct)})
                </div>
              </div>
            </div>
            <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
              <div>
                <div className="text-muted-foreground">Qty</div>
                <div>{num(p.quantity)}</div>
              </div>
              <div>
                <div className="text-muted-foreground">Total G/L</div>
                <div className={toneClass(p.total_gain)}>{maskSigned(p.total_gain, masked)}</div>
              </div>
              <div>
                <div className="text-muted-foreground">% of acct</div>
                <div>{pct(p.pct_of_account, false)}</div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Desktop: full table (horizontal scroll only as a last resort) */}
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border text-left text-muted-foreground">
              <th className="px-2 py-2 font-medium">Symbol</th>
              <th className="px-2 py-2 text-right font-medium">Last</th>
              <th className="px-2 py-2 text-right font-medium">Today $</th>
              <th className="px-2 py-2 text-right font-medium">Today %</th>
              <th className="px-2 py-2 text-right font-medium">Total G/L</th>
              <th className="px-2 py-2 text-right font-medium">Total %</th>
              <th className="px-2 py-2 text-right font-medium">Value</th>
              <th className="px-2 py-2 text-right font-medium">% Acct</th>
              <th className="px-2 py-2 text-right font-medium">Qty</th>
              <th className="px-2 py-2 text-right font-medium">Avg cost</th>
              <th className="px-2 py-2 text-right font-medium">Cost basis</th>
              <th className="px-2 py-2 font-medium">52-wk range</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p, i) => (
              <tr key={`${p.symbol}-${i}`} className="border-b border-border/40 hover:bg-muted/30">
                <td className="px-2 py-2">
                  <div className="font-mono font-semibold">{p.symbol}</div>
                  {optionTag(p) ? (
                    <div className="text-[10px] text-muted-foreground">{optionTag(p)}</div>
                  ) : p.name ? (
                    <div className="max-w-[160px] truncate text-[10px] text-muted-foreground">{p.name}</div>
                  ) : null}
                </td>
                <td className="px-2 py-2 text-right tabular-nums">{money(p.last_price)}</td>
                <td className={cn('px-2 py-2 text-right tabular-nums', toneClass(p.day_change))}>{maskSigned(p.day_change, masked)}</td>
                <td className={cn('px-2 py-2 text-right tabular-nums', toneClass(p.day_change_pct))}>{pct(p.day_change_pct)}</td>
                <td className={cn('px-2 py-2 text-right tabular-nums', toneClass(p.total_gain))}>{maskSigned(p.total_gain, masked)}</td>
                <td className={cn('px-2 py-2 text-right tabular-nums', toneClass(p.total_gain_pct))}>{pct(p.total_gain_pct)}</td>
                <td className="px-2 py-2 text-right font-medium tabular-nums">{maskMoney(p.current_value, masked)}</td>
                <td className="px-2 py-2 text-right tabular-nums">{pct(p.pct_of_account, false)}</td>
                <td className="px-2 py-2 text-right tabular-nums">{num(p.quantity)}</td>
                <td className="px-2 py-2 text-right tabular-nums">{money(p.avg_cost)}</td>
                <td className="px-2 py-2 text-right tabular-nums">{maskMoney(p.cost_basis_total, masked)}</td>
                <td className="px-2 py-2"><Week52 p={p} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
