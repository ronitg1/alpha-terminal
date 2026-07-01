/**
 * Portfolio Thesis tab (M6): run the AI agent/thesis engine on your holdings.
 * Reuses the per-ticker thesis endpoint (grounded in fundamentals + any saved
 * agent analysis) for each underlying you hold. Runs are on-demand and cost LLM
 * credits, so they're per-ticker with a sequential "Run all". Responsive (#8).
 */
import { sleevesApi } from '@/services/sleeves-api';
import { cn } from '@/lib/utils';
import type { PortfolioAccount } from '@/types/portfolio';
import type { TickerThesis, Valuation } from '@/types/sleeves';
import { ChevronDown, ChevronRight, Sparkles } from 'lucide-react';
import { useMemo, useState } from 'react';
import { toast } from 'sonner';

const BIAS_CLASS: Record<string, string> = {
  bullish: 'bg-emerald-500/15 text-emerald-500',
  bearish: 'bg-rose-500/15 text-rose-500',
  mixed: 'bg-amber-500/15 text-amber-500',
  neutral: 'bg-muted text-muted-foreground',
};

/**
 * Valuation football field: one horizontal band per method (low→high, mid ticked)
 * on a shared scale, with a vertical line at the current price so you can see at a
 * glance whether the market is above or below the estimated fair value.
 */
function FootballField({ v }: { v: Valuation }) {
  if (!v.available || !v.bands?.length || v.current_price == null) return null;
  const price = v.current_price;
  const lows = v.bands.map((b) => b.low);
  const highs = v.bands.map((b) => b.high);
  const min = Math.min(...lows, price);
  const max = Math.max(...highs, price);
  const pad = (max - min) * 0.08 || 1;
  const lo = min - pad;
  const hi = max + pad;
  const x = (val: number) => ((val - lo) / (hi - lo)) * 100;
  const up = v.upside_pct ?? 0;
  const upTone = up > 3 ? 'text-emerald-500' : up < -3 ? 'text-rose-500' : 'text-muted-foreground';

  return (
    <div className="mt-3 rounded-md border border-border/60 bg-background/40 p-3">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Valuation</span>
        <span className="text-[11px]">
          Fair ~${v.fair_value} <span className={cn('font-medium', upTone)}>({up >= 0 ? '+' : ''}{up}%)</span>
        </span>
      </div>
      <div className="relative">
        <div className="space-y-2">
          {v.bands.map((b) => (
            <div key={b.method}>
              <div className="mb-0.5 flex justify-between text-[10px] text-muted-foreground">
                <span>{b.method}</span>
                <span className="tabular-nums">${b.low}–${b.high}</span>
              </div>
              <div className="relative h-2 rounded bg-muted">
                <div
                  className="absolute inset-y-0 rounded bg-primary/30"
                  style={{ left: `${x(b.low)}%`, width: `${Math.max(1, x(b.high) - x(b.low))}%` }}
                />
                <div
                  className="absolute top-1/2 h-3 w-0.5 -translate-x-1/2 -translate-y-1/2 rounded bg-primary"
                  style={{ left: `${x(b.mid)}%` }}
                  title={`${b.method} mid $${b.mid}`}
                />
              </div>
            </div>
          ))}
        </div>
        {/* Current-price reference line across all bands */}
        <div
          className="pointer-events-none absolute inset-y-0 w-px -translate-x-1/2 bg-foreground/70"
          style={{ left: `${x(price)}%` }}
        />
      </div>
      <div className="mt-1.5 flex justify-between text-[9px] tabular-nums text-muted-foreground">
        <span>${lo.toFixed(0)}</span>
        <span className="font-medium text-foreground">price ${price}</span>
        <span>${hi.toFixed(0)}</span>
      </div>
    </div>
  );
}

function ThesisRow({ ticker }: { ticker: string }) {
  const [thesis, setThesis] = useState<TickerThesis | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  const run = async () => {
    setLoading(true);
    try {
      const t = await sleevesApi.getTickerThesis(ticker, 'quick');
      setThesis(t);
      setOpen(true);
    } catch (e) {
      toast.error(`${ticker}: ${e instanceof Error ? e.message : e}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="rounded-lg border border-border/60 bg-card">
      <div className="flex items-center gap-2 px-3 py-2">
        <button type="button" onClick={() => thesis && setOpen((o) => !o)} className="flex items-center gap-1.5">
          {thesis ? (open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />) : <span className="w-3.5" />}
          <span className="font-mono text-sm font-semibold">{ticker}</span>
        </button>
        {thesis && (
          <span className={cn('rounded px-1.5 py-0.5 text-[10px] font-medium capitalize', BIAS_CLASS[thesis.bias] ?? BIAS_CLASS.neutral)}>
            {thesis.bias}
          </span>
        )}
        <button
          type="button"
          onClick={() => void run()}
          disabled={loading}
          className="ml-auto inline-flex items-center gap-1 rounded border border-primary/40 bg-primary/5 px-2 py-1 text-[11px] text-primary hover:bg-primary/10 disabled:opacity-50"
        >
          <Sparkles className={cn('h-3 w-3', loading && 'animate-pulse')} />
          {loading ? 'Analyzing…' : thesis ? 'Rerun' : 'Run analysis'}
        </button>
      </div>
      {thesis && (
        <div className="border-t border-border/60 px-3 py-2">
          <p className="text-xs leading-relaxed">{thesis.condensed}</p>
          {open && thesis.valuation?.available && <FootballField v={thesis.valuation} />}
          {open && thesis.full && thesis.full !== thesis.condensed && (
            <p className="mt-2 whitespace-pre-line text-[11px] leading-relaxed text-muted-foreground">{thesis.full}</p>
          )}
        </div>
      )}
    </div>
  );
}

export function PortfolioThesis({ account }: { account: PortfolioAccount }) {
  const [runToken, setRunToken] = useState(0);
  const [runningAll, setRunningAll] = useState(false);
  const tickers = useMemo(
    () => Array.from(new Set(account.positions.filter((p) => p.kind === 'stock' && p.underlying).map((p) => p.underlying))) as string[],
    [account],
  );

  // "Run all" just re-mounts rows with an auto-run flag would be complex; instead
  // we sequentially fire each ticker's endpoint (warms the per-(ticker,day) cache
  // the rows then read). Kept sequential to avoid hammering the LLM.
  const runAll = async () => {
    setRunningAll(true);
    try {
      for (const t of tickers) {
        try { await sleevesApi.getTickerThesis(t, 'quick'); } catch { /* skip one */ }
      }
      setRunToken((n) => n + 1); // remount rows so they pick up the warmed cache
      toast.success('Ran AI analysis on your holdings.');
    } finally {
      setRunningAll(false);
    }
  };

  if (tickers.length === 0) {
    return <p className="p-4 text-sm italic text-muted-foreground">No stock holdings to analyze.</p>;
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <p className="text-xs text-muted-foreground">
          Run the AI agents on each holding for a quick thesis. Uses your DeepSeek key (Settings) and costs LLM credits.
        </p>
        <button
          type="button"
          onClick={() => void runAll()}
          disabled={runningAll}
          className="ml-auto inline-flex items-center gap-1 rounded border border-primary/40 bg-primary/5 px-2 py-1 text-[11px] text-primary hover:bg-primary/10 disabled:opacity-50"
        >
          <Sparkles className={cn('h-3 w-3', runningAll && 'animate-pulse')} />
          {runningAll ? 'Running…' : 'Run all'}
        </button>
      </div>
      <div className="space-y-2" key={runToken}>
        {tickers.map((t) => <ThesisRow key={t} ticker={t} />)}
      </div>
    </div>
  );
}
