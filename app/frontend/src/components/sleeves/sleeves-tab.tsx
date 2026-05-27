/**
 * SleevesTab — top-level container rendered by TabService when the user
 * is on the Sleeves tab. Mounts the SleevesProvider so the context is
 * scoped to the dashboard (we don't pay the fetch cost on other tabs).
 *
 * Layout: vertical stack — header / high-conviction strip / sleeve grid.
 * The header and strip are static-height; the grid scrolls.
 */

import { SleevesProvider, useSleevesContext } from '@/contexts/sleeves-context';
import { AlertCircle } from 'lucide-react';
import { DashboardHeader } from './dashboard-header';
import { HighConvictionStrip } from './high-conviction-strip';
import { LiveActivityPanel } from './live-activity-panel';
import { SleeveGrid } from './sleeve-grid';
import { TickerDrillDrawer } from './ticker-drill-drawer';

function SleevesContent() {
  const { scanStatus, scanError, latestScan, config } = useSleevesContext();

  if (scanStatus === 'loading' && !config) {
    return (
      <div className="h-full w-full flex items-center justify-center text-muted-foreground">
        <div className="text-sm">Loading dashboard…</div>
      </div>
    );
  }

  if (scanStatus === 'error') {
    return (
      <div className="h-full w-full flex flex-col bg-background">
        <DashboardHeader />
        <div className="flex-1 flex items-center justify-center p-8">
          <div className="max-w-lg text-center space-y-3">
            <AlertCircle className="h-10 w-10 text-rose-500 mx-auto" />
            <div className="text-base font-medium">Could not load dashboard data</div>
            <div className="text-sm text-muted-foreground font-mono break-all">{scanError}</div>
            <div className="text-xs text-muted-foreground">
              Most likely the backend isn't running. Start it with{' '}
              <code className="bg-muted px-1.5 py-0.5 rounded">
                poetry run uvicorn app.backend.main:app --reload
              </code>{' '}
              and click Refresh.
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full w-full flex flex-col bg-background">
      <DashboardHeader />
      <div className="flex-1 overflow-y-auto">
        <LiveActivityPanel />
        <HighConvictionStrip />
        <SleeveGrid />
        {!latestScan && config && (
          <div className="px-6 pb-6">
            <div className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
              No scans yet. Run{' '}
              <code className="bg-muted px-1.5 py-0.5 rounded">
                poetry run python -m src.run_morning_scan
              </code>{' '}
              to produce one. The CSV will appear under{' '}
              <code className="bg-muted px-1.5 py-0.5 rounded">outputs/</code> and click
              Refresh to load it.
            </div>
          </div>
        )}
      </div>
      <TickerDrillDrawer />
    </div>
  );
}

export function SleevesTab() {
  return (
    <SleevesProvider>
      <SleevesContent />
    </SleevesProvider>
  );
}
