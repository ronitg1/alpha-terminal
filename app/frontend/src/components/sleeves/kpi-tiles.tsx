/**
 * KpiTiles — four headline tiles under the portfolio header.
 *
 * Big numbers, small labels. Each tile is a single line of meaning the
 * user can absorb in <1s. Allocation (sanity check that sleeves sum to
 * 100%), Weighted Conviction (allocation-weighted), High-conviction count,
 * Watchlist queue size.
 */
import { Card } from '@/components/ui/card';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { cn } from '@/lib/utils';
import { AlertTriangle, Check, ListChecks, Star, Target } from 'lucide-react';
import { useMemo } from 'react';
import { readoutForPortfolio } from './utils/derive-bias';

export function KpiTiles() {
  const { config, latestScan, watchlist } = useSleevesContext();

  const allocationTotal = useMemo(
    () =>
      (config?.sleeves ?? []).reduce((sum, s) => sum + s.allocation_pct, 0),
    [config],
  );
  const allocationOk = Math.abs(allocationTotal - 100) < 0.5;

  const readout = useMemo(
    () => readoutForPortfolio(config?.sleeves ?? [], latestScan),
    [config, latestScan],
  );

  const unscanned = watchlist.filter(
    (w) =>
      !(latestScan?.rows ?? []).some(
        (r) => r.ticker.toUpperCase() === w.ticker.toUpperCase(),
      ),
  ).length;

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 px-6 py-3">
      <Tile
        label="Allocation"
        icon={<Target className="h-3 w-3" />}
        value={`${allocationTotal.toFixed(0)}%`}
        sub={
          allocationOk ? (
            <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
              <Check className="h-3 w-3" />
              {config?.sleeves.length ?? 0} sleeves
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-400">
              <AlertTriangle className="h-3 w-3" />
              not 100%
            </span>
          )
        }
      />
      <Tile
        label="Weighted Conviction"
        icon={<span className="font-mono text-[10px]">Σ</span>}
        value={
          readout.scanned > 0
            ? `${Math.round(readout.weightedConv)}/100`
            : '—'
        }
        sub={
          readout.scanned > 0
            ? `${readout.scanned} ticker${readout.scanned === 1 ? '' : 's'} scanned`
            : 'no scan yet'
        }
      />
      <Tile
        label="High Conviction"
        icon={<Star className="h-3 w-3" />}
        value={`${readout.highConv}`}
        sub={
          readout.variant > 0
            ? `${readout.variant} with variant ✨`
            : 'no variant flags'
        }
        accent={readout.highConv > 0 ? 'positive' : undefined}
      />
      <Tile
        label="Watchlist"
        icon={<ListChecks className="h-3 w-3" />}
        value={`${watchlist.length}`}
        sub={
          watchlist.length === 0
            ? 'empty'
            : unscanned > 0
              ? `${unscanned} unscanned`
              : 'all scanned'
        }
      />
    </div>
  );
}

function Tile({
  label,
  icon,
  value,
  sub,
  accent,
}: {
  label: string;
  icon: React.ReactNode;
  value: string;
  sub: React.ReactNode;
  accent?: 'positive' | 'negative';
}) {
  return (
    <Card className="p-3">
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-muted-foreground">
        {icon}
        {label}
      </div>
      <div
        className={cn(
          'mt-1 text-2xl font-semibold font-mono leading-none',
          accent === 'positive' && 'text-emerald-600 dark:text-emerald-400',
          accent === 'negative' && 'text-rose-600 dark:text-rose-400',
        )}
      >
        {value}
      </div>
      <div className="mt-1 text-[11px] text-muted-foreground">{sub}</div>
    </Card>
  );
}
