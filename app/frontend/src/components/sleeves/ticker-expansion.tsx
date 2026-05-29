/**
 * TickerExpansion — inline expanded view of a ticker, rendered below its
 * TickerRow when the row is selected.
 *
 * Replaces the right-side TickerDrillDrawer (Phase B). Same content shape:
 *   - Condensed thesis (variant perception or directional lean)
 *   - Price sparkline (90d) + fundamentals snapshot
 *   - Per-agent verdicts with rich fields
 *   - Recent news
 *
 * Why inline beats a drawer:
 *   - User can keep two rows open at once for comparison
 *   - No focus-trap modal context-switch when clicking around
 *   - Content can flow with the page instead of being a fixed-width panel
 *
 * The "Condensed" view is always visible; a "Full thesis" toggle reveals
 * the per-agent rich-field breakdown. Mirrors the portfolio + sleeve
 * thesis pattern.
 */
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { sleevesApi } from '@/services/sleeves-api';
import type {
  PerAgentVerdict,
  TickerData,
  TickerRow as TickerRowData,
} from '@/types/sleeves';
import {
  ChevronDown,
  ChevronUp,
  Sparkles,
  X,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { AnalystChip } from './analyst-chip';
import { CompanyOverviewCard } from './company-overview-card';
import { PriceSparkline } from './price-sparkline';
import { RecentNewsList } from './recent-news-list';
import { SignalPill } from './signal-pill';
import { TrafficLight } from './traffic-light';
import { cn } from '@/lib/utils';
import {
  pctChange,
  slicePrices,
  TIMEFRAMES,
  Timeframe,
} from './utils/slice-prices';

interface TickerExpansionProps {
  /** Always provided. When row is null this is the only source for the
   *  symbol (the ticker isn't in latestScan yet). */
  ticker: string;
  /** Scan row when the ticker has been scanned this cycle; null otherwise. */
  row: TickerRowData | null;
  onClose: () => void;
}

export function TickerExpansion({ ticker, row, onClose }: TickerExpansionProps) {
  const [tickerData, setTickerData] = useState<TickerData | null>(null);
  const [loading, setLoading] = useState(true);
  const [showFull, setShowFull] = useState(false);
  // Persist timeframe choice in localStorage so the user's preference
  // sticks across expansions. Default 3M because it shows enough trend
  // without being a fire-hose.
  const [timeframe, setTimeframe] = useState<Timeframe>(() => {
    const stored = (
      typeof window !== 'undefined' &&
      window.localStorage.getItem('chart-timeframe')
    ) as Timeframe | null;
    return stored && TIMEFRAMES.some((t) => t.label === stored)
      ? stored
      : '3M';
  });
  const handlePickTimeframe = (tf: Timeframe) => {
    setTimeframe(tf);
    try {
      window.localStorage.setItem('chart-timeframe', tf);
    } catch {
      // localStorage can fail in private mode; non-fatal.
    }
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setTickerData(null);
    sleevesApi
      .getTickerData(ticker)
      .then((data) => {
        if (!cancelled) setTickerData(data);
      })
      .catch((err: Error) => {
        console.error(`getTickerData(${ticker}) failed:`, err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ticker]);

  const condensed = row ? condensedThesis(row) : null;

  return (
    <div className="mx-3 mb-3 p-4 rounded-md border border-border bg-muted/30 space-y-4">
      {/* Header with close */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono font-semibold text-lg">{ticker}</span>
            {row?.has_variant_perception && (
              <Sparkles className="h-4 w-4 text-amber-500" />
            )}
            {row ? (
              <>
                <SignalPill
                  signal={row.consensus}
                  confidence={row.avg_confidence}
                  compact
                />
                <span className="font-mono text-xs text-muted-foreground">
                  score {row.weighted_score.toFixed(1)} ·{' '}
                  {row.hold_period.replace(/_/g, ' ')}
                </span>
              </>
            ) : (
              <span className="text-xs text-muted-foreground italic">
                not in latest scan
              </span>
            )}
          </div>
          {row && (
            <div className="mt-1 text-xs uppercase tracking-wide text-muted-foreground">
              {row.sleeve.replace(/_/g, ' ')} · {row.position_type.replace(/_/g, ' ')}
            </div>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 w-7 p-0"
          onClick={onClose}
          title="Close detail"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      {/* Condensed thesis — only shown when there's scan data. */}
      {condensed && (
        <div className="text-sm leading-relaxed">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
            Condensed thesis
          </div>
          <p className="italic">"{condensed}"</p>
        </div>
      )}

      {/* Price chart + fundamentals — useful even without full thesis. */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div>
          <div className="flex items-center justify-between gap-2 mb-1.5">
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
              Price
            </div>
            <PriceChangeBadge
              prices={slicePrices(
                tickerData?.price_history ?? [],
                timeframe,
              )}
              timeframe={timeframe}
            />
          </div>
          {loading ? (
            <div className="h-24 w-full rounded bg-muted-foreground/5 animate-pulse" />
          ) : (
            <PriceSparkline
              prices={slicePrices(
                tickerData?.price_history ?? [],
                timeframe,
              )}
              height={120}
            />
          )}
          {/* Timeframe selector — pill row, equal-weight, active is filled. */}
          <div className="mt-2 inline-flex rounded-md border border-border overflow-hidden text-[11px] font-mono">
            {TIMEFRAMES.map((tf, i) => (
              <button
                key={tf.label}
                type="button"
                onClick={() => handlePickTimeframe(tf.label)}
                className={cn(
                  'px-2 py-0.5 transition-colors',
                  i > 0 && 'border-l border-border',
                  timeframe === tf.label
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:bg-accent/40 hover:text-foreground',
                )}
              >
                {tf.label}
              </button>
            ))}
          </div>
        </div>
        <div>
          <CompanyOverviewCard
            data={tickerData}
            loading={loading}
            ticker={ticker}
          />
        </div>
      </div>

      {/* Full-thesis toggle (only when there are agent verdicts to show) */}
      {row && (
        <div className="flex items-center justify-between">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            Per-agent breakdown · {row.per_agent.length}
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => setShowFull((v) => !v)}
          >
            {showFull ? (
              <>
                <ChevronUp className="h-3.5 w-3.5 mr-1" /> Hide full thesis
              </>
            ) : (
              <>
                <ChevronDown className="h-3.5 w-3.5 mr-1" /> Show full thesis
              </>
            )}
          </Button>
        </div>
      )}

      {row && showFull && (
        <>
          {row.per_agent.length === 0 ? (
            <div className="text-sm text-muted-foreground italic">
              No agent verdicts.
            </div>
          ) : (
            <div className="space-y-3">
              {row.per_agent.map((v) => (
                <AgentVerdictBlock
                  key={v.agent}
                  verdict={v}
                  rowFallback={row.variant_perception}
                />
              ))}
            </div>
          )}

          <Separator />
          <div>
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1.5">
              Recent News
            </div>
            <RecentNewsList
              items={tickerData?.recent_news ?? []}
              loading={loading}
            />
          </div>
        </>
      )}

      {/* Always show recent news on unscanned tickers (no full-thesis
          accordion to gate behind) so the user still gets context. */}
      {!row && (
        <>
          <Separator />
          <div>
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1.5">
              Recent News
            </div>
            <RecentNewsList
              items={tickerData?.recent_news ?? []}
              loading={loading}
            />
          </div>
        </>
      )}
    </div>
  );
}

// ─── Price-change badge ────────────────────────────────────────────────────

function PriceChangeBadge({
  prices,
  timeframe,
}: {
  prices: ReturnType<typeof slicePrices>;
  timeframe: Timeframe;
}) {
  const pct = pctChange(prices);
  if (prices.length === 0) return null;
  if (pct == null) {
    return (
      <span className="text-[11px] font-mono text-muted-foreground">
        {timeframe} · —
      </span>
    );
  }
  const last = prices[prices.length - 1].close;
  const positive = pct >= 0;
  return (
    <span className="text-[11px] font-mono inline-flex items-center gap-2">
      <span className="text-foreground font-semibold">
        ${last < 100 ? last.toFixed(2) : last.toFixed(1)}
      </span>
      <span
        className={
          positive
            ? 'text-emerald-600 dark:text-emerald-400'
            : 'text-rose-600 dark:text-rose-400'
        }
      >
        {positive ? '+' : ''}
        {pct.toFixed(2)}%
      </span>
      <span className="text-muted-foreground">· {timeframe}</span>
    </span>
  );
}


// ─── Condensed thesis derivation ───────────────────────────────────────────

/** Flatten a structured-reasoning object (Fundamentals-agent style) into a
 *  short sentence-friendly summary. Picks any 'signal' / 'details' subfields
 *  the agents emit. Falls back to JSON if the shape is exotic. */
function summarizeStructuredReasoning(
  obj: Record<string, unknown>,
): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(obj)) {
    if (value == null) continue;
    if (typeof value === 'string' || typeof value === 'number') {
      parts.push(`${key.replace(/_/g, ' ')}: ${value}`);
      continue;
    }
    if (typeof value === 'object') {
      const v = value as Record<string, unknown>;
      const sig = v.signal != null ? String(v.signal) : null;
      const det = v.details != null ? String(v.details) : null;
      if (sig || det) {
        parts.push(
          `${key.replace(/_signal$/, '').replace(/_/g, ' ')}: ${[sig, det]
            .filter(Boolean)
            .join(' — ')}`,
        );
      }
    }
  }
  if (parts.length === 0) {
    try {
      return JSON.stringify(obj);
    } catch {
      return '';
    }
  }
  return parts.join('. ') + '.';
}

function condensedThesis(row: TickerRowData): string {
  if (row.variant_perception && row.variant_perception.toLowerCase() !== 'no edge — skip') {
    return row.variant_perception;
  }
  // Fall back to the dominant agent's first reasoning sentence.
  if (row.per_agent.length === 0) {
    return `${row.consensus} consensus at ${Math.round(row.avg_confidence)}% confidence; no contrarian thesis available.`;
  }
  const dominant = [...row.per_agent].sort(
    (a, b) => b.confidence - a.confidence,
  )[0];
  const raw = dominant.raw ?? {};
  // The Fundamentals agent emits reasoning as a nested object rather than a
  // string — coerce to a flat string so the condensed-thesis snippet logic
  // (sentence-trim) doesn't crash on the object shape.
  const rRaw = raw.reasoning;
  const r =
    typeof rRaw === 'string'
      ? rRaw
      : rRaw && typeof rRaw === 'object'
        ? summarizeStructuredReasoning(rRaw as Record<string, unknown>)
        : '';
  if (r) {
    const firstSentence = r.match(/^[^.!?\n]+[.!?]?/)?.[0] ?? r;
    return firstSentence.length > 220
      ? firstSentence.slice(0, 220) + '…'
      : firstSentence;
  }
  return `${dominant.agent.replace(/_/g, ' ')} leads at ${Math.round(dominant.confidence)}% ${dominant.signal}; ${row.per_agent.length} agent${row.per_agent.length === 1 ? '' : 's'} weighing in.`;
}

// ─── Per-agent rich-field block ────────────────────────────────────────────

function AgentVerdictBlock({
  verdict,
  rowFallback,
}: {
  verdict: PerAgentVerdict;
  rowFallback: string;
}) {
  return (
    <div className="rounded border border-border/60 p-3">
      <div className="flex items-center gap-3">
        <AnalystChip
          agentKey={verdict.agent}
          variant="inline"
          className="text-sm"
        />
        <div className="flex-1" />
        <SignalPill
          signal={verdict.signal}
          confidence={verdict.confidence}
          compact
        />
        <span className="font-mono text-xs text-muted-foreground tabular-nums w-8 text-right">
          {Math.round(verdict.confidence)}
        </span>
      </div>
      <div className="mt-2">
        <AgentRichFields verdict={verdict} rowFallback={rowFallback} />
      </div>
    </div>
  );
}

function AgentRichFields({
  verdict,
  rowFallback,
}: {
  verdict: PerAgentVerdict;
  rowFallback: string;
}) {
  const raw = verdict.raw ?? {};
  const hasRaw = Object.keys(raw).length > 0;

  if (!hasRaw) {
    return (
      <div className="text-xs text-muted-foreground italic">
        No rich output captured.
        {rowFallback && (
          <>
            {' '}
            Row variant perception: <span className="not-italic">"{rowFallback}"</span>
          </>
        )}
      </div>
    );
  }

  const variant = asString(raw.variant_perception);
  // Reasoning can be either a plain string (most agents) or a structured
  // object (Fundamentals: {profitability_signal, growth_signal, ...}).
  // Keep both shapes — the render path branches on typeof.
  const reasoning = raw.reasoning as string | Record<string, unknown> | undefined;
  const killSwitch = asString(raw.kill_switch);
  const catalystNear = asString(raw.catalyst_near_term);
  const catalystMedium = asString(raw.catalyst_medium_term);
  const probWrong = asString(raw.probability_wrong);

  const iraStack = asString(raw.ira_credit_stack);
  const feocRisk = asString(raw.feoc_risk);
  const subSector = asString(raw.sub_sector);
  const unitEcon = asString(raw.unit_economics_note);

  const techCat = asString(raw.tech_category);
  const moatType = asString(raw.moat_type);
  const moatDur = asString(raw.moat_durability);
  const sCurve = asString(raw.s_curve_position);
  const aiExposure = asString(raw.ai_exposure);
  const aiTailwind = asString(raw.ai_tailwind);
  const valuation = asString(raw.valuation_assessment);
  const competitors = asString(raw.competitors_note);

  return (
    <div className="space-y-2 text-xs">
      {variant && variant.toLowerCase() !== 'no edge — skip' && (
        <Field label="Variant perception" value={`"${variant}"`} italic />
      )}

      {(iraStack || feocRisk || subSector || unitEcon) && (
        <div className="grid grid-cols-2 gap-2">
          {subSector && <KVChip label="Sub-sector" value={subSector} />}
          {iraStack && <KVChip label="IRA credit stack" value={iraStack} />}
          {feocRisk && (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">
                FEOC risk
              </div>
              <TrafficLight status={feocRisk} field="feoc risk" />
            </div>
          )}
          {unitEcon && <KVChip label="Unit econ" value={unitEcon} span={2} />}
        </div>
      )}

      {(techCat ||
        moatType ||
        moatDur ||
        sCurve ||
        aiExposure ||
        aiTailwind ||
        valuation) && (
        <div className="grid grid-cols-2 gap-2">
          {techCat && <KVChip label="Tech category" value={techCat} />}
          {moatType && (
            <KVChip
              label="Moat"
              value={`${moatType}${moatDur ? ` · ${moatDur}` : ''}`}
            />
          )}
          {sCurve && <KVChip label="S-curve" value={sCurve} />}
          {aiExposure && (
            <KVChip
              label="AI exposure"
              value={`${aiExposure}${aiTailwind ? ` · ${aiTailwind}` : ''}`}
            />
          )}
          {valuation && <KVChip label="Valuation" value={valuation} span={2} />}
        </div>
      )}

      {competitors && <Field label="Competitors" value={competitors} />}

      {(catalystNear || catalystMedium) && (
        <div className="space-y-1">
          {catalystNear && (
            <Field label="Catalyst (0-90d)" value={catalystNear} />
          )}
          {catalystMedium && (
            <Field label="Catalyst (90-365d)" value={catalystMedium} />
          )}
        </div>
      )}

      {killSwitch && (
        <div className="p-2 rounded bg-rose-500/5 border border-rose-500/20">
          <div className="text-[10px] uppercase tracking-wide text-rose-700 dark:text-rose-400 mb-0.5">
            Kill switch
          </div>
          <div className="text-foreground/90">{killSwitch}</div>
        </div>
      )}

      {probWrong && <KVChip label="Probability wrong" value={probWrong} />}

      {reasoning && (
        <div>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
            Reasoning
          </div>
          {typeof reasoning === 'string' ? (
            <div className="leading-relaxed text-foreground/90 whitespace-pre-wrap">
              {reasoning}
            </div>
          ) : (
            <StructuredReasoning data={reasoning} />
          )}
        </div>
      )}
    </div>
  );
}

/** Safely render any raw agent field as text. Strings pass through; numbers
 *  and booleans get stringified; objects/arrays get JSON-flattened so we
 *  never accidentally pass an object to React as a child (the crash that
 *  surfaced as "Objects are not valid as a React child" for Fundamentals). */
function asString(v: unknown): string | undefined {
  if (v == null) return undefined;
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return undefined;
  }
}

/** Render a nested-object reasoning payload (Fundamentals agent style:
 *  {profitability_signal: {signal, details}, growth_signal: {...}, …}) as
 *  a labeled-block list. Falls back to JSON.stringify for unexpected
 *  shapes so we still get _some_ visibility. */
function StructuredReasoning({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data);
  if (entries.length === 0) {
    return (
      <div className="text-xs text-muted-foreground italic">
        Empty reasoning object.
      </div>
    );
  }
  return (
    <div className="space-y-2 text-xs">
      {entries.map(([key, value]) => (
        <div key={key} className="rounded border border-border/40 p-2">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
            {key.replace(/_/g, ' ')}
          </div>
          <SubReasoning value={value} />
        </div>
      ))}
    </div>
  );
}

function SubReasoning({ value }: { value: unknown }) {
  if (value == null) {
    return <span className="text-muted-foreground italic">—</span>;
  }
  if (typeof value === 'string') {
    return <span>{value}</span>;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return <span className="font-mono">{String(value)}</span>;
  }
  if (Array.isArray(value)) {
    return (
      <ul className="list-disc pl-4 space-y-0.5">
        {value.map((v, i) => (
          <li key={i}>
            <SubReasoning value={v} />
          </li>
        ))}
      </ul>
    );
  }
  // Object — render label/value pairs.
  const obj = value as Record<string, unknown>;
  return (
    <div className="space-y-1">
      {Object.entries(obj).map(([k, v]) => (
        <div key={k} className="grid grid-cols-[100px_1fr] gap-2">
          <span className="text-muted-foreground capitalize">
            {k.replace(/_/g, ' ')}
          </span>
          <span className="break-words">
            <SubReasoning value={v} />
          </span>
        </div>
      ))}
    </div>
  );
}

function Field({
  label,
  value,
  italic,
}: {
  label: string;
  value: string;
  italic?: boolean;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">
        {label}
      </div>
      <div className={italic ? 'italic' : ''}>{value}</div>
    </div>
  );
}

function KVChip({
  label,
  value,
  span,
}: {
  label: string;
  value: string;
  span?: number;
}) {
  return (
    <div className={span === 2 ? 'col-span-2' : ''}>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">
        {label}
      </div>
      <Badge variant="outline" className="font-mono text-[11px]">
        {value}
      </Badge>
    </div>
  );
}
