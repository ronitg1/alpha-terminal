/**
 * DashboardHeader — top strip of the Sleeves tab.
 *
 * Phase 1: scan timestamp + a disabled "Run Scan" button (live triggering
 * lands in Phase 2). Refresh button re-fetches the latest scan + config.
 */

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { Play, RefreshCw } from 'lucide-react';

function formatDate(iso: string | undefined | null): string {
  if (!iso) return 'no scan yet';
  return iso; // YYYY-MM-DD is already legible; localize in Phase 4.
}

export function DashboardHeader() {
  const { latestScan, scanStatus, refresh } = useSleevesContext();

  const rowCount = latestScan?.row_count ?? 0;
  const isLoading = scanStatus === 'loading';

  return (
    <div className="flex items-center justify-between gap-4 px-6 py-4 border-b border-border bg-background">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">Sleeves Dashboard</h1>
        <Separator orientation="vertical" className="h-5" />
        <span className="text-sm text-muted-foreground">
          Morning scan · <span className="font-mono">{formatDate(latestScan?.date)}</span>
        </span>
        {rowCount > 0 && (
          <Badge variant="secondary" className="font-mono">
            {rowCount} {rowCount === 1 ? 'row' : 'rows'}
          </Badge>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => void refresh()}
          disabled={isLoading}
        >
          <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
        {/* Phase 2 wires live scan triggering. Keeping the button visible but
            disabled now sets user expectations without surprising them. */}
        <Button
          size="sm"
          disabled
          title="Coming in Phase 2 — for now run `poetry run python -m src.run_morning_scan` from the CLI"
        >
          <Play className="h-4 w-4 mr-2" />
          Run Scan
        </Button>
      </div>
    </div>
  );
}
