/**
 * OptionsScreenerCard — one per candidate from the screener.
 *
 * Generic on signals: renders one chip per ``candidate.signals[]`` entry
 * with the label, value, fired state, and tooltip the backend provided.
 * Adding a new strategy on the server-side requires no template changes
 * here.
 *
 * Click the row header to toggle the chain viewer; click the conviction
 * badge or any chip to read its tooltip.
 */

import { Badge } from '@/components/ui/badge';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { ScreenerCandidate, ScreenerSignal } from '@/types/sleeves';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useState } from 'react';
import { OptionChainViewer } from './option-chain-viewer';

interface OptionsScreenerCardProps {
  candidate: ScreenerCandidate;
  /** Defaults to false. Pass true to render the first card open. */
  defaultOpen?: boolean;
}

export function OptionsScreenerCard({ candidate, defaultOpen }: OptionsScreenerCardProps) {
  const [open, setOpen] = useState(!!defaultOpen);
  const totalSignals = candidate.signals.length || 3;
  const convClass = convictionColor(candidate.conviction, totalSignals);

  return (
    <TooltipProvider delayDuration={200}>
      <div className="rounded-md border border-border bg-card">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="w-full flex items-center gap-3 px-3 py-2 hover:bg-muted/30 text-left group"
        >
          {open ? (
            <ChevronDown className="h-4 w-4 text-muted-foreground group-hover:text-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 text-muted-foreground group-hover:text-foreground" />
          )}

          {/* Ticker */}
          <span className="font-mono text-sm font-semibold w-16">{candidate.ticker}</span>

          {/* Conviction badge */}
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge
                variant="outline"
                className={cn('text-[10px] font-mono cursor-help', convClass)}
              >
                {candidate.conviction}/{totalSignals}
              </Badge>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-xs">
              <div className="text-xs leading-relaxed">
                <div className="font-semibold mb-1">
                  Conviction = {candidate.conviction}/{totalSignals}
                </div>
                Number of strategy rules currently firing for this ticker.
                Higher means more agreement across signals. Hover any chip
                below for the underlying rule and current value.
              </div>
            </TooltipContent>
          </Tooltip>

          {/* Dynamic signal chips */}
          {candidate.signals.map((s, i) => (
            <SignalChipTip key={`${s.label}-${i}`} signal={s} />
          ))}

          <div className="flex-1" />

          {/* Spot price */}
          {candidate.last_price !== null && (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="text-xs font-mono tabular-nums text-muted-foreground cursor-help">
                  ${candidate.last_price.toFixed(2)}
                </span>
              </TooltipTrigger>
              <TooltipContent side="top">
                <div className="text-xs">Last close (spot price)</div>
              </TooltipContent>
            </Tooltip>
          )}
        </button>

        {!open && (
          <div className="px-3 pb-2 text-[10px] text-muted-foreground border-t border-border/40 pt-1.5">
            ▸ Click to load option chain
          </div>
        )}

        {open && (
          <div className="px-3 pb-3 border-t border-border/40">
            <OptionChainViewer ticker={candidate.ticker} recommendation={candidate.recommendation} />
          </div>
        )}
      </div>
    </TooltipProvider>
  );
}

function SignalChipTip({ signal }: { signal: ScreenerSignal }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant="outline"
          className={cn(
            'text-[10px] font-mono px-1.5 py-0 cursor-help',
            signal.fired
              ? 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400'
              : 'opacity-50'
          )}
        >
          {signal.label} {signal.value_text}
        </Badge>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs">
        <div className="space-y-1">
          <div className="font-semibold text-xs">{signal.label}</div>
          <div className="text-xs leading-relaxed">
            {signal.tooltip}{' '}
            {signal.fired ? (
              <span className="text-amber-500">Fired.</span>
            ) : (
              <span className="text-muted-foreground">Not fired.</span>
            )}
          </div>
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

function convictionColor(conviction: number, total: number): string {
  // Pin the colour on the ratio so 3/3 = strong even if a strategy ever
  // exposes more or fewer signals.
  if (total === 0) return 'opacity-60';
  const ratio = conviction / total;
  if (ratio >= 1)
    return 'border-emerald-500/60 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400';
  if (ratio >= 0.66)
    return 'border-amber-500/60 bg-amber-500/10 text-amber-700 dark:text-amber-400';
  if (ratio > 0)
    return 'border-yellow-500/40 bg-yellow-500/5 text-yellow-700 dark:text-yellow-400';
  return 'opacity-60';
}
