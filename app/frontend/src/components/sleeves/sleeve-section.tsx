/**
 * SleeveSection — collapsible per-sleeve container.
 *
 * Header line: caret · sleeve name · allocation · bias chip · ticker count · per-sleeve Run.
 * Body: list of TickerRow with subtle dividers.
 *
 * Default open state: parent passes in (PositionsSection picks based on
 * "mega_tech expanded, others collapsed" rule). Local state persists user
 * toggles within the session — explicit user action overrides the default.
 */
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { cn } from '@/lib/utils';
import type { SleeveConfig, TickerRow as TickerRowData } from '@/types/sleeves';
import { ChevronDown, ChevronRight, Pencil, Play, Square } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { AnalystChip } from './analyst-chip';
import { SleeveThesisBar } from './sleeve-thesis-bar';
import { TickerExpansion } from './ticker-expansion';
import { TickerRow } from './ticker-row';
import {
  biasColorClass,
  biasLabel,
  readoutForSleeve,
} from './utils/derive-bias';
import { WatchlistEditor } from './watchlist-editor';

interface SleeveSectionProps {
  sleeve: SleeveConfig;
  defaultOpen: boolean;
}

export function SleeveSection({ sleeve, defaultOpen }: SleeveSectionProps) {
  const { latestScan, runScan, scanStatus, stopScan, selectedTicker, selectTicker } =
    useSleevesContext();
  const isOpportunistic = sleeve.name === 'opportunistic';
  const isRunning = scanStatus === 'running';
  const [open, setOpen] = useState(defaultOpen);
  const [watchlistOpen, setWatchlistOpen] = useState(false);

  const readout = useMemo(
    () => readoutForSleeve(sleeve, latestScan),
    [sleeve, latestScan],
  );

  // Build the ticker list: configured sleeve tickers, OR watchlist for the
  // opportunistic sleeve (which uses the watchlist as its live ticker set).
  const sleeveScanRows: TickerRowData[] = useMemo(
    () => (latestScan?.rows ?? []).filter((r) => r.sleeve === sleeve.name),
    [latestScan, sleeve.name],
  );
  const rowsByTicker = useMemo(
    () => new Map(sleeveScanRows.map((r) => [r.ticker.toUpperCase(), r])),
    [sleeveScanRows],
  );

  const tickerList: string[] = useMemo(() => {
    // sleeve.tickers is the canonical list (edited via Manage Sleeves or
    // the inline Edit Watchlist dialog, which writes to the sleeve config
    // for opportunistic). No union with watchlist — that caused "I deleted
    // it but it's still here" because watchlist re-added what the user
    // removed from the sleeve config.
    return sleeve.tickers.length > 0
      ? sleeve.tickers.map((t) => t.toUpperCase())
      : Array.from(rowsByTicker.keys());
  }, [sleeve.tickers, rowsByTicker]);

  // Auto-open this section when a ticker we contain becomes selected. Lets
  // high-conviction tiles or external selection trigger expansion without
  // forcing the user to scroll then click the caret.
  //
  // Dep is ONLY [selectedTicker] — not [..., tickerList] — because
  // tickerList is a new array reference every render (it's a useMemo'd
  // map() of sleeve.tickers). Including it as a dep made this effect fire
  // on every render and re-open the section the user just manually closed.
  // That was the "Mega Tech panel won't close" bug.
  useEffect(() => {
    if (!selectedTicker) return;
    const upper = selectedTicker.toUpperCase();
    if (sleeve.tickers.some((t) => t.toUpperCase() === upper)) {
      setOpen(true);
    }
  }, [selectedTicker, sleeve.tickers]);

  const handleRun = (e: React.MouseEvent) => {
    e.stopPropagation();
    void runScan({ sleeves: [sleeve.name] });
  };
  const handleStop = (e: React.MouseEvent) => {
    e.stopPropagation();
    stopScan();
  };

  return (
    <Card id={`sleeve-section-${sleeve.name}`} className="overflow-hidden">
      {/* Header row. Uses a div (not a button) so we can nest Run/Stop/Edit
          <Button>s inside without HTML validation errors. The whole row is
          still keyboard-accessible via role=button + onKeyDown. */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setOpen((v) => !v);
          }
        }}
        className={cn(
          'w-full flex items-center gap-3 px-4 py-2.5 text-left cursor-pointer',
          'hover:bg-accent/40 transition-colors focus:outline-none focus:ring-1 focus:ring-primary/30',
          open && 'border-b border-border',
        )}
      >
        {open ? (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        )}
        <span className="text-sm font-semibold capitalize">
          {sleeve.name.replace(/_/g, ' ')}
        </span>
        <span className="text-xs text-muted-foreground font-mono">
          {sleeve.allocation_pct.toFixed(0)}%
        </span>
        <span
          className={cn(
            'text-xs font-medium',
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
        <span className="text-xs text-muted-foreground">
          · {tickerList.length} ticker{tickerList.length === 1 ? '' : 's'}
        </span>

        <span className="ml-auto inline-flex items-center gap-1.5">
          {isOpportunistic && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={(e) => {
                e.stopPropagation();
                setWatchlistOpen(true);
              }}
            >
              <Pencil className="h-3 w-3 mr-1" />
              Edit
            </Button>
          )}
          {isRunning ? (
            <Button
              size="sm"
              variant="outline"
              className="h-7 px-2 text-xs"
              onClick={handleStop}
            >
              <Square className="h-3 w-3 mr-1 fill-current" />
              Stop
            </Button>
          ) : (
            <Button
              size="sm"
              variant="outline"
              className="h-7 px-2 text-xs"
              onClick={handleRun}
            >
              <Play className="h-3 w-3 mr-1 fill-current" />
              Run sleeve
            </Button>
          )}
        </span>
      </div>

      {open && (
        <>
          <SleeveThesisBar sleeveName={sleeve.name} />
          <div className="px-4 py-2 border-b border-border/40 flex flex-wrap gap-1 bg-muted/20">
            {sleeve.agents.map((a) => (
              <AnalystChip
                key={a}
                agentKey={a}
                weight={sleeve.agent_weights[a]}
              />
            ))}
          </div>

          {tickerList.length === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-muted-foreground">
              {isOpportunistic ? (
                <>
                  No tickers in your watchlist.{' '}
                  <Button
                    variant="link"
                    className="text-sm p-0 h-auto"
                    onClick={() => setWatchlistOpen(true)}
                  >
                    Add some
                  </Button>
                  .
                </>
              ) : (
                <>No tickers in this sleeve.</>
              )}
            </div>
          ) : (
            <div className="divide-y divide-border/40">
              {tickerList.map((t) => {
                const rowData = rowsByTicker.get(t.toUpperCase()) ?? null;
                const isSelected = selectedTicker?.toUpperCase() === t.toUpperCase();
                return (
                  <div key={t} id={`ticker-anchor-${t.toUpperCase()}`}>
                    <TickerRow ticker={t} row={rowData} />
                    {/* Render expansion for ANY selected ticker — scanned
                        or not. Unscanned tickers still get the price chart
                        + company overview + key financials so clicking
                        them does something useful. */}
                    {isSelected && (
                      <TickerExpansion
                        ticker={t}
                        row={rowData}
                        onClose={() => selectTicker(null)}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}

      {isOpportunistic && (
        <WatchlistEditor
          open={watchlistOpen}
          onOpenChange={setWatchlistOpen}
        />
      )}
    </Card>
  );
}
