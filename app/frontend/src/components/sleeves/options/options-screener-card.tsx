/**
 * OptionsScreenerCard — one per candidate from the screener.
 *
 * Shows conviction as a 0–100 % score with a mini progress bar, signal chips,
 * expiry tier pills (linked to the chain viewer's expiry picker), and an
 * on-demand LLM "Reason" button that fires a single DeepSeek V3 call.
 */

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { API_BASE_URL } from '@/lib/api-base';
import { cn } from '@/lib/utils';
import { ExpiryTier, ScreenerCandidate, ScreenerSignal } from '@/types/sleeves';
import { ChevronDown, ChevronRight, Sparkles } from 'lucide-react';
import { useState } from 'react';
import { OptionChainViewer } from './option-chain-viewer';

interface OptionsScreenerCardProps {
  candidate: ScreenerCandidate;
  defaultOpen?: boolean;
}

export function OptionsScreenerCard({ candidate, defaultOpen }: OptionsScreenerCardProps) {
  const [open, setOpen] = useState(!!defaultOpen);
  const [selectedTierIdx, setSelectedTierIdx] = useState<number | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [reasonLoading, setReasonLoading] = useState(false);

  const selectedTier =
    selectedTierIdx != null ? candidate.expiry_tiers?.[selectedTierIdx] ?? null : null;

  const c = candidate.conviction_pct ?? (candidate.conviction / 3) * 100;

  const handleReason = async () => {
    if (reason) { setReason(null); return; }
    setReasonLoading(true);
    try {
      const resp = await fetch(`${API_BASE_URL}/sleeves/options/reason`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker: candidate.ticker,
          conviction_pct: c,
          signals: candidate.signals,
          recommendation: candidate.recommendation,
        }),
      });
      if (!resp.ok) throw new Error(`${resp.status}`);
      const data = await resp.json();
      setReason(data.thesis ?? 'No thesis generated.');
    } catch {
      setReason('Could not generate thesis — check backend connection.');
    } finally {
      setReasonLoading(false);
    }
  };

  return (
    <TooltipProvider delayDuration={200}>
      <div className="rounded-md border border-border bg-card">
        {/* ── Row header ── */}
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="w-full flex items-center gap-3 px-3 py-2.5 hover:bg-muted/30 text-left group"
        >
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
          )}

          {/* Ticker */}
          <span className="font-mono text-sm font-semibold w-14 flex-shrink-0">
            {candidate.ticker}
          </span>

          {/* Conviction % badge + bar */}
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="flex flex-col items-center gap-0.5 cursor-help w-12 flex-shrink-0">
                <span className={cn('text-[11px] font-mono font-bold tabular-nums leading-none', convPctColor(c))}>
                  {c.toFixed(0)}%
                </span>
                <div className="w-10 h-1 rounded-full bg-muted overflow-hidden">
                  <div
                    className={cn('h-full rounded-full transition-all', convPctBg(c))}
                    style={{ width: `${Math.min(100, c)}%` }}
                  />
                </div>
              </div>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-xs">
              <div className="text-xs leading-relaxed space-y-1">
                <div className="font-semibold">Conviction {c.toFixed(1)}%</div>
                <div>
                  Weighted score across {candidate.signals.length} signals with magnitude
                  adjustment. &gt;80% = high conviction, 60–80% = strong, 40–60% = moderate.
                  Hover any chip for the individual rule threshold.
                </div>
              </div>
            </TooltipContent>
          </Tooltip>

          {/* Signal chips */}
          <div className="flex items-center gap-1.5 flex-wrap min-w-0">
            {candidate.signals.map((s, i) => (
              <SignalChipTip key={`${s.label}-${i}`} signal={s} />
            ))}
          </div>

          <div className="flex-1" />

          {/* Spot price */}
          {candidate.last_price !== null && (
            <span className="text-xs font-mono tabular-nums text-muted-foreground flex-shrink-0">
              ${candidate.last_price.toFixed(2)}
            </span>
          )}
        </button>

        {/* ── Expiry tier pills + Reason button (always shown) ── */}
        <div className="px-3 pb-2 flex items-center gap-1.5 flex-wrap border-t border-border/30 pt-1.5">
          {(candidate.expiry_tiers?.length ?? 0) > 0 && (
            <>
              <span className="text-[10px] text-muted-foreground mr-0.5">Plays:</span>
              {candidate.expiry_tiers.map((tier, i) => (
                <TierPill
                  key={i}
                  tier={tier}
                  active={selectedTierIdx === i}
                  onClick={() => {
                    setSelectedTierIdx(selectedTierIdx === i ? null : i);
                    setOpen(true);
                  }}
                />
              ))}
            </>
          )}
          <div className="flex-1" />
          <Button
            variant="ghost"
            size="sm"
            className={cn(
              'h-5 px-2 text-[10px] gap-1',
              reason
                ? 'text-sky-300 hover:text-sky-200'
                : 'text-muted-foreground hover:text-sky-300',
            )}
            onClick={(e) => { e.stopPropagation(); void handleReason(); }}
            disabled={reasonLoading}
            title="Generate a 2-sentence LLM thesis for this setup (DeepSeek V3)"
          >
            <Sparkles className="h-2.5 w-2.5" />
            {reasonLoading ? 'thinking…' : reason ? 'Hide' : 'Reason'}
          </Button>
        </div>

        {/* ── LLM thesis ── */}
        {reason && (
          <div className="mx-3 mb-2 px-2.5 py-2 rounded bg-sky-500/5 border border-sky-500/20 text-[11px] text-foreground/90 leading-relaxed">
            {reason}
          </div>
        )}

        {/* ── Prompt hint when collapsed ── */}
        {!open && !(candidate.expiry_tiers?.length) && (
          <div className="px-3 pb-2 text-[10px] text-muted-foreground border-t border-border/30 pt-1">
            ▸ Click to load option chain
          </div>
        )}

        {/* ── Chain viewer ── */}
        {open && (
          <div className="px-3 pb-3 border-t border-border/40">
            <OptionChainViewer
              ticker={candidate.ticker}
              recommendation={candidate.recommendation}
              preferredDte={selectedTier?.dte ?? undefined}
              legs={selectedTier?.legs}
              structureLabel={selectedTier?.structure}
            />
          </div>
        )}
      </div>
    </TooltipProvider>
  );
}

// ─── Expiry tier pill ────────────────────────────────────────────────────────

function TierPill({
  tier,
  active,
  onClick,
}: {
  tier: ExpiryTier;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onClick(); }}
          className={cn(
            'inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-mono transition-colors',
            active
              ? 'border-foreground/60 bg-foreground/8 text-foreground'
              : 'border-border text-muted-foreground hover:border-foreground/40 hover:text-foreground',
          )}
        >
          <span className="font-semibold">{tier.dte}d</span>
          <span className="opacity-60">·</span>
          <span>{tier.structure}</span>
        </button>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs">
        <div className="text-xs leading-relaxed">
          <div className="font-semibold mb-0.5">{tier.label}</div>
          {tier.rationale}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

// ─── Signal chip ─────────────────────────────────────────────────────────────

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
              : 'opacity-50',
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

// ─── Colour helpers ──────────────────────────────────────────────────────────

function convPctColor(pct: number): string {
  if (pct >= 80) return 'text-emerald-500 dark:text-emerald-400';
  if (pct >= 60) return 'text-amber-500 dark:text-amber-400';
  if (pct >= 40) return 'text-yellow-500 dark:text-yellow-400';
  return 'text-muted-foreground';
}

function convPctBg(pct: number): string {
  if (pct >= 80) return 'bg-emerald-500';
  if (pct >= 60) return 'bg-amber-500';
  if (pct >= 40) return 'bg-yellow-500';
  return 'bg-muted-foreground/40';
}
