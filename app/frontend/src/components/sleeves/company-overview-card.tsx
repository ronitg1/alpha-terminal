/**
 * CompanyOverviewCard — two-sentence "what does this company do" + key
 * financials grid.
 *
 * Used by both the Sleeves-tab TickerExpansion and the My-Stocks StockCard.
 * Data comes from /sleeves/ticker/{ticker} — the same fetch the parent
 * already triggers, so this component just consumes from useTickerData.
 */
import { cn } from '@/lib/utils';
import type { TickerData } from '@/types/sleeves';
import { Building2, ExternalLink } from 'lucide-react';
import {
  firstSentences,
  pickKeyFinancials,
} from './utils/ticker-overview';

interface CompanyOverviewCardProps {
  data: TickerData | null;
  loading: boolean;
  ticker: string;
  /** Hide if there's no description AND no fundamentals — caller can
   *  decide whether to render a placeholder or nothing. */
  hideWhenEmpty?: boolean;
}

export function CompanyOverviewCard({
  data,
  loading,
  ticker,
  hideWhenEmpty,
}: CompanyOverviewCardProps) {
  const details = data?.details ?? null;
  const fundamentals = data?.fundamentals ?? null;
  const description = details?.description ?? '';
  const overview = firstSentences(description, 2);
  const kpis = pickKeyFinancials(fundamentals, details);

  if (loading) {
    return (
      <div className="rounded-md border border-border bg-card/30 p-3 space-y-2">
        <div className="h-3 w-1/3 bg-muted-foreground/10 rounded animate-pulse" />
        <div className="h-2 w-full bg-muted-foreground/10 rounded animate-pulse" />
        <div className="h-2 w-5/6 bg-muted-foreground/10 rounded animate-pulse" />
      </div>
    );
  }

  if (!overview && kpis.length === 0) {
    if (hideWhenEmpty) return null;
    return (
      <div className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground italic">
        No company overview or fundamentals available for {ticker}. This usually
        means the ticker is outside the FDS coverage (small / foreign listings)
        and Polygon's reference endpoint returned no data either.
      </div>
    );
  }

  const hasFundamentals = !!fundamentals;

  return (
    <div className="rounded-md border border-border bg-card/30 p-3 space-y-2.5">
      {/* Header: company name + industry */}
      {(details?.name || details?.sic_description) && (
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-sm font-semibold truncate">
              <Building2 className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
              {details?.name ?? ticker}
            </div>
            {details?.sic_description && (
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground mt-0.5">
                {details.sic_description}
                {details.primary_exchange && ` · ${details.primary_exchange}`}
              </div>
            )}
          </div>
          {details?.homepage_url && (
            <a
              href={details.homepage_url}
              target="_blank"
              rel="noreferrer"
              className="text-[10px] inline-flex items-center gap-0.5 text-muted-foreground hover:text-foreground"
              onClick={(e) => e.stopPropagation()}
              title="Open company website"
            >
              site
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
      )}

      {/* 2-sentence overview */}
      {overview && (
        <div>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">
            Overview
          </div>
          <p className="text-sm leading-relaxed">{overview}</p>
        </div>
      )}

      {/* Key financials grid — always rendered (even when empty) so the
          section is visibly present and the user knows it's a coverage
          gap, not a missing component. */}
      <div>
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
          Key financials {hasFundamentals ? '(TTM)' : '(reference)'}
        </div>
        {kpis.length > 0 ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-3 gap-y-1.5 text-xs">
            {kpis.map((k) => (
              <div key={k.label}>
                <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                  {k.label}
                </div>
                <div
                  className={cn(
                    'font-mono font-medium tabular-nums',
                    k.accent === 'positive' &&
                      'text-emerald-600 dark:text-emerald-400',
                    k.accent === 'negative' &&
                      'text-rose-600 dark:text-rose-400',
                  )}
                >
                  {k.value}
                </div>
                {k.sub && (
                  <div className="text-[10px] text-muted-foreground">
                    {k.sub}
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="text-xs text-muted-foreground italic">
            No fundamentals available for {ticker}. Outside FDS coverage and
            Polygon reference returned no market_cap either.
          </div>
        )}
        {!hasFundamentals && kpis.length > 0 && (
          <div className="text-[10px] text-muted-foreground italic mt-1.5">
            Ratios (P/E, margins, etc.) are not available — the ticker is
            outside FDS coverage. Values shown come from the Polygon
            reference endpoint only.
          </div>
        )}
      </div>
    </div>
  );
}
