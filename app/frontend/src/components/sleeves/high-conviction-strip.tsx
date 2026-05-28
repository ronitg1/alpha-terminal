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

  // When no real high-conviction signals exist, fall back to the top 3 by
  // avg_confidence so the user still gets a "what should I look at first?"
  // surface. Mark them as "soft" to make clear they're not the real thing.
  const isFallback = highlights.length === 0;
  const displayed = isFallback
    ? [...rows].sort((a, b) => b.avg_confidence - a.avg_confidence).slice(0, 3)
    : highlights;

  return (
    <div className="px-6 py-4 border-b border-border">
      <div className="flex items-center gap-3 mb-2">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          {isFallback
            ? `Top by Confidence (no high-conviction signals · ${rows.length} tickers scanned)`
            : `High-Conviction Signals · ${highlights.length}`}
        </span>
      </div>
      {isFallback && (
        <div className="mb-3 text-[11px] text-muted-foreground leading-relaxed max-w-2xl">
          Every signal came back neutral — agents found no edge above their thresholds. The cards
          below show the three tickers with the highest agent confidence anyway, in case you want
          to drill into the reasoning. Disciplined abstention is a feature; consider widening the
          watchlist or re-running after a catalyst.
        </div>
      )}
      <TooltipProvider delayDuration={150}>
        <div className="flex gap-3 overflow-x-auto pb-1">
          {displayed.map((row) => (
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
