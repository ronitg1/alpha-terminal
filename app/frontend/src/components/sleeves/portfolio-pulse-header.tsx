/**
 * PortfolioPulseHeader — sticky top of the Sleeves dashboard.
 *
 * Left:  Portfolio Pulse title + bias one-liner + scan-history selector
 * Right: Manage sleeves · Refresh · Run portfolio
 *
 * The "Run portfolio" button is the high-real-estate primary action because
 * portfolio-scope runs are the most common use. Sleeve-scope and ticker-scope
 * runs live on their respective cards/rows.
 *
 * When a scan is running, Run morphs into Stop and a live-events counter
 * appears next to the bias line.
 */
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { cn } from '@/lib/utils';
import { Layers, ListChecks, Play, RefreshCw, Square } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { SleeveManagerDialog } from './sleeve-manager-dialog';
import { WatchlistEditor } from './watchlist-editor';
import {
  biasColorClass,
  biasLabel,
  readoutForPortfolio,
} from './utils/derive-bias';

export function PortfolioPulseHeader() {
  const {
    config,
    latestScan,
    scanStatus,
    refresh,
    runScan,
    stopScan,
    liveActivity,
    scanHistory,
    loadScanByDate,
    watchlist,
  } = useSleevesContext();

  const [managerOpen, setManagerOpen] = useState(false);
  const [watchlistOpen, setWatchlistOpen] = useState(false);
  const isLoading = scanStatus === 'loading';
  const isRunning = scanStatus === 'running';

  const readout = useMemo(
    () => readoutForPortfolio(config?.sleeves ?? [], latestScan),
    [config, latestScan],
  );

  const handleRunPortfolio = () => {
    // Portfolio run = every sleeve. Includes the watchlist if any are queued.
    void runScan({
      includeWatchlist: watchlist.length > 0,
    });
  };

  // Keyboard shortcuts. Only fire when no input/textarea/contenteditable is
  // focused — otherwise typing in a form would trigger them.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (!target) return;
      const tag = target.tagName?.toLowerCase();
      if (
        tag === 'input' ||
        tag === 'textarea' ||
        tag === 'select' ||
        target.isContentEditable
      ) {
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'enter' && !isRunning) {
        // Cmd/Ctrl+Enter — run portfolio. Works even when an input had focus
        // and bubbled up after blur. Avoids stealing plain 'R' which a user
        // might type in a ticker field they just blurred.
        e.preventDefault();
        handleRunPortfolio();
        return;
      }
      if (e.key === 'r' && !e.shiftKey && !e.metaKey && !e.ctrlKey && !isRunning) {
        e.preventDefault();
        handleRunPortfolio();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [isRunning]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="sticky top-0 z-20 bg-background border-b border-border">
      <div className="flex items-start justify-between gap-4 px-6 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="text-base font-semibold tracking-tight">
              Portfolio Pulse
            </h1>
            {scanHistory.length > 1 ? (
              <select
                value={latestScan?.date ?? ''}
                onChange={(e) => {
                  const d = e.target.value;
                  if (d) void loadScanByDate(d);
                }}
                disabled={isRunning}
                className="font-mono text-xs bg-background border border-border rounded px-1.5 py-0.5 hover:bg-accent disabled:opacity-50"
                title="Scan history"
              >
                {scanHistory.map((s) => (
                  <option key={s.date} value={s.date}>
                    {s.date}
                  </option>
                ))}
              </select>
            ) : (
              <span className="font-mono text-xs text-muted-foreground">
                {latestScan?.date ?? 'no scan yet'}
              </span>
            )}
            {isRunning && (
              <Badge variant="secondary" className="font-mono animate-pulse">
                running · {liveActivity.length} events
              </Badge>
            )}
          </div>

          <div className="mt-1 text-[12px] text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1">
            <span
              className={cn(
                'font-medium',
                biasColorClass(readout.bias),
              )}
            >
              {biasLabel(readout.bias)} bias
            </span>
            {readout.scanned > 0 && (
              <>
                <Separator />
                <span>
                  Net <NetTag net={readout.net} />
                </span>
                <Separator />
                <span>
                  Conv <span className="font-mono">{Math.round(readout.weightedConv)}</span>
                </span>
                <Separator />
                <span>
                  <span className="font-mono">{readout.highConv}</span> high-conviction
                </span>
                {readout.variant > 0 && (
                  <>
                    <Separator />
                    <span className="text-amber-600 dark:text-amber-400">
                      ✨ {readout.variant} variant
                    </span>
                  </>
                )}
              </>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setWatchlistOpen(true)}
            disabled={isRunning}
            title="Edit watchlist (the opportunistic queue)"
          >
            <ListChecks className="h-4 w-4 mr-1.5" />
            <span className="hidden sm:inline">
              Watchlist
              {watchlist.length > 0 && (
                <span className="ml-1 font-mono text-[10px] text-muted-foreground">
                  {watchlist.length}
                </span>
              )}
            </span>
          </Button>
          <WatchlistEditor
            open={watchlistOpen}
            onOpenChange={setWatchlistOpen}
          />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setManagerOpen(true)}
            disabled={isRunning}
            title="Create, edit, or delete sleeves"
          >
            <Layers className="h-4 w-4 mr-1.5" />
            <span className="hidden sm:inline">Manage</span>
          </Button>
          <SleeveManagerDialog
            open={managerOpen}
            onOpenChange={setManagerOpen}
          />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void refresh()}
            disabled={isLoading || isRunning}
          >
            <RefreshCw
              className={cn('h-4 w-4 mr-1.5', isLoading && 'animate-spin')}
            />
            <span className="hidden sm:inline">Refresh</span>
          </Button>
          {isRunning ? (
            <Button size="sm" variant="destructive" onClick={() => stopScan()}>
              <Square className="h-4 w-4 mr-1.5 fill-current" />
              Stop
            </Button>
          ) : (
            <Button size="sm" onClick={handleRunPortfolio}>
              <Play className="h-4 w-4 mr-1.5 fill-current" />
              Run portfolio
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

function Separator() {
  return <span className="text-border" aria-hidden="true">·</span>;
}

function NetTag({ net }: { net: number }) {
  const pct = Math.round(net * 100);
  const positive = pct > 0;
  if (pct === 0) {
    return <span className="font-mono text-muted-foreground">flat</span>;
  }
  return (
    <span
      className={cn(
        'font-mono font-medium',
        positive
          ? 'text-emerald-600 dark:text-emerald-400'
          : 'text-rose-600 dark:text-rose-400',
      )}
    >
      {positive ? '+' : ''}
      {pct}%
    </span>
  );
}
