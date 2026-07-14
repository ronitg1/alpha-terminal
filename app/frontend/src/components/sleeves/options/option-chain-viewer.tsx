/**
 * OptionChainViewer — calls on the left, puts on the right, ATM row badged.
 *
 * Every label and column header is tooltipped so a user who isn't fluent in
 * options vocabulary can hover anything for a one-line definition. A help
 * banner above the tables also reminds them that clicking any row copies a
 * trade ticker to the clipboard.
 */

import { Badge } from '@/components/ui/badge';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { sleevesApi } from '@/services/sleeves-api';
import { OptionContract, OptionLeg, OptionsChainResponse, ScreenerRecommendation } from '@/types/sleeves';
import { Copy, Star } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { OptionLegRow } from './option-leg-row';

/** A leg resolved to a concrete strike in the loaded chain. */
interface ResolvedLeg {
  direction: 'call' | 'put';
  side: 'long' | 'short';
  strike: number;
}

interface OptionChainViewerProps {
  ticker: string;
  /** Per-strategy contract recommendation. When present, the chain viewer
   *  highlights the matching contract and renders a callout above the
   *  tables explaining why. */
  recommendation?: ScreenerRecommendation;
  /** When set (from an expiry tier pill click), the viewer pre-selects the
   *  expiry whose DTE is closest to this value. */
  preferredDte?: number;
  /** Legs of the selected expiry tier's structure. When provided (and a tier
   *  is a multi-leg spread), every leg's strike is highlighted with a BUY/SELL
   *  tag. Falls back to the single ``recommendation`` strike when absent. */
  legs?: OptionLeg[];
  /** Display name of the selected structure, e.g. "call debit spread". Shown
   *  in the recommendation callout when legs are present. */
  structureLabel?: string;
}

export function OptionChainViewer({
  ticker,
  recommendation,
  preferredDte,
  legs,
  structureLabel,
}: OptionChainViewerProps) {
  const [chain, setChain] = useState<OptionsChainResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Selected expiry (null = "nearest", populated once the user picks).
  // Resets when the ticker changes.
  const [expiry, setExpiry] = useState<string | null>(null);

  // Tracks the preferredDte value we've already auto-applied. Lets us
  // distinguish "the user clicked a new tier pill" (→ redirect to that DTE)
  // from "the chain just reloaded" (→ keep the current expiry, don't override
  // a manual dropdown pick). Without this, a second tier-pill click was
  // ignored because `expiry` was no longer null after the first redirect.
  const appliedDteRef = useRef<number | null>(null);

  useEffect(() => {
    setExpiry(null);
    appliedDteRef.current = null;
  }, [ticker]);

  useEffect(() => {
    let cancelled = false;
    let redirecting = false;  // true when we're about to re-fetch for preferredDte
    // When the pill is toggled off, allow the same DTE to redirect again later.
    if (preferredDte == null) appliedDteRef.current = null;
    setLoading(true);
    setError(null);
    sleevesApi
      .getOptionsChain(ticker, { expiration: expiry ?? undefined })
      .then((data) => {
        if (cancelled) return;
        // If a preferred DTE was passed from a tier pill click AND we haven't
        // applied it yet, silently redirect to the closest available expiry so
        // the user sees that tier's chain without an extra click. The ref guard
        // ensures this fires once per distinct pill click — not on every reload,
        // which would clobber a manual expiry pick.
        if (
          preferredDte != null &&
          appliedDteRef.current !== preferredDte &&
          data.available_expirations.length > 0
        ) {
          const closest = data.available_expirations.reduce((best, d) => {
            const diff = Math.abs(daysUntil(d) - preferredDte);
            const bestDiff = Math.abs(daysUntil(best) - preferredDte);
            return diff < bestDiff ? d : best;
          });
          appliedDteRef.current = preferredDte;
          if (closest !== data.expiration) {
            redirecting = true;
            setExpiry(closest);
            // Keep loading=true — the second fetch will turn it off.
            return;
          }
        }
        setChain(data);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled && !redirecting) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ticker, expiry, preferredDte]);

  // Recommended-strike resolution. Computed before any early-return so the
  // hooks fire in the same order every render (Rules of Hooks).
  //
  // Key behaviour: when the user picks a different expiry from the dropdown,
  // the strike offset is *re-scaled* by √(days/7). The intuition: at 7 days
  // a +2% OTM call is one weekly standard deviation away; at 30 days the
  // same statistical reach is ~+4%, at 60 days ~+6%. This keeps the
  // recommended strike "comparably aggressive" regardless of expiry pick.
  const callsList = chain?.calls ?? [];
  const putsList = chain?.puts ?? [];
  const spot = chain?.spot ?? 0;
  const selectedExpiration = chain?.expiration ?? null;

  const daysToSelected = useMemo(() => {
    if (!selectedExpiration) return null;
    const t = Date.parse(selectedExpiration);
    if (Number.isNaN(t)) return null;
    const todayUtc = Date.UTC(
      new Date().getUTCFullYear(),
      new Date().getUTCMonth(),
      new Date().getUTCDate(),
    );
    return Math.max(0, Math.round((t - todayUtc) / (1000 * 60 * 60 * 24)));
  }, [selectedExpiration]);

  const scaledOffsetPct = useMemo(() => {
    if (!recommendation) return 0;
    const base = recommendation.strike_offset_pct;
    if (base === 0 || daysToSelected === null) return base;
    // sqrt(days / 7) — BSM moves scale with √time.
    const scale = Math.sqrt(Math.max(1, daysToSelected) / 7);
    return base * scale;
  }, [recommendation, daysToSelected]);

  const recTargetStrike = useMemo(() => {
    if (!recommendation || !spot) return null;
    // An absolute strike (e.g. the pattern scanner's recommended contract)
    // pins the highlight exactly; otherwise use the time-scaled offset.
    if (recommendation.strike_abs != null) return recommendation.strike_abs;
    return spot * (1 + scaledOffsetPct / 100);
  }, [recommendation, spot, scaledOffsetPct]);
  const recStrike = useMemo(() => {
    if (!recommendation || recTargetStrike === null) return null;
    const pool = recommendation.direction === 'call' ? callsList : putsList;
    return nearestStrike(pool, recTargetStrike);
  }, [recommendation, recTargetStrike, callsList, putsList]);
  const recContract: OptionContract | null = useMemo(() => {
    if (recStrike === null || !recommendation) return null;
    const pool = recommendation.direction === 'call' ? callsList : putsList;
    return pool.find((c) => c.strike === recStrike) ?? null;
  }, [recStrike, recommendation, callsList, putsList]);

  // Multi-leg resolution. Each leg's offset is sqrt-time scaled (same as the
  // single-leg path) then snapped to the nearest available strike. When no
  // legs are passed we synthesize one from the single recommendation so the
  // highlight logic below is uniform.
  const resolvedLegs: ResolvedLeg[] = useMemo(() => {
    const scale = (base: number) =>
      base === 0 || daysToSelected === null
        ? base
        : base * Math.sqrt(Math.max(1, daysToSelected) / 7);
    const resolve = (direction: 'call' | 'put', offsetPct: number): number | null => {
      if (!spot) return null;
      const pool = direction === 'call' ? callsList : putsList;
      return nearestStrike(pool, spot * (1 + scale(offsetPct) / 100));
    };
    if (legs && legs.length > 0) {
      return legs
        .map((l) => ({ direction: l.direction, side: l.side, strike: resolve(l.direction, l.strike_offset_pct) }))
        .filter((l): l is ResolvedLeg => l.strike !== null);
    }
    if (recommendation && recStrike !== null) {
      return [{ direction: recommendation.direction, side: 'long', strike: recStrike }];
    }
    return [];
  }, [legs, recommendation, recStrike, spot, daysToSelected, callsList, putsList]);

  const callLegs = useMemo(() => resolvedLegs.filter((l) => l.direction === 'call'), [resolvedLegs]);
  const putLegs = useMemo(() => resolvedLegs.filter((l) => l.direction === 'put'), [resolvedLegs]);

  // Does the picked expiry match the strategy's recommended lean?
  // near = ≤14d, mid = 15–28d, far = 29+
  const expiryMatch: 'match' | 'shorter' | 'longer' | 'unknown' = useMemo(() => {
    if (!recommendation || daysToSelected === null) return 'unknown';
    const lean = recommendation.expiry_lean;
    const actual: 'near' | 'mid' | 'far' =
      daysToSelected <= 14 ? 'near' : daysToSelected <= 28 ? 'mid' : 'far';
    if (actual === lean) return 'match';
    const order = { near: 0, mid: 1, far: 2 };
    return order[actual] < order[lean] ? 'shorter' : 'longer';
  }, [recommendation, daysToSelected]);

  if (loading) {
    return (
      <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-3">
        <SkeletonTable />
        <SkeletonTable />
      </div>
    );
  }

  if (error) {
    return (
      <div className="mt-2 text-xs text-rose-500 italic">Failed to load chain: {error}</div>
    );
  }

  if (!chain || (chain.calls.length === 0 && chain.puts.length === 0)) {
    return (
      <div className="mt-2 text-xs text-muted-foreground italic px-2 py-3 rounded border border-dashed">
        No contracts in the ATM window for the nearest expiry.
      </div>
    );
  }

  const atmStrikeCalls = nearestStrike(chain.calls, chain.spot);
  const atmStrikePuts = nearestStrike(chain.puts, chain.spot);

  return (
    <TooltipProvider delayDuration={200}>
      <div className="mt-2">
        {recommendation && (
          <RecommendationCallout
            recommendation={recommendation}
            contract={recContract}
            ticker={chain.ticker}
            scaledOffsetPct={scaledOffsetPct}
            daysToSelected={daysToSelected}
            expiryMatch={expiryMatch}
            resolvedLegs={resolvedLegs}
            structureLabel={structureLabel}
          />
        )}

        <div className="flex items-center gap-2 mb-2 flex-wrap">
          <BadgeTip label={`spot $${chain.spot.toFixed(2)}`}>
            <div className="font-semibold mb-0.5">Spot price</div>
            Last close for the underlying stock. Strikes are filtered to a
            window around this.
          </BadgeTip>

          {/* Expiry selector — replaces the static badge. Defaults to nearest;
              user can pick any expiry the backend pulled in its horizon. */}
          {chain.available_expirations.length > 0 && (
            <Tooltip>
              <TooltipTrigger asChild>
                <label className="inline-flex items-center gap-1 text-[10px] cursor-help">
                  <span className="text-muted-foreground">expiry</span>
                  <select
                    value={chain.expiration ?? ''}
                    onChange={(e) => setExpiry(e.target.value || null)}
                    onClick={(e) => e.stopPropagation()}
                    className="bg-background border border-border rounded px-1.5 py-0.5 font-mono text-[10px]"
                  >
                    {chain.available_expirations.map((d) => (
                      <option key={d} value={d}>
                        {d} ({daysUntil(d)}d)
                      </option>
                    ))}
                  </select>
                </label>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-xs text-xs leading-relaxed">
                <div className="font-semibold mb-0.5">Expiration date</div>
                Pick from {chain.available_expirations.length} expiries in the
                next {chain.horizon_days} days. Shorter expiries are cheaper but
                decay faster (more theta); longer expiries are pricier but more
                forgiving on timing.
              </TooltipContent>
            </Tooltip>
          )}

          <BadgeTip label={`window ±${chain.atm_window_pct}%`}>
            <div className="font-semibold mb-0.5">ATM window</div>
            Only strikes within this percent of spot are shown. Keeps the view
            focused on actively-traded near-the-money contracts.
          </BadgeTip>
          <div className="flex-1" />
          <span className="hidden md:inline-flex items-center gap-1 text-[10px] text-muted-foreground">
            <Copy className="h-3 w-3" /> click any row to copy trade detail
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 min-w-0">
          <ChainTable
            title="Calls"
            subtitle="bet the stock goes up"
            contracts={chain.calls}
            underlying={chain.ticker}
            atmStrike={atmStrikeCalls}
            highlightLegs={callLegs}
          />
          <ChainTable
            title="Puts"
            subtitle="bet the stock goes down"
            contracts={chain.puts}
            underlying={chain.ticker}
            atmStrike={atmStrikePuts}
            highlightLegs={putLegs}
          />
        </div>
      </div>
    </TooltipProvider>
  );
}

function BadgeTip({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant="outline" className="text-[10px] cursor-help">
          {label}
        </Badge>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs text-xs leading-relaxed">
        {children}
      </TooltipContent>
    </Tooltip>
  );
}

// ─── Chain table ────────────────────────────────────────────────────────────

function ChainTable({
  title,
  subtitle,
  contracts,
  underlying,
  atmStrike,
  highlightLegs,
}: {
  title: string;
  subtitle: string;
  contracts: OptionContract[];
  underlying: string;
  atmStrike: number | null;
  /** Legs (in this option type) to highlight, each tagged long/short. */
  highlightLegs: ResolvedLeg[];
}) {
  if (contracts.length === 0) {
    return (
      <div>
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
          {title}
        </div>
        <div className="text-xs text-muted-foreground italic">No contracts.</div>
      </div>
    );
  }

  return (
    <div className="min-w-0">
      <div className="flex items-baseline gap-2 mb-1">
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {title}
        </div>
        <div className="text-[10px] text-muted-foreground/70">— {subtitle}</div>
      </div>
      <div className="overflow-x-auto">
      <table className="w-full text-[10px] min-w-[480px]">
        <thead>
          <tr className="text-muted-foreground border-b border-border">
            <Th tip={<><b>Strike</b>: the price at which you can buy (call) or sell (put) the stock if you exercise the option.</>}>
              Strike
            </Th>
            <Th tip={<><b>Last</b>: most recent traded price for the option, per share. Multiply by 100 for the per-contract premium.</>}>
              Last
            </Th>
            <Th tip={<><b>Bid / Ask</b>: what buyers will pay vs what sellers want. The gap is the spread you cross to enter or exit. Wider spread = less liquid.</>}>
              Bid/Ask
            </Th>
            <Th tip={<><b>IV (Implied Volatility)</b>: the market's annualized estimate of how much the underlying will move. Higher IV = more expensive option.</>}>
              IV
            </Th>
            <Th tip={<><b>Δ (Delta)</b>: how much the option price moves for each $1 the stock moves. Calls range 0→1, puts 0→−1. ~0.5 ≈ at-the-money.</>}>
              Δ
            </Th>
            <Th tip={<><b>Vol</b>: number of contracts traded today. Higher = more liquid.</>}>
              Vol
            </Th>
            <Th tip={<><b>OI (Open Interest)</b>: total outstanding contracts across all traders. Confirms the strike is actively held.</>}>
              OI
            </Th>
            <th className="w-6" />
          </tr>
        </thead>
        <tbody>
          {contracts.map((c) => {
            const leg = highlightLegs.find((l) => l.strike === c.strike);
            return (
              <OptionLegRow
                key={c.ticker ?? `${c.type}-${c.strike}-${c.expiration}`}
                contract={c}
                underlying={underlying}
                atm={atmStrike !== null && c.strike === atmStrike}
                highlight={leg?.side}
              />
            );
          })}
        </tbody>
      </table>
      </div>
    </div>
  );
}

// ─── Recommendation callout ─────────────────────────────────────────────────

function RecommendationCallout({
  recommendation,
  contract,
  ticker,
  scaledOffsetPct,
  daysToSelected,
  expiryMatch,
  resolvedLegs,
  structureLabel,
}: {
  recommendation: ScreenerRecommendation;
  contract: OptionContract | null;
  ticker: string;
  /** Offset actually used to pick the strike, after sqrt-time scaling. */
  scaledOffsetPct: number;
  daysToSelected: number | null;
  expiryMatch: 'match' | 'shorter' | 'longer' | 'unknown';
  resolvedLegs: ResolvedLeg[];
  structureLabel?: string;
}) {
  const isSpread = resolvedLegs.length > 1;
  const dirLabel = recommendation.direction === 'call' ? 'CALL' : 'PUT';
  const dirCls =
    recommendation.direction === 'call'
      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
      : 'border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-400';

  // Build a short "strike rationale" suffix: ATM, or "+X% OTM (scaled for
  // Yd expiry from base +Z%)" when the picked expiry adjusted the strike.
  const baseOffset = recommendation.strike_offset_pct;
  const offsetText =
    Math.abs(scaledOffsetPct) < 0.05
      ? 'at-the-money'
      : `${scaledOffsetPct > 0 ? '+' : ''}${scaledOffsetPct.toFixed(1)}% ${scaledOffsetPct > 0 ? 'OTM' : 'ITM'}`;
  const offsetScaledNote =
    baseOffset !== 0 && daysToSelected !== null && Math.abs(scaledOffsetPct - baseOffset) > 0.1
      ? ` · scaled from base ${baseOffset > 0 ? '+' : ''}${baseOffset.toFixed(1)}% for ${daysToSelected}d expiry`
      : '';

  return (
    <div className="mb-3 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2">
      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
        <Star className="h-3.5 w-3.5 text-amber-500 fill-amber-500 flex-shrink-0" />
        <span className="text-[10px] uppercase tracking-wide text-amber-700 dark:text-amber-400 font-semibold">
          Recommended trade
        </span>
        <Badge variant="outline" className={cn('text-[10px] font-mono', dirCls)}>
          {dirLabel}
        </Badge>
        {structureLabel && (
          <Badge variant="outline" className="text-[10px] font-mono">
            {structureLabel}
          </Badge>
        )}
        <div className="flex-1" />
        <ExpiryMatchBadge match={expiryMatch} lean={recommendation.expiry_lean} />
      </div>

      {/* Contract / legs — on their own line so multi-leg spreads read cleanly. */}
      {isSpread ? (
        <div className="flex items-center gap-1.5 flex-wrap mb-1.5">
          <span className="text-[11px] font-mono text-muted-foreground">{ticker}</span>
          {resolvedLegs.map((l, i) => (
            <span
              key={i}
              className={cn(
                'inline-flex items-center gap-1 text-[11px] font-mono px-1.5 py-0.5 rounded border',
                l.side === 'long'
                  ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
                  : 'border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-400',
              )}
            >
              <span className="font-bold">{l.side === 'long' ? 'BUY' : 'SELL'}</span>
              {l.strike.toFixed(2)}{recommendation.direction === 'call' ? 'C' : 'P'}
            </span>
          ))}
          {daysToSelected !== null && (
            <span className="text-[11px] text-muted-foreground">exp {daysToSelected}d</span>
          )}
        </div>
      ) : contract ? (
        <div className="text-xs font-mono mb-1.5">
          {ticker} ${contract.strike.toFixed(2)} {recommendation.direction === 'call' ? 'C' : 'P'} · exp {contract.expiration}
          {daysToSelected !== null && (
            <span className="text-muted-foreground"> ({daysToSelected}d)</span>
          )}
        </div>
      ) : (
        <div className="text-xs italic text-muted-foreground mb-1.5">
          no matching contract in current expiry — try a different expiry
        </div>
      )}
      <div className="text-xs leading-relaxed text-foreground/85">
        <span className="text-muted-foreground">Strike rationale:</span> {offsetText}
        <span className="text-muted-foreground">{offsetScaledNote}</span>
      </div>
      <div className="text-xs leading-relaxed text-foreground/85 mt-1">
        {recommendation.reasoning}
      </div>
    </div>
  );
}

function ExpiryMatchBadge({
  match,
  lean,
}: {
  match: 'match' | 'shorter' | 'longer' | 'unknown';
  lean: 'near' | 'mid' | 'far';
}) {
  const leanLabel =
    lean === 'near' ? 'near-term (≤14d)' : lean === 'mid' ? 'mid (2–4 weeks)' : 'far (1+ month)';

  if (match === 'unknown') {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="text-[10px] text-muted-foreground cursor-help">
            recommends {leanLabel}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs text-xs leading-relaxed">
          The strategy recommends a <b>{lean}</b> expiry for this setup.
        </TooltipContent>
      </Tooltip>
    );
  }

  if (match === 'match') {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex items-center gap-1 text-[10px] text-emerald-700 dark:text-emerald-400 cursor-help">
            ✓ matches recommended {lean} expiry
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs text-xs leading-relaxed">
          The picked expiry is in the {leanLabel} range the strategy recommends.
        </TooltipContent>
      </Tooltip>
    );
  }

  const direction = match === 'shorter' ? 'shorter' : 'longer';
  const advice =
    match === 'shorter'
      ? 'Theta will bite harder if the move takes time. Consider a longer expiry to align with the thesis.'
      : 'Theta is cheaper but you pay more premium up front. Consider a shorter expiry for the same directional bet.';
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex items-center gap-1 text-[10px] text-amber-700 dark:text-amber-400 cursor-help">
          ⚠ {direction} than recommended {lean} expiry
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs text-xs leading-relaxed">
        The strategy recommends {leanLabel}, but you've picked a {direction} expiry. {advice}
      </TooltipContent>
    </Tooltip>
  );
}


function Th({ tip, children }: { tip: React.ReactNode; children: React.ReactNode }) {
  return (
    <th className="text-left font-medium px-2 py-1">
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="cursor-help underline decoration-dotted underline-offset-2">
            {children}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs text-xs leading-relaxed">
          {tip}
        </TooltipContent>
      </Tooltip>
    </th>
  );
}

function SkeletonTable() {
  return (
    <div className="space-y-1">
      <div className="h-3 w-12 rounded bg-muted-foreground/20 animate-pulse" />
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="h-5 w-full rounded bg-muted-foreground/10 animate-pulse" />
      ))}
    </div>
  );
}

function daysUntil(iso: string): number {
  const target = Date.parse(iso);
  if (Number.isNaN(target)) return 0;
  const todayUtc = Date.UTC(
    new Date().getUTCFullYear(),
    new Date().getUTCMonth(),
    new Date().getUTCDate(),
  );
  return Math.max(0, Math.round((target - todayUtc) / (1000 * 60 * 60 * 24)));
}

function nearestStrike(contracts: OptionContract[], spot: number): number | null {
  if (contracts.length === 0) return null;
  let best = contracts[0];
  for (const c of contracts) {
    if (Math.abs(c.strike - spot) < Math.abs(best.strike - spot)) {
      best = c;
    }
  }
  return best.strike;
}
