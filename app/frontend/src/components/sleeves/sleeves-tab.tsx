/**
 * SleevesTab — Phase-A redesigned dashboard.
 *
 * Vertical stack (top → bottom):
 *   1. PortfolioPulseHeader   — sticky; bias + Run portfolio
 *   2. KpiTiles               — 4 headline numbers
 *   3. PortfolioThesisCard    — condensed-with-expand synthesis
 *   4. SleeveSummaryRow       — 4 per-sleeve overview cards, each with Run
 *   5. LiveActivityPanel      — pre-existing live agent feed during scans
 *   6. HighConvictionTiles    — richer than the old strip, with price/spark
 *   7. PositionsSection       — collapsible per-sleeve rich rows
 *   8. TickerDrillDrawer      — still mounted; Phase B will swap for inline expand
 */
import { useSleevesContext } from '@/contexts/sleeves-context';
import { AlertCircle } from 'lucide-react';
import { HighConvictionTiles } from './high-conviction-tiles';
import { KpiTiles } from './kpi-tiles';
import { LiveActivityPanel } from './live-activity-panel';
import { PortfolioPulseHeader } from './portfolio-pulse-header';
import { PortfolioThesisCard } from './portfolio-thesis-card';
import { PositionsSection } from './positions-section';
import { SleeveSummaryRow } from './sleeve-summary-row';

export function SleevesContent() {
  const { scanStatus, scanError, config } = useSleevesContext();

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
        <PortfolioPulseHeader />
        <div className="flex-1 flex items-center justify-center p-8">
          <div className="max-w-lg text-center space-y-3">
            <AlertCircle className="h-10 w-10 text-rose-500 mx-auto" />
            <div className="text-base font-medium">
              Could not load dashboard data
            </div>
            <div className="text-sm text-muted-foreground font-mono break-all">
              {scanError}
            </div>
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
    <div className="h-full w-full bg-background overflow-y-auto">
      <PortfolioPulseHeader />
      <KpiTiles />
      <PortfolioThesisCard />
      <SleeveSummaryRow />
      <LiveActivityPanel />
      <HighConvictionTiles />
      <PositionsSection />
      {/* The legacy TickerDrillDrawer has been replaced by inline
          TickerExpansion rendered below each selected row. */}
    </div>
  );
}

export function SleevesTab() {
  return <SleevesContent />;
}
