/**
 * HighConvictionStrip — horizontal row of cards for the highest-conviction
 * tickers across all sleeves. Surfaces:
 *
 *   • highlight === 'green'  → long candidates
 *   • highlight === 'red'    → short candidates
 *   • has_variant_perception → alpha_seeker spotted an edge
 *
 * Empty state matters: on a quiet day every signal is neutral and we still
 * want a useful message rather than a blank strip.
 */

import { Card } from '@/components/ui/card';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { TickerRow } from '@/types/sleeves';
import { Sparkles } from 'lucide-react';
import { SignalPill } from './signal-pill';

function pickHighConviction(rows: TickerRow[]): TickerRow[] {
  // Anything that isn't 'neutral' highlight, OR has a variant perception
  // flagged by alpha_seeker. Sort by absolute weighted score so strongest
  // calls bubble to the top regardless of direction.
  const filtered = rows.filter(
    (r) => r.highlight !== 'neutral' || r.has_variant_perception
  );
  return filtered
    .slice()
    .sort((a, b) => Math.abs(b.weighted_score) - Math.abs(a.weighted_score));
}

export function HighConvictionStrip() {
  const { latestScan, selectTicker } = useSleevesContext();
  const rows = latestScan?.rows ?? [];
  const highlights = pickHighConviction(rows);

  if (rows.length === 0) {
    return null; // dashboard-level empty state handles "no scan yet"
  }

  if (highlights.length === 0) {
    return (
      <div className="px-6 py-4 border-b border-border">
        <div className="text-xs uppercase tracking-wide text-muted-foreground mb-2">
          High-Conviction Signals
        </div>
        <Card className="p-4 bg-muted/30 border-dashed">
          <div className="text-sm text-muted-foreground">
            No high-conviction signals — last scan returned 0 above threshold across {rows.length} tickers.
            Disciplined abstention is a feature, not a bug; consider widening the watchlist or re-running after a catalyst.
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="px-6 py-4 border-b border-border">
      <div className="text-xs uppercase tracking-wide text-muted-foreground mb-2">
        High-Conviction Signals · {highlights.length}
      </div>
      <TooltipProvider delayDuration={150}>
        <div className="flex gap-3 overflow-x-auto pb-1">
          {highlights.map((row) => (
            <Tooltip key={row.ticker}>
              <TooltipTrigger asChild>
                <button
                  onClick={() => selectTicker(row.ticker)}
                  className="text-left"
                >
                  <Card className="min-w-[140px] p-3 hover:bg-accent transition-colors cursor-pointer">
                    <div className="flex items-center justify-between mb-2">
                      <span className="font-semibold font-mono">{row.ticker}</span>
                      {row.has_variant_perception && (
                        <Sparkles className="h-3.5 w-3.5 text-amber-500" />
                      )}
                    </div>
                    <SignalPill signal={row.consensus} confidence={row.avg_confidence} />
                    <div className="mt-2 text-[10px] uppercase tracking-wide text-muted-foreground">
                      {row.sleeve.replace(/_/g, ' ')}
                    </div>
                  </Card>
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-xs">
                <div className="text-xs space-y-1">
                  <div>
                    <strong>{row.ticker}</strong> · {row.position_type.replace(/_/g, ' ')}
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
          ))}
        </div>
      </TooltipProvider>
    </div>
  );
}
