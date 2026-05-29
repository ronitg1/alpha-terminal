/**
 * HighConvictionTiles — richer replacement for the old HighConvictionStrip.
 *
 * Each tile shows ticker, current price, 30d sparkline + % change, signal
 * pill with confidence, and a variant-perception marker. Click tile →
 * scrolls to the matching row in the sleeve section + selects the ticker
 * so the existing drill drawer opens with full context.
 *
 * Empty state: when no high-conviction signal is present, fall back to
 * top-3 by avg_confidence so the user always has somewhere to look first.
 */
import { Card } from '@/components/ui/card';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { cn } from '@/lib/utils';
import type { TickerRow } from '@/types/sleeves';
import { Sparkles, Star } from 'lucide-react';
import { useTickerData } from './hooks/use-ticker-data';
import { MiniSpark } from './mini-spark';
import { SignalPill } from './signal-pill';

const HIGH_CONV_THRESHOLD = 60;

function pickHighConviction(rows: TickerRow[]): TickerRow[] {
  const filtered = rows.filter(
    (r) =>
      r.avg_confidence >= HIGH_CONV_THRESHOLD ||
      r.has_variant_perception ||
      r.highlight !== 'neutral',
  );
  return filtered
    .slice()
    .sort((a, b) => Math.abs(b.weighted_score) - Math.abs(a.weighted_score));
}

export function HighConvictionTiles() {
  const { latestScan, selectTicker } = useSleevesContext();
  const rows = latestScan?.rows ?? [];

  if (rows.length === 0) return null;

  const highlights = pickHighConviction(rows);
  const isFallback = highlights.length === 0;
  const displayed = isFallback
    ? [...rows].sort((a, b) => b.avg_confidence - a.avg_confidence).slice(0, 4)
    : highlights.slice(0, 8);

  return (
    <div className="px-6 py-3">
      <div className="flex items-center gap-1.5 mb-2 text-[10px] uppercase tracking-wide text-muted-foreground">
        <Star className="h-3 w-3" />
        {isFallback
          ? `Top by confidence · no high-conviction signals (${rows.length} scanned)`
          : `High-Conviction Signals · ${highlights.length}`}
      </div>

      {isFallback && (
        <div className="mb-2 text-[11px] text-muted-foreground leading-relaxed max-w-2xl">
          Every signal came back below the high-conviction bar. The tiles below
          show the four highest-confidence names anyway.
        </div>
      )}

      <TooltipProvider delayDuration={200}>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
          {displayed.map((row) => (
            <HighConvictionTile
              key={row.ticker}
              row={row}
              onSelect={() => {
                selectTicker(row.ticker);
                // Defer the scroll a tick so the SleeveSection auto-open
                // useEffect has a chance to mount the expansion first.
                setTimeout(() => {
                  const el = document.getElementById(
                    `ticker-anchor-${row.ticker.toUpperCase()}`,
                  );
                  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }, 80);
              }}
            />
          ))}
        </div>
      </TooltipProvider>
    </div>
  );
}

function HighConvictionTile({
  row,
  onSelect,
}: {
  row: TickerRow;
  onSelect: () => void;
}) {
  const { data } = useTickerData(row.ticker);
  // Restrict the tile spark + % chip to the last ~22 trading days (1M).
  // Backend now serves ~2y by default — full series would compress to
  // mush at 140px width, and a 2-year % change isn't actionable here.
  const fullCloses = (data?.price_history ?? []).map((p) => p.close);
  const closes = fullCloses.slice(-22);
  const last = closes.length ? closes[closes.length - 1] : null;
  const first = closes.length ? closes[0] : null;
  const pctChange = last != null && first != null && first !== 0 ? ((last - first) / first) * 100 : null;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button onClick={onSelect} className="text-left w-full">
          <Card
            className={cn(
              'p-3 transition-colors hover:bg-accent cursor-pointer',
              row.has_variant_perception &&
                'border-amber-500/30 bg-amber-500/[0.03]',
            )}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-semibold font-mono text-sm">
                {row.ticker}
              </span>
              {row.has_variant_perception && (
                <Sparkles className="h-3.5 w-3.5 text-amber-500 flex-shrink-0" />
              )}
            </div>

            <div className="mt-1 flex items-baseline gap-2">
              <span className="text-base font-mono font-semibold tabular-nums">
                {last != null ? formatPrice(last) : '—'}
              </span>
              {pctChange != null && (
                <span
                  className={cn(
                    'text-[11px] font-mono',
                    pctChange >= 0
                      ? 'text-emerald-600 dark:text-emerald-400'
                      : 'text-rose-600 dark:text-rose-400',
                  )}
                  title="1-month change"
                >
                  {pctChange >= 0 ? '+' : ''}
                  {pctChange.toFixed(1)}%
                </span>
              )}
              <span className="text-[9px] uppercase tracking-wide text-muted-foreground/70">
                1M
              </span>
            </div>

            <div className="mt-1">
              <MiniSpark closes={closes} width={140} height={28} />
            </div>

            <div className="mt-2 flex items-center justify-between gap-2">
              <SignalPill
                signal={row.consensus}
                confidence={row.avg_confidence}
                compact
              />
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground capitalize">
                {row.sleeve.replace(/_/g, ' ')}
              </span>
            </div>
          </Card>
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-xs">
        <div className="text-xs space-y-1">
          <div>
            <strong>{row.ticker}</strong> ·{' '}
            {row.position_type.replace(/_/g, ' ')}
          </div>
          {row.variant_perception && (
            <div className="italic">"{row.variant_perception}"</div>
          )}
          <div className="text-muted-foreground">
            weighted score {row.weighted_score.toFixed(1)} · {row.hold_period}
          </div>
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

function formatPrice(n: number): string {
  if (n < 1) return `$${n.toFixed(3)}`;
  if (n < 100) return `$${n.toFixed(2)}`;
  return `$${n.toFixed(1)}`;
}
