/**
 * Positions view for the Portfolio tab. Desktop: a dense multi-column table
 * (mirrors a brokerage positions grid). iOS / narrow: the table is hidden and
 * each holding renders as a stacked card, so nothing overflows horizontally.
 * (Convention #8 — every UI change works on iOS.)
 */
import type { PortfolioPosition } from '@/types/portfolio';
import { cn } from '@/lib/utils';
import { maskMoney, maskSigned, money, num, pct, toneClass } from './format';

const _MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// Expiration "2027-01-15" -> "Jan-15-2027" (Fidelity style).
function fmtExpiry(iso: string | null): string {
  if (!iso) return '';
  const [y, m, d] = iso.split('-');
  const mi = Number(m) - 1;
  if (!y || Number.isNaN(mi) || mi < 0 || mi > 11) return iso;
  return `${_MONTHS[mi]}-${d}-${y}`;
}

// Main label for a holding: options read like Fidelity ("NVDA 210 Call"),
// stocks/ETFs just show the ticker.
function positionTitle(p: PortfolioPosition): string {
  if (p.kind !== 'option') return p.symbol;
  const cp = p.option_type ? (p.option_type.toUpperCase().startsWith('C') ? 'Call' : 'Put') : '';
  const strike = p.strike != null ? String(p.strike) : '';
  return [p.underlying || p.symbol, strike, cp].filter(Boolean).join(' ');
}

// Secondary line: option expiration, else the company name.
function positionSubtitle(p: PortfolioPosition): string {
  return p.kind === 'option' ? fmtExpiry(p.expiration) : (p.name || '');
}

function hasWeek52(p: PortfolioPosition): boolean {
  return p.week52_low != null && p.week52_high != null && p.last_price != null;
}

/**
 * Fidelity-style 52-week range: low ─── ● ─── high, with a marker at the current
 * price and the traversed portion tinted. `flex-1` lets it fill a table cell; on a
 * card it spans the full width. Colour of the marker tracks position in the range
 * (near the low = red, near the high = green).
 */
function Week52Bar({ p, className }: { p: PortfolioPosition; className?: string }) {
  if (!hasWeek52(p)) return <span className="text-muted-foreground">—</span>;
  const low = p.week52_low as number;
  const high = p.week52_high as number;
  const last = p.last_price as number;
  const span = high - low;
  const posPct = span > 0 ? Math.min(100, Math.max(0, ((last - low) / span) * 100)) : 50;
  const markerTone = posPct >= 66 ? 'bg-emerald-500' : posPct <= 33 ? 'bg-rose-500' : 'bg-primary';
  return (
    <div className={cn('flex items-center gap-1.5', className)}>
      <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">{money(low, { compact: true })}</span>
      <div className="relative h-1 min-w-[3rem] flex-1 rounded-full bg-muted">
        <div className="absolute inset-y-0 left-0 rounded-full bg-primary/25" style={{ width: `${posPct}%` }} />
        <div
          className={cn('absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-background', markerTone)}
          style={{ left: `${posPct}%` }}
          title={`${money(last)} · ${posPct.toFixed(0)}% of 52-wk range`}
        />
      </div>
      <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">{money(high, { compact: true })}</span>
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
  const subToday = positions.reduce((s, p) => s + (p.day_change ?? 0), 0);
  const subValue = positions.reduce((s, p) => s + (p.current_value ?? 0), 0);
  const subGain = positions.reduce((s, p) => s + (p.total_gain ?? 0), 0);
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{title}</span>
        <span className="text-[11px] text-muted-foreground">({positions.length})</span>
      </div>
      {/* Mobile / iOS: stacked cards */}
      <div className="space-y-2 md:hidden">
        {positions.map((p, i) => (
          <div key={`${p.symbol}-${i}`} className="rounded-lg border border-border/60 bg-card p-3">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="font-mono text-sm font-semibold">{positionTitle(p)}</div>
                {positionSubtitle(p) && <div className="truncate text-[11px] text-muted-foreground">{positionSubtitle(p)}</div>}
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
            {hasWeek52(p) && (
              <div className="mt-2.5">
                <div className="mb-1 text-[10px] text-muted-foreground">52-week range</div>
                <Week52Bar p={p} />
              </div>
            )}
          </div>
        ))}
        {/* Mobile subtotal row */}
        <div className="flex items-center justify-between rounded-lg border-2 border-border bg-muted/40 px-3 py-2 text-xs font-semibold">
          <span>{title} subtotal</span>
          <div className="flex items-center gap-3 tabular-nums">
            <span className={toneClass(subToday)} title="Today">{maskSigned(subToday, masked)}</span>
            <span className={toneClass(subGain)} title="Total gain/loss">{maskSigned(subGain, masked)}</span>
            <span title="Value">{maskMoney(subValue, masked)}</span>
          </div>
        </div>
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
              <tr key={`${p.symbol}-${i}`} className="h-12 border-b border-border/40 hover:bg-muted/30 [&>td]:align-middle [&>td]:whitespace-nowrap">
                <td className="px-2 py-1">
                  <div className="font-mono font-semibold leading-tight">{positionTitle(p)}</div>
                  <div className="h-[13px] max-w-[180px] truncate text-[10px] leading-tight text-muted-foreground">
                    {positionSubtitle(p)}
                  </div>
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
                <td className="px-2 py-2"><div className="w-32"><Week52Bar p={p} /></div></td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t-2 border-border bg-muted/40 font-semibold">
              <td className="px-2 py-2" colSpan={2}>{title} subtotal</td>
              <td className={cn('px-2 py-2 text-right tabular-nums', toneClass(subToday))}>{maskSigned(subToday, masked)}</td>
              <td />
              <td className={cn('px-2 py-2 text-right tabular-nums', toneClass(subGain))}>{maskSigned(subGain, masked)}</td>
              <td />
              <td className="px-2 py-2 text-right tabular-nums">{maskMoney(subValue, masked)}</td>
              <td colSpan={5} />
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}
