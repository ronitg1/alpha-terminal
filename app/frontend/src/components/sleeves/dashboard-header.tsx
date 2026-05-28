/**
 * DashboardHeader — top strip of the Sleeves tab.
 *
 * Refresh button re-fetches the latest scan + config.
 * Run Scan kicks off a live scan via SSE; while running it morphs into a
 * Stop button. Counter shows live tickers complete during a scan.
 */

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { Play, RefreshCw, Square } from 'lucide-react';

function formatDate(iso: string | undefined | null): string {
  if (!iso) return 'no scan yet';
  return iso;
}

export function DashboardHeader() {
  const {
    latestScan,
    scanStatus,
    refresh,
    runScan,
    stopScan,
    liveActivity,
    watchlist,
    scanHistory,
    loadScanByDate,
  } = useSleevesContext();

  const rowCount = latestScan?.row_count ?? 0;
  const isLoading = scanStatus === 'loading';
  const isRunning = scanStatus === 'running';
  const hasWatchlist = watchlist.length > 0;

  const handleRunScan = () => {
    // Default to re-scanning whatever tickers were in the last scan so the
    // run completes quickly during dev/iteration. Falls back to all sleeves
    // when no prior scan exists.
    const priorTickers = latestScan?.rows.map((r) => r.ticker);
    // Always include the watchlist when it has entries — running a scan
    // without the user's queued candidates would be confusing.
    void runScan({
      tickers: priorTickers && priorTickers.length > 0 ? priorTickers : undefined,
      includeWatchlist: hasWatchlist,
    });
  };

  return (
    <div className="flex items-center justify-between gap-4 px-6 py-4 border-b border-border bg-background">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">Sleeves Dashboard</h1>
        <Separator orientation="vertical" className="h-5" />
        <span className="text-sm text-muted-foreground">Morning scan ·</span>
        {scanHistory.length > 1 ? (
          // Multiple scans on disk → render a dropdown for switching dates.
          // Plain <select> styled with Tailwind — no Shadcn select primitive
          // is shipped in this fork, and the cmdk-based Command would be
          // overkill for a date list.
          <select
            value={latestScan?.date ?? ''}
            onChange={(e) => {
              const d = e.target.value;
              if (d) void loadScanByDate(d);
            }}
            disabled={isRunning}
            className="font-mono text-sm bg-background border border-border rounded px-2 py-1 hover:bg-accent disabled:opacity-50"
          >
            {scanHistory.map((s) => (
              <option key={s.date} value={s.date}>
                {s.date}
              </option>
            ))}
          </select>
        ) : (
          <span className="font-mono text-sm text-muted-foreground">
            {formatDate(latestScan?.date)}
          </span>
        )}
        {rowCount > 0 && !isRunning && (
          <Badge variant="secondary" className="font-mono">
            {rowCount} {rowCount === 1 ? 'row' : 'rows'}
          </Badge>
        )}
        {isRunning && (
          <Badge variant="secondary" className="font-mono animate-pulse">
            running · {liveActivity.length} events
          </Badge>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => void refresh()}
          disabled={isLoading || isRunning}
        >
          <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
        {isRunning ? (
          <Button size="sm" variant="destructive" onClick={() => stopScan()}>
            <Square className="h-4 w-4 mr-2 fill-current" />
            Stop
          </Button>
        ) : (
          <Button
            size="sm"
            onClick={handleRunScan}
            title={
              hasWatchlist
                ? `Will include ${watchlist.length} watchlist ticker${watchlist.length === 1 ? '' : 's'}.`
                : 'Run morning scan against the current ticker set.'
            }
          >
            <Play className="h-4 w-4 mr-2 fill-current" />
            Run Scan
          </Button>
        )}
      </div>
    </div>
  );
}
