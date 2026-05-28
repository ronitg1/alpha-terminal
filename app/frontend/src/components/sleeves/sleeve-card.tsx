/**
 * SleeveCard — one of four cards in the dashboard grid.
 *
 * Renders a sleeve's allocation, agent panel, and per-ticker table.
 * Clicking a row selects the ticker in SleevesContext (the drill-down
 * drawer that listens to selectedTicker ships in Phase 3).
 */

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { cn } from '@/lib/utils';
import { SleeveConfig, TickerRow } from '@/types/sleeves';
import { Pencil, Sparkles } from 'lucide-react';
import { useState } from 'react';
import { AnalystChip } from './analyst-chip';
import { SignalPill } from './signal-pill';
import { WatchlistEditor } from './watchlist-editor';

interface SleeveCardProps {
  sleeve: SleeveConfig;
}

const HIGHLIGHT_ROW_CLASS: Record<string, string> = {
  green: 'bg-emerald-500/5 hover:bg-emerald-500/10',
  red: 'bg-rose-500/5 hover:bg-rose-500/10',
  yellow: 'bg-amber-500/5 hover:bg-amber-500/10',
  neutral: 'hover:bg-accent',
};

function sleeveDisplayName(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Pick the dominant agent (highest confidence) and return a one-sentence
 *  preview of its reasoning. Falls back to variant_perception or a synthetic
 *  "agent: signal" line when reasoning isn't in raw (CSV-loaded rows). Used
 *  for the native browser title= tooltip on each ticker row so hovering a
 *  neutral pill explains WHY it's neutral without opening the drawer. */
function dominantReasoning(row: TickerRow): string {
  if (!row.per_agent.length) return row.variant_perception || '';
  const dominant = [...row.per_agent].sort((a, b) => b.confidence - a.confidence)[0];
  const raw = dominant.raw ?? {};
  const r = (raw.reasoning as string | undefined) ?? '';
  if (r) {
    const firstSent = r.match(/^[^.!?\n]+[.!?]?/)?.[0] ?? r;
    return firstSent.length > 200 ? firstSent.slice(0, 200) + '…' : firstSent;
  }
  if (row.variant_perception) return row.variant_perception;
  return `${dominant.agent.replace(/_/g, ' ')}: ${dominant.signal} (${Math.round(dominant.confidence)}%)`;
}

function ScoreBar({ score }: { score: number }) {
  // Score is roughly [-100, +100]. Render as a centered bar.
  const pct = Math.max(-100, Math.min(100, score));
  const isPositive = pct >= 0;
  const width = Math.min(100, Math.abs(pct));
  return (
    <div className="relative h-1.5 w-16 bg-muted rounded overflow-hidden">
      <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
      <div
        className={cn(
          'absolute top-0 bottom-0 transition-all',
          isPositive ? 'bg-emerald-500 left-1/2' : 'bg-rose-500 right-1/2'
        )}
        style={{ width: `${width / 2}%` }}
      />
    </div>
  );
}

export function SleeveCard({ sleeve }: SleeveCardProps) {
  const { latestScan, selectTicker, selectedTicker, watchlist } = useSleevesContext();
  const [watchlistOpen, setWatchlistOpen] = useState(false);
  const isOpportunistic = sleeve.name === 'opportunistic';

  // Rows scoped to this sleeve. Filter from the flat latestScan list.
  const rowsBySleeve: TickerRow[] = (latestScan?.rows ?? []).filter(
    (r) => r.sleeve === sleeve.name
  );
  const rowsByTicker = new Map(rowsBySleeve.map((r) => [r.ticker, r]));

  // For the opportunistic sleeve, surface the watchlist as the ticker set
  // even before a scan touches them — that way the user can see what
  // they've queued up without having to click into the editor.
  let tickers: string[];
  if (isOpportunistic) {
    tickers = watchlist.map((e) => e.ticker);
  } else {
    tickers = sleeve.tickers.length > 0 ? sleeve.tickers : Array.from(rowsByTicker.keys());
  }

  return (
    <Card className="flex flex-col overflow-hidden">
      <CardHeader className="pb-3">
        <div className="flex items-baseline justify-between gap-2">
          <CardTitle className="text-base flex items-center gap-2">
            {sleeveDisplayName(sleeve.name)}
            {isOpportunistic && (
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-xs"
                onClick={() => setWatchlistOpen(true)}
              >
                <Pencil className="h-3 w-3 mr-1" />
                Edit watchlist
              </Button>
            )}
          </CardTitle>
          <span className="text-xs font-mono text-muted-foreground">
            {sleeve.allocation_pct.toFixed(0)}%
          </span>
        </div>
        <div className="flex flex-wrap gap-1 mt-1">
          {sleeve.agents.map((a) => (
            <AnalystChip key={a} agentKey={a} weight={sleeve.agent_weights[a]} />
          ))}
        </div>
      </CardHeader>

      <CardContent className="p-0 flex-1 overflow-auto">
        {tickers.length === 0 ? (
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
          <Table>
            <TableHeader className="bg-muted/40">
              <TableRow>
                <TableHead className="h-8 text-xs">Ticker</TableHead>
                <TableHead className="h-8 text-xs">Signal</TableHead>
                <TableHead className="h-8 text-xs">Score</TableHead>
                <TableHead className="h-8 text-xs text-right">Conv</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tickers.map((ticker) => {
                const row = rowsByTicker.get(ticker);
                const isSelected = selectedTicker === ticker;
                return (
                  <TableRow
                    key={ticker}
                    onClick={() => row && selectTicker(ticker)}
                    className={cn(
                      'cursor-pointer transition-colors',
                      row && HIGHLIGHT_ROW_CLASS[row.highlight],
                      isSelected && 'ring-1 ring-primary/40'
                    )}
                    title={row ? dominantReasoning(row) : undefined}
                  >
                    <TableCell className="font-mono font-medium text-sm py-1.5">
                      <span className="inline-flex items-center gap-1">
                        {ticker}
                        {row?.has_variant_perception && (
                          <Sparkles className="h-3 w-3 text-amber-500" />
                        )}
                      </span>
                    </TableCell>
                    <TableCell className="py-1.5">
                      {row ? (
                        <SignalPill signal={row.consensus} compact />
                      ) : (
                        <span className="text-xs text-muted-foreground italic">
                          not scanned
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="py-1.5">
                      {row && <ScoreBar score={row.weighted_score} />}
                    </TableCell>
                    <TableCell className="py-1.5 text-right font-mono text-xs">
                      {row ? Math.round(row.avg_confidence) : '—'}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
      {isOpportunistic && (
        <WatchlistEditor open={watchlistOpen} onOpenChange={setWatchlistOpen} />
      )}
    </Card>
  );
}
