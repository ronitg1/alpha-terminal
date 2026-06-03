/**
 * FinnhubSnapshot — compact "what I need to know" stat strip for the Portfolio
 * Pulse deep dive. Pulls the same /sleeves/ticker/{ticker}/finnhub data as the
 * Market tab's full panel, but renders just the headline numbers: analyst
 * consensus, last earnings beat/miss, growth, valuation, and insider flow.
 *
 * Renders nothing when Finnhub isn't configured or has no data, so the deep
 * dive degrades cleanly.
 */

import { sleevesApi } from '@/services/sleeves-api';
import { FinnhubFundamentals } from '@/types/sleeves';
import { cn } from '@/lib/utils';
import { useEffect, useState } from 'react';

function pct(v: number | undefined): string {
  return v == null ? '—' : `${v.toFixed(1)}%`;
}

export function FinnhubSnapshot({ ticker }: { ticker: string }) {
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

  if (loading) {
    return <div className="h-12 rounded-md bg-muted-foreground/5 animate-pulse" />;
  }
  if (!data || !data.configured) return null;

  const m = data.metrics ?? {};
  const rec = data.recommendation;
  const earnings = data.earnings ?? [];
  const beats = earnings.filter((e) => e.beat).length;
  const lastEarn = earnings[0];
  const flow = data.insider_flow;

  const buy = rec ? rec.strong_buy + rec.buy : 0;
  const hold = rec ? rec.hold : 0;
  const sell = rec ? rec.sell + rec.strong_sell : 0;
  const recTotal = buy + hold + sell;

  return (
    <div className="rounded-md border border-border/60 bg-muted/10 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        {recTotal > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Analysts</span>
            <div className="flex h-2 w-20 rounded overflow-hidden">
              {buy > 0 && <div className="bg-emerald-500" style={{ width: `${(buy / recTotal) * 100}%` }} />}
              {hold > 0 && <div className="bg-amber-400" style={{ width: `${(hold / recTotal) * 100}%` }} />}
              {sell > 0 && <div className="bg-rose-500" style={{ width: `${(sell / recTotal) * 100}%` }} />}
            </div>
            <span className="text-[10px] font-mono text-muted-foreground">
              {buy}/{hold}/{sell}
            </span>
          </div>
        )}

        {earnings.length > 0 && (
          <Stat
            label="Earnings"
            value={`${beats}/${earnings.length} beat`}
            sub={lastEarn?.surprise_pct != null ? `${lastEarn.surprise_pct >= 0 ? '+' : ''}${lastEarn.surprise_pct.toFixed(1)}% last` : undefined}
            tone={beats > earnings.length / 2 ? 'pos' : 'neg'}
          />
        )}
        {m.revenue_growth_ttm != null && (
          <Stat label="Rev growth" value={pct(m.revenue_growth_ttm)} tone={m.revenue_growth_ttm >= 0 ? 'pos' : 'neg'} />
        )}
        {m.eps_growth_ttm != null && (
          <Stat label="EPS growth" value={pct(m.eps_growth_ttm)} tone={m.eps_growth_ttm >= 0 ? 'pos' : 'neg'} />
        )}
        {m.net_margin_ttm != null && <Stat label="Net margin" value={pct(m.net_margin_ttm)} />}
        {m.pe_ttm != null && <Stat label="P/E" value={m.pe_ttm.toFixed(1)} />}
        {flow && flow.n > 0 && (
          <Stat
            label="Insider"
            value={`${flow.net_shares >= 0 ? '+' : ''}${(flow.net_shares / 1000).toFixed(0)}k sh`}
            tone={flow.net_shares >= 0 ? 'pos' : 'neg'}
          />
        )}
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: 'pos' | 'neg';
}) {
  return (
    <div className="flex flex-col">
      <span className="text-[9px] uppercase tracking-wide text-muted-foreground leading-none">{label}</span>
      <span
        className={cn(
          'text-xs font-mono font-semibold tabular-nums leading-tight mt-0.5',
          tone === 'pos' && 'text-emerald-500',
          tone === 'neg' && 'text-rose-500',
        )}
      >
        {value}
        {sub && <span className="text-[9px] text-muted-foreground font-normal ml-1">{sub}</span>}
      </span>
    </div>
  );
}
