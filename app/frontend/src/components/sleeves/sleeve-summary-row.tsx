/**
 * SleeveSummaryRow — horizontal strip of sleeve overview cards.
 *
 * One card per sleeve in config order, each carrying:
 *   • name + allocation %
 *   • bias label + weighted conviction
 *   • count of bullish / bearish / variant
 *   • per-sleeve Run button
 *
 * Click the sleeve card → scrolls down to the matching PositionsSection
 * entry (anchor-style nav). Keeps a single source of truth on the page.
 */
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { cn } from '@/lib/utils';
import type { SleeveConfig } from '@/types/sleeves';
import { Play, Sparkles, Square } from 'lucide-react';
import { useMemo } from 'react';
import {
  biasColorClass,
  biasLabel,
  readoutForSleeve,
} from './utils/derive-bias';

export function SleeveSummaryRow() {
  const { config } = useSleevesContext();
  const sleeves = config?.sleeves ?? [];

  if (sleeves.length === 0) return null;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3 px-6 py-1">
      {sleeves.map((s) => (
        <SleeveSummaryCard key={s.name} sleeve={s} />
      ))}
    </div>
  );
}

function SleeveSummaryCard({ sleeve }: { sleeve: SleeveConfig }) {
  const { latestScan, runScan, scanStatus, stopScan } = useSleevesContext();
  const isRunning = scanStatus === 'running';
  const readout = useMemo(
    () => readoutForSleeve(sleeve, latestScan),
    [sleeve, latestScan],
  );

  const handleRun = (e: React.MouseEvent) => {
    e.stopPropagation();
    void runScan({ sleeves: [sleeve.name] });
  };

  const handleStop = (e: React.MouseEvent) => {
    e.stopPropagation();
    stopScan();
  };

  const handleJump = () => {
    // Scroll-anchor: each SleeveSection has id=`sleeve-section-${name}`.
    const el = document.getElementById(`sleeve-section-${sleeve.name}`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <Card
      onClick={handleJump}
      className={cn(
        'p-3 cursor-pointer transition-colors hover:bg-accent/40',
        'group',
      )}
    >
      <div className="flex items-baseline justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold capitalize truncate">
            {sleeve.name.replace(/_/g, ' ')}
          </div>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
            {sleeve.allocation_pct.toFixed(0)}% allocation
          </div>
        </div>
        {readout.variant > 0 && (
          <span
            className="text-amber-500 inline-flex items-center gap-0.5 text-[10px] font-mono"
            title={`${readout.variant} variant-perception flag${readout.variant === 1 ? '' : 's'}`}
          >
            <Sparkles className="h-3 w-3" /> {readout.variant}
          </span>
        )}
      </div>

      <div className="mt-2 flex items-baseline gap-2">
        <span
          className={cn(
            'text-sm font-medium',
            biasColorClass(readout.bias),
          )}
        >
          {biasLabel(readout.bias)}
        </span>
        {readout.scanned > 0 && (
          <span className="text-xs text-muted-foreground font-mono">
            conv {Math.round(readout.weightedConv)}
          </span>
        )}
      </div>

      <div className="mt-1 text-[11px] text-muted-foreground font-mono leading-tight">
        {readout.scanned > 0 ? (
          <>
            <span className="text-emerald-600 dark:text-emerald-400">
              {readout.bullish}↑
            </span>
            {' · '}
            <span className="text-rose-600 dark:text-rose-400">
              {readout.bearish}↓
            </span>
            {' · '}
            <span>{readout.neutral}=</span>
            {' · '}
            <span>{readout.scanned} scanned</span>
          </>
        ) : (
          <span className="italic">no scan</span>
        )}
      </div>

      <div className="mt-2 flex gap-1.5 opacity-80 group-hover:opacity-100 transition-opacity">
        {isRunning ? (
          <Button
            size="sm"
            variant="outline"
            className="h-7 px-2 text-xs flex-1"
            onClick={handleStop}
          >
            <Square className="h-3 w-3 mr-1 fill-current" /> Stop
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            className="h-7 px-2 text-xs flex-1"
            onClick={handleRun}
            title={`Scan only the ${sleeve.tickers.length || readout.scanned} ${sleeve.name.replace(/_/g, ' ')} tickers`}
          >
            <Play className="h-3 w-3 mr-1 fill-current" /> Run sleeve
          </Button>
        )}
      </div>
    </Card>
  );
}
