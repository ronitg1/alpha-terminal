/**
 * StockCard — one ticker on the My Stocks dashboard.
 *
 * Card content:
 *   - Header: ticker + remove button + reorder buttons
 *   - Big number: latest close
 *   - Price-change badge for the chosen timeframe
 *   - Sparkline (height ~120)
 *   - Per-card timeframe pill row (1W → 2Y)
 *   - Footer KPIs: 1D Δ, market cap (if available), Sleeve signal (if scanned)
 *
 * Each card holds its own selected timeframe — different tickers can show
 * different windows side by side, persisted across reloads under
 * "my-stocks-tf-{ticker}".
 */
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { cn } from '@/lib/utils';
import { CompanyOverviewCard } from '@/components/sleeves/company-overview-card';
import { useTickerData } from '@/components/sleeves/hooks/use-ticker-data';
import { MiniSpark } from '@/components/sleeves/mini-spark';
import { PriceSparkline } from '@/components/sleeves/price-sparkline';
import { SignalPill } from '@/components/sleeves/signal-pill';
import {
  pctChange,
  slicePrices,
  TIMEFRAMES,
  Timeframe,
} from '@/components/sleeves/utils/slice-prices';
import { firstSentences } from '@/components/sleeves/utils/ticker-overview';
import { ChevronDown, ChevronUp, Info, Play, Trash2 } from 'lucide-react';
import { useState } from 'react';

interface StockCardProps {
  ticker: string;
  onRemove: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  canMoveUp: boolean;
  canMoveDown: boolean;
}

const TF_STORAGE_PREFIX = 'my-stocks-tf-';

function loadTimeframe(ticker: string): Timeframe {
  try {
    const stored = window.localStorage.getItem(
      `${TF_STORAGE_PREFIX}${ticker}`,
    ) as Timeframe | null;
    if (stored && TIMEFRAMES.some((t) => t.label === stored)) return stored;
  } catch {
    // ignore
  }
  return '3M';
}

function saveTimeframe(ticker: string, tf: Timeframe): void {
  try {
    window.localStorage.setItem(`${TF_STORAGE_PREFIX}${ticker}`, tf);
  } catch {
    // ignore
  }
}

export function StockCard({
  ticker,
  onRemove,
  onMoveUp,
  onMoveDown,
  canMoveUp,
  canMoveDown,
}: StockCardProps) {
  const { data, loading } = useTickerData(ticker);
  const { runScan, scanStatus, latestScan, selectTicker } = useSleevesContext();
  const [timeframe, setTimeframeState] = useState<Timeframe>(() =>
    loadTimeframe(ticker),
  );
  const [showOverview, setShowOverview] = useState(false);

  const handlePickTimeframe = (tf: Timeframe) => {
    setTimeframeState(tf);
    saveTimeframe(ticker, tf);
  };

  const fullPrices = data?.price_history ?? [];
  const slicedPrices = slicePrices(fullPrices, timeframe);
  const tfPct = pctChange(slicedPrices);
  const last = fullPrices.length
    ? fullPrices[fullPrices.length - 1].close
    : null;
  const prev = fullPrices.length > 1
    ? fullPrices[fullPrices.length - 2].close
    : null;
  const dayPct =
    last != null && prev != null && prev !== 0
      ? ((last - prev) / prev) * 100
      : null;
  const marketCap = (data?.fundamentals?.market_cap as number | null) ?? null;
  const isRunning = scanStatus === 'running';

  // Find the matching scan row (if any). Lets us echo the agent signal on
  // the card without forcing a re-scan.
  const scanRow = latestScan?.rows.find(
    (r) => r.ticker.toUpperCase() === ticker.toUpperCase(),
  );

  return (
    <Card className="p-4 space-y-3">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-mono font-semibold text-base">{ticker}</div>
          {data?.details?.name && (
            <div className="text-xs text-muted-foreground truncate">
              {data.details.name}
            </div>
          )}
          {data?.details?.sic_description && (
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground/80 truncate">
              {data.details.sic_description}
            </div>
          )}
          {!data?.details?.name && marketCap != null && (
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
              Market cap {formatMarketCap(marketCap)}
            </div>
          )}
        </div>
        <div className="flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0"
            onClick={onMoveUp}
            disabled={!canMoveUp}
            title="Move up"
          >
            <ChevronUp className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0"
            onClick={onMoveDown}
            disabled={!canMoveDown}
            title="Move down"
          >
            <ChevronDown className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0 text-rose-500 hover:text-rose-600"
            onClick={onRemove}
            title="Remove from My Stocks"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Price + tf change */}
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-mono font-semibold tabular-nums">
            {last != null ? formatPrice(last) : '—'}
          </span>
          {dayPct != null && (
            <span
              className={cn(
                'text-xs font-mono inline-flex items-baseline gap-1',
                dayPct >= 0
                  ? 'text-emerald-600 dark:text-emerald-400'
                  : 'text-rose-600 dark:text-rose-400',
              )}
              title="1-day change"
            >
              {dayPct >= 0 ? '+' : ''}
              {dayPct.toFixed(2)}%
              <span className="text-[9px] uppercase text-muted-foreground/70">
                1D
              </span>
            </span>
          )}
        </div>
        {tfPct != null && (
          <span
            className={cn(
              'text-xs font-mono inline-flex items-baseline gap-1',
              tfPct >= 0
                ? 'text-emerald-600 dark:text-emerald-400'
                : 'text-rose-600 dark:text-rose-400',
            )}
            title={`${timeframe} change`}
          >
            {tfPct >= 0 ? '+' : ''}
            {tfPct.toFixed(2)}%
            <span className="text-[9px] uppercase text-muted-foreground/70">
              {timeframe}
            </span>
          </span>
        )}
      </div>

      {/* Chart */}
      {loading && fullPrices.length === 0 ? (
        <div className="h-28 w-full rounded bg-muted-foreground/5 animate-pulse" />
      ) : slicedPrices.length >= 2 ? (
        <PriceSparkline prices={slicedPrices} height={120} />
      ) : (
        <div className="h-28 w-full flex items-center justify-center text-xs text-muted-foreground italic">
          No price data for this window.
        </div>
      )}

      {/* Timeframe pill row */}
      <div className="inline-flex rounded-md border border-border overflow-hidden text-[11px] font-mono">
        {TIMEFRAMES.map((tf, i) => (
          <button
            key={tf.label}
            type="button"
            onClick={() => handlePickTimeframe(tf.label)}
            className={cn(
              'px-2 py-0.5 transition-colors',
              i > 0 && 'border-l border-border',
              timeframe === tf.label
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:bg-accent/40 hover:text-foreground',
            )}
          >
            {tf.label}
          </button>
        ))}
      </div>

      {/* Sleeve signal echo + overview toggle + Run button */}
      <div className="flex items-center justify-between gap-2 pt-1 border-t border-border/40">
        <div className="flex items-center gap-2 text-[11px]">
          {scanRow ? (
            <>
              <SignalPill
                signal={scanRow.consensus}
                confidence={scanRow.avg_confidence}
                compact
              />
              <button
                className="text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
                onClick={() => selectTicker(ticker)}
                title="Jump to this ticker's full thesis in the Sleeves tab"
              >
                open in Sleeves
              </button>
            </>
          ) : (
            <span className="text-muted-foreground italic">
              not in latest scan
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            className="h-7 px-2 text-xs"
            onClick={() => setShowOverview((v) => !v)}
            title={
              showOverview
                ? 'Hide company overview'
                : 'Show 2-sentence overview + key financials'
            }
          >
            <Info className="h-3 w-3 mr-1" />
            {showOverview ? 'Hide' : 'Overview'}
            {showOverview ? (
              <ChevronUp className="h-3 w-3 ml-0.5" />
            ) : (
              <ChevronDown className="h-3 w-3 ml-0.5" />
            )}
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-7 px-2 text-xs"
            onClick={() => void runScan({ tickers: [ticker] })}
            disabled={isRunning}
            title="Scan only this ticker"
          >
            <Play className="h-3 w-3 mr-1 fill-current" />
            Scan
          </Button>
        </div>
      </div>

      {/* Inline 2-sentence overview hint + expand-on-click. The condensed
          line is always visible (when we have a description) so the card
          gives an at-a-glance "what does this thing do" without forcing the
          expand. */}
      {!showOverview && data?.details?.description && (
        <button
          onClick={() => setShowOverview(true)}
          className="text-[11px] text-left text-muted-foreground italic line-clamp-2 hover:text-foreground transition-colors"
          title="Click for full overview + key financials"
        >
          {firstSentences(data.details.description, 2)}
        </button>
      )}

      {showOverview && (
        <CompanyOverviewCard
          data={data}
          loading={loading}
          ticker={ticker}
        />
      )}
    </Card>
  );
}

function formatPrice(n: number): string {
  if (n < 1) return `$${n.toFixed(3)}`;
  if (n < 100) return `$${n.toFixed(2)}`;
  return `$${n.toFixed(1)}`;
}

function formatMarketCap(n: number): string {
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  return `$${n.toFixed(0)}`;
}

// MiniSpark imported but unused in this card; keep the import so a later
// "compact mode" can swap in without touching imports.
export { MiniSpark };
