/**
 * FinnhubFinancials — free-tier Finnhub enrichment for the Market tab.
 *
 * Surfaces data Massive doesn't provide: growth/turnover metrics, the earnings
 * beat/miss track record, analyst recommendation consensus, recent insider
 * flow, and peers. Renders nothing when no FINNHUB_API_KEY is configured
 * (configured=false) so the section disappears cleanly rather than erroring.
 * Forward analyst estimates are premium and intentionally not shown.
 */

import { sleevesApi } from '@/services/sleeves-api';
import { FinnhubEarnings, FinnhubFundamentals, FinnhubRecommendation } from '@/types/sleeves';
import { cn } from '@/lib/utils';
import { useEffect, useState } from 'react';

// Metric display config: friendly key → label + formatter. Grouped for layout.
type MetricFmt = (v: number) => string;
const pct: MetricFmt = (v) => `${v.toFixed(1)}%`;
const x: MetricFmt = (v) => `${v.toFixed(2)}×`;
const num: MetricFmt = (v) => v.toFixed(2);

const METRIC_GROUPS: { title: string; rows: [string, string, MetricFmt][] }[] = [
  {
    title: 'Growth',
    rows: [
      ['revenue_growth_ttm', 'Revenue (TTM YoY)', pct],
      ['eps_growth_ttm', 'EPS (TTM YoY)', pct],
      ['revenue_growth_5y', 'Revenue (5Y)', pct],
      ['eps_growth_5y', 'EPS (5Y)', pct],
      ['fcf_cagr_5y', 'FCF CAGR (5Y)', pct],
    ],
  },
  {
    title: 'Efficiency & quality',
    rows: [
      ['gross_margin_ttm', 'Gross margin', pct],
      ['operating_margin_ttm', 'Operating margin', pct],
      ['net_margin_ttm', 'Net margin', pct],
      ['roe_ttm', 'ROE', pct],
      ['roa_ttm', 'ROA', pct],
      ['asset_turnover_ttm', 'Asset turnover', x],
      ['inventory_turnover_ttm', 'Inventory turnover', x],
      ['receivables_turnover_ttm', 'Receivables turnover', x],
    ],
  },
  {
    title: 'Valuation & balance sheet',
    rows: [
      ['pe_ttm', 'P/E (TTM)', num],
      ['pb', 'P/B', num],
      ['ps_ttm', 'P/S (TTM)', num],
      ['dividend_yield', 'Dividend yield', pct],
      ['current_ratio', 'Current ratio', num],
      ['debt_to_equity', 'Debt / equity', num],
      ['beta', 'Beta', num],
    ],
  },
];

export function FinnhubFinancials({ ticker }: { ticker: string }) {
  const [data, setData] = useState<FinnhubFundamentals | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setData(null);
    sleevesApi
      .getTickerFinnhub(ticker)
      .then((d) => { if (!cancelled) setData(d); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [ticker]);

  // Hide entirely when not configured or nothing useful came back.
  if (loading) {
    return (
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="h-4 w-40 rounded bg-muted-foreground/10 animate-pulse mb-3" />
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-8 rounded bg-muted-foreground/5 animate-pulse" />
          ))}
        </div>
      </div>
    );
  }
  if (!data || !data.configured) return null;

  const hasMetrics = data.metrics && Object.keys(data.metrics).length > 0;
  const hasAnything =
    hasMetrics ||
    (data.earnings?.length ?? 0) > 0 ||
    data.recommendation ||
    (data.peers?.length ?? 0) > 0 ||
    data.insider_flow;
  if (!hasAnything) return null;

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-5">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">Financials & analyst data</h2>
        <span className="text-[10px] text-muted-foreground border border-border rounded px-1.5 py-0.5">
          Finnhub
        </span>
      </div>

      {hasMetrics && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-x-6 gap-y-4">
          {METRIC_GROUPS.map((g) => {
            const rows = g.rows.filter(([k]) => data.metrics![k] != null);
            if (rows.length === 0) return null;
            return (
              <div key={g.title}>
                <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1.5">
                  {g.title}
                </div>
                <dl className="space-y-1">
                  {rows.map(([k, label, fmt]) => (
                    <div key={k} className="flex items-baseline justify-between gap-2 text-xs">
                      <dt className="text-muted-foreground truncate">{label}</dt>
                      <dd className="font-mono tabular-nums">{fmt(data.metrics![k])}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            );
          })}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {data.recommendation && <RecommendationBar rec={data.recommendation} />}
        {(data.earnings?.length ?? 0) > 0 && <EarningsHistory rows={data.earnings!.slice(0, 6)} />}
      </div>

      {(data.insider_flow || (data.peers?.length ?? 0) > 0) && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 pt-1 border-t border-border/50">
          {data.insider_flow && data.insider_flow.n > 0 && (
            <div className="text-xs">
              <span className="text-muted-foreground">Insider flow (recent): </span>
              <span
                className={cn(
                  'font-mono font-semibold',
                  data.insider_flow.net_shares >= 0 ? 'text-emerald-500' : 'text-rose-500',
                )}
              >
                {data.insider_flow.net_shares >= 0 ? '+' : ''}
                {data.insider_flow.net_shares.toLocaleString()} sh
              </span>
              <span className="text-muted-foreground">
                {' '}· {data.insider_flow.buys} buys / {data.insider_flow.sells} sells
              </span>
            </div>
          )}
          {(data.peers?.length ?? 0) > 0 && (
            <div className="flex items-center gap-1.5 text-xs">
              <span className="text-muted-foreground">Peers:</span>
              <div className="flex flex-wrap gap-1">
                {data.peers!.map((p) => (
                  <span key={p} className="font-mono text-[10px] border border-border rounded px-1.5 py-0.5">
                    {p}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function RecommendationBar({ rec }: { rec: FinnhubRecommendation }) {
  const segs = [
    { label: 'Strong buy', n: rec.strong_buy, cls: 'bg-emerald-600' },
    { label: 'Buy', n: rec.buy, cls: 'bg-emerald-400' },
    { label: 'Hold', n: rec.hold, cls: 'bg-amber-400' },
    { label: 'Sell', n: rec.sell, cls: 'bg-rose-400' },
    { label: 'Strong sell', n: rec.strong_sell, cls: 'bg-rose-600' },
  ];
  const total = segs.reduce((s, x) => s + x.n, 0);
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1.5">
        Analyst consensus
        <span className="ml-1 normal-case opacity-70">({total} analysts · {rec.period})</span>
      </div>
      <div className="flex h-3 rounded overflow-hidden">
        {segs.map((s) =>
          s.n > 0 ? (
            <div
              key={s.label}
              className={s.cls}
              style={{ width: `${(s.n / total) * 100}%` }}
              title={`${s.label}: ${s.n}`}
            />
          ) : null,
        )}
      </div>
      <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
        <span className="text-emerald-500">{rec.strong_buy + rec.buy} buy</span>
        <span className="text-amber-500">{rec.hold} hold</span>
        <span className="text-rose-500">{rec.sell + rec.strong_sell} sell</span>
      </div>
    </div>
  );
}

function EarningsHistory({ rows }: { rows: FinnhubEarnings[] }) {
  // rows arrive most-recent-first; show oldest→newest left to right.
  const ordered = [...rows].reverse();
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1.5">
        EPS surprise history
      </div>
      <div className="flex items-end gap-1.5">
        {ordered.map((e) => {
          const surprise = e.surprise_pct ?? 0;
          const mag = Math.min(100, Math.abs(surprise) * 6 + 12);
          return (
            <div key={e.period} className="flex-1 flex flex-col items-center gap-1" title={
              `${e.period}: actual ${e.actual ?? '—'} vs est ${e.estimate ?? '—'} (${surprise >= 0 ? '+' : ''}${surprise.toFixed(1)}%)`
            }>
              <div
                className={cn('w-full rounded-sm', e.beat ? 'bg-emerald-500/70' : 'bg-rose-500/70')}
                style={{ height: `${mag}%`, minHeight: 6, maxHeight: 44 }}
              />
              <span className="text-[8px] text-muted-foreground font-mono">
                {e.period.slice(2, 7)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
