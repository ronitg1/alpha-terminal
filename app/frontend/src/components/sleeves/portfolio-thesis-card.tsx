/**
 * PortfolioThesisCard — condensed portfolio-level read with optional
 * LLM-synthesized PM memo.
 *
 * Two views overlaid on the same card:
 *   1. Deterministic readout (always available, zero cost). Computed from
 *      the latestScan rows via derive-bias. This is the default content.
 *   2. LLM PM memo (POST /sleeves/thesis/portfolio). Requires a click to
 *      generate so we don't burn DeepSeek credits on every page load. Once
 *      generated, the result is cached server-side by scan signature, so
 *      subsequent clicks are free.
 *
 * Expand toggle reveals per-sleeve breakdown for the deterministic view.
 * When the LLM memo is loaded, "Expand" shows the full multi-paragraph
 * memo instead.
 */
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { cn } from '@/lib/utils';
import { sleevesApi } from '@/services/sleeves-api';
import type { Thesis } from '@/types/sleeves';
import {
  ChevronDown,
  ChevronUp,
  FileText,
  RefreshCw,
  Sparkles,
  Wand2,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import {
  readoutForPortfolio,
  readoutForSleeve,
} from './utils/derive-bias';
import { biasLabel } from './utils/derive-bias';

export function PortfolioThesisCard() {
  const { config, latestScan } = useSleevesContext();
  const [expanded, setExpanded] = useState(false);
  const [thesis, setThesis] = useState<Thesis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sleeves = config?.sleeves ?? [];
  const portfolio = useMemo(
    () => readoutForPortfolio(sleeves, latestScan),
    [sleeves, latestScan],
  );
  const perSleeve = useMemo(
    () =>
      sleeves.map((s) => ({
        sleeve: s,
        readout: readoutForSleeve(s, latestScan),
      })),
    [sleeves, latestScan],
  );

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await sleevesApi.getPortfolioThesis();
      setThesis(r);
      setExpanded(true);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  if (!latestScan || latestScan.rows.length === 0) {
    return (
      <Card className="mx-6 my-3 p-4 border-dashed">
        <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
          <FileText className="h-3.5 w-3.5" />
          Portfolio Thesis
        </div>
        <p className="mt-1.5 text-sm text-muted-foreground italic">
          Run a portfolio scan to generate a thesis. Until then, no synthesis is
          available.
        </p>
      </Card>
    );
  }

  // ─── Condensed view (always visible) ───────────────────────────────────────
  const topSleeve = [...perSleeve]
    .filter(({ readout }) => readout.scanned > 0)
    .sort((a, b) => b.readout.weightedConv - a.readout.weightedConv)[0];

  const bottomSleeve = [...perSleeve]
    .filter(({ readout }) => readout.scanned > 0)
    .sort((a, b) => a.readout.weightedConv - b.readout.weightedConv)[0];

  const variantTickers = (latestScan.rows ?? []).filter(
    (r) => r.has_variant_perception,
  );

  return (
    <Card className="mx-6 my-3 p-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
          <FileText className="h-3.5 w-3.5" />
          Portfolio Thesis
          <span className="font-mono normal-case tracking-normal text-[10px] text-muted-foreground/70">
            · synthesized from {portfolio.scanned} ticker
            {portfolio.scanned === 1 ? '' : 's'}
            {thesis && (
              <>
                {' '}
                · LLM bias:{' '}
                <strong className={biasColorFor(thesis.bias)}>
                  {biasLabel(thesis.bias as never)}
                </strong>
              </>
            )}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant={thesis ? 'ghost' : 'outline'}
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={handleGenerate}
            disabled={loading}
            title={
              thesis
                ? 'Re-generate the LLM PM memo (cached by scan signature)'
                : 'Synthesize a PM-style memo via DeepSeek (~$0.05-$0.20)'
            }
          >
            {loading ? (
              <RefreshCw className="h-3.5 w-3.5 mr-1 animate-spin" />
            ) : (
              <Wand2 className="h-3.5 w-3.5 mr-1" />
            )}
            {thesis ? 'Refresh memo' : 'Generate LLM memo'}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? (
              <>
                <ChevronUp className="h-3.5 w-3.5 mr-1" /> Collapse
              </>
            ) : (
              <>
                <ChevronDown className="h-3.5 w-3.5 mr-1" /> Expand
              </>
            )}
          </Button>
        </div>
      </div>

      {error && (
        <div className="mt-2 text-xs px-2 py-1.5 rounded border border-rose-500/30 bg-rose-500/5 text-rose-700 dark:text-rose-400">
          Thesis call failed: {error}
        </div>
      )}

      {/* Condensed: prefer LLM if loaded, otherwise deterministic. */}
      <div className="mt-3 space-y-1.5 text-sm leading-relaxed">
        {thesis ? (
          <p>{thesis.condensed}</p>
        ) : (
          <>
            <p>
              We see a{' '}
              <strong className={biasColorFor(portfolio.bias)}>
                {biasLabel(portfolio.bias).toLowerCase()}
              </strong>{' '}
              read across the book this scan ·{' '}
              <span className="font-mono">{portfolio.bullish}</span> bullish vs{' '}
              <span className="font-mono">{portfolio.bearish}</span> bearish out of{' '}
              <span className="font-mono">{portfolio.scanned}</span> scanned, weighted
              conviction <span className="font-mono">{Math.round(portfolio.weightedConv)}</span>.
            </p>
            {topSleeve && (
              <p className="text-muted-foreground">
                Strongest signal cluster is{' '}
                <strong className="text-foreground capitalize">
                  {topSleeve.sleeve.name.replace(/_/g, ' ')}
                </strong>{' '}
                (conv {Math.round(topSleeve.readout.weightedConv)}); softest is{' '}
                <strong className="text-foreground capitalize">
                  {bottomSleeve?.sleeve.name.replace(/_/g, ' ')}
                </strong>{' '}
                (conv {bottomSleeve ? Math.round(bottomSleeve.readout.weightedConv) : '—'}).
              </p>
            )}
            {variantTickers.length > 0 && (
              <p className="text-amber-700 dark:text-amber-400 inline-flex items-center gap-1.5">
                <Sparkles className="h-3.5 w-3.5" />
                {variantTickers.length} name
                {variantTickers.length === 1 ? '' : 's'} flagged variant perception ·{' '}
                <span className="font-mono">
                  {variantTickers.slice(0, 4).map((r) => r.ticker).join(', ')}
                  {variantTickers.length > 4 ? '…' : ''}
                </span>
              </p>
            )}
          </>
        )}
      </div>

      {expanded && thesis && (
        <div className="mt-4 pt-4 border-t border-border space-y-3 text-sm leading-relaxed whitespace-pre-wrap">
          {thesis.full}
          <div className="text-[10px] text-muted-foreground italic pt-2 border-t border-border/50">
            Generated {new Date(thesis.generated_at).toLocaleString()} via
            DeepSeek · cached server-side by scan signature. Re-run scan to
            invalidate.
          </div>
        </div>
      )}

      {expanded && !thesis && (
        <div className="mt-4 pt-4 border-t border-border space-y-3 text-sm">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
            Per-sleeve breakdown (deterministic)
          </div>
          {perSleeve.map(({ sleeve, readout }) => (
            <div key={sleeve.name} className="text-sm">
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-medium capitalize">
                  {sleeve.name.replace(/_/g, ' ')}{' '}
                  <span className="text-xs text-muted-foreground font-mono">
                    {sleeve.allocation_pct.toFixed(0)}%
                  </span>
                </span>
                <span className={cn('text-xs', biasColorFor(readout.bias))}>
                  {biasLabel(readout.bias)} · conv{' '}
                  <span className="font-mono">{Math.round(readout.weightedConv)}</span>
                </span>
              </div>
              <div className="text-xs text-muted-foreground mt-0.5">
                {readout.scanned === 0
                  ? 'No tickers scanned for this sleeve.'
                  : `${readout.bullish} bullish · ${readout.bearish} bearish · ${readout.neutral} neutral · ${readout.highConv} high-conviction${readout.variant > 0 ? ` · ${readout.variant} variant ✨` : ''}`}
              </div>
            </div>
          ))}
          <div className="text-[11px] text-muted-foreground italic pt-2 border-t border-border/50">
            Click "Generate LLM memo" above for a PM-voice synthesis.
          </div>
        </div>
      )}
    </Card>
  );
}

function biasColorFor(bias: string): string {
  switch (bias) {
    case 'bullish':
      return 'text-emerald-600 dark:text-emerald-400';
    case 'bearish':
      return 'text-rose-600 dark:text-rose-400';
    case 'mixed':
      return 'text-amber-600 dark:text-amber-400';
    default:
      return 'text-muted-foreground';
  }
}
