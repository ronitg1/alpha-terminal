/**
 * TickerRow — rich, one-line row used inside each sleeve section.
 *
 * Replaces the old plain-text table row. Single horizontal layout:
 *   [ticker + variant ✨]  [price]  [Δ%]  [sparkline]  [signal pill]
 *   [conv chip]  [Run]  [chevron→drawer]
 *
 * Phase A: clicking the chevron (and the row body) selects the ticker and
 * lets the existing drill drawer open. Phase B replaces that with an
 * inline expand-in-place.
 *
 * Conviction-3 (≥80) gets a thicker left border — replaces the noisy 5%
 * tinting that washed every row in the old card.
 */
import { Button } from '@/components/ui/button';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { cn } from '@/lib/utils';
import type { TickerRow as TickerRowData } from '@/types/sleeves';
import { ChevronRight, Play, Sparkles, Square } from 'lucide-react';
import { useTickerData } from './hooks/use-ticker-data';
import { MiniSpark } from './mini-spark';
import { SignalPill } from './signal-pill';

interface TickerRowProps {
  /** The ticker symbol (always required). */
  ticker: string;
  /** May be null if the ticker hasn't been scanned in this run. */
  row: TickerRowData | null;
}

export function TickerRow({ ticker, row }: TickerRowProps) {
  const { selectTicker, selectedTicker, runScan, scanStatus, stopScan } =
    useSleevesContext();
  const { data } = useTickerData(ticker);
  const isRunning = scanStatus === 'running';
  const isSelected = selectedTicker === ticker;

  // Restrict the row spark to the last 22 trading days (1M) so 2 years
  // of backend data doesn't compress to mush at 96px width. The Δ%
  // displayed alongside is day-over-day (prev-bar close), unchanged.
  const fullCloses = (data?.price_history ?? []).map((p) => p.close);
  const closes = fullCloses.slice(-22);
  const last = fullCloses.length ? fullCloses[fullCloses.length - 1] : null;
  const prev = fullCloses.length > 1 ? fullCloses[fullCloses.length - 2] : null;
  const todayPct = last != null && prev != null && prev !== 0 ? ((last - prev) / prev) * 100 : null;

  const handleRun = (e: React.MouseEvent) => {
    e.stopPropagation();
    void runScan({ tickers: [ticker] });
  };

  const handleStop = (e: React.MouseEvent) => {
    e.stopPropagation();
    stopScan();
  };

  const handleSelect = () => selectTicker(ticker);

  const isHighConv = row && row.avg_confidence >= 80;
  const isLong = row?.consensus === 'bullish';
  const isShort = row?.consensus === 'bearish';

  return (
    <div
      onClick={handleSelect}
      className={cn(
        'group flex items-center gap-3 px-3 py-2 rounded cursor-pointer',
        'border-l-2 border-transparent transition-colors',
        'hover:bg-accent/50',
        isSelected && 'bg-accent/70 ring-1 ring-primary/30',
        isHighConv && isLong && 'border-l-emerald-500/70',
        isHighConv && isShort && 'border-l-rose-500/70',
        isHighConv && !isLong && !isShort && 'border-l-amber-500/70',
      )}
      title={row?.variant_perception || undefined}
    >
      {/* Ticker */}
      <div className="w-20 flex-shrink-0 flex items-center gap-1.5">
        <span className="font-mono font-semibold text-sm tabular-nums">
          {ticker}
        </span>
        {row?.has_variant_perception && (
          <Sparkles
            className="h-3.5 w-3.5 text-amber-500 flex-shrink-0"
            aria-label="variant perception"
          />
        )}
      </div>

      {/* Price */}
      <div className="w-20 flex-shrink-0 text-right font-mono text-sm tabular-nums">
        {last != null ? formatPrice(last) : <span className="text-muted-foreground">—</span>}
      </div>

      {/* Δ today (1D) — labeled so the column meaning is obvious. */}
      <div
        className="w-20 flex-shrink-0 text-right font-mono text-xs tabular-nums"
        title="1-day change (close vs previous close)"
      >
        {todayPct != null ? (
          <span className="inline-flex items-baseline gap-1 justify-end">
            <span
              className={
                todayPct >= 0
                  ? 'text-emerald-600 dark:text-emerald-400'
                  : 'text-rose-600 dark:text-rose-400'
              }
            >
              {todayPct >= 0 ? '+' : ''}
              {todayPct.toFixed(1)}%
            </span>
            <span className="text-[9px] uppercase text-muted-foreground/70">
              1D
            </span>
          </span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </div>

      {/* 1-month sparkline */}
      <div
        className="w-24 flex-shrink-0"
        title="Last ~1 month of daily closes"
      >
        <MiniSpark closes={closes} width={96} height={26} />
      </div>

      {/* Signal */}
      <div className="flex-1 min-w-[80px]">
        {row ? (
          <SignalPill signal={row.consensus} confidence={row.avg_confidence} compact />
        ) : (
          <span className="text-xs text-muted-foreground italic">not scanned</span>
        )}
      </div>

      {/* Weighted score chip */}
      {row && (
        <div className="w-16 flex-shrink-0 text-right text-xs font-mono text-muted-foreground tabular-nums">
          {row.weighted_score >= 0 ? '+' : ''}
          {row.weighted_score.toFixed(0)}
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-1 flex-shrink-0 opacity-60 group-hover:opacity-100 transition-opacity">
        {isRunning ? (
          <Button
            size="sm"
            variant="ghost"
            className="h-7 w-7 p-0"
            onClick={handleStop}
            title="Stop running scan"
          >
            <Square className="h-3 w-3 fill-current" />
          </Button>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="h-7 w-7 p-0"
            onClick={handleRun}
            title={`Scan ${ticker} only`}
          >
            <Play className="h-3 w-3 fill-current" />
          </Button>
        )}
        <ChevronRight
          className={cn(
            'h-3.5 w-3.5 text-muted-foreground transition-transform',
            isSelected && 'rotate-90',
          )}
        />
      </div>
    </div>
  );
}

function formatPrice(n: number): string {
  if (n < 1) return `$${n.toFixed(3)}`;
  if (n < 100) return `$${n.toFixed(2)}`;
  return `$${n.toFixed(1)}`;
}
