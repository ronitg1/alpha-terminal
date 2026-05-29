/**
 * FundamentalsCard — KV grid of the most-commonly-cited TTM ratios.
 *
 * Shape mirrors `<KVChip>` in ticker-drill-drawer.tsx (kept inline rather
 * than extracted to avoid an avoidable shared module — they may diverge
 * later, and this card has its own formatting rules for currency / pct).
 *
 * Renders a 4-column grid on >=sm, 2-column on mobile. Missing fields are
 * shown as "—" so the grid stays rectangular and the reader can tell
 * what's absent from what's actually unknown.
 */

import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { Fundamentals } from '@/types/sleeves';

interface FundamentalsCardProps {
  fundamentals: Fundamentals | null;
  loading?: boolean;
  className?: string;
}

export function FundamentalsCard({ fundamentals, loading, className }: FundamentalsCardProps) {
  if (loading) {
    return (
      <div className={cn('grid grid-cols-2 sm:grid-cols-4 gap-2', className)}>
        {Array.from({ length: 8 }).map((_, i) => (
          <SkeletonChip key={i} />
        ))}
      </div>
    );
  }

  if (!fundamentals) {
    return (
      <div
        className={cn(
          'text-xs text-muted-foreground italic px-2 py-3 rounded border border-dashed',
          className
        )}
      >
        Fundamentals unavailable.
      </div>
    );
  }

  const f = fundamentals;
  const items: { label: string; value: string }[] = [
    { label: 'Market cap', value: formatCurrency(f.market_cap) },
    { label: 'P/E', value: formatRatio(f.price_to_earnings_ratio) },
    { label: 'P/B', value: formatRatio(f.price_to_book_ratio) },
    { label: 'EV/EBITDA', value: formatRatio(f.enterprise_value_to_ebitda_ratio) },
    { label: 'Op margin', value: formatPct(f.operating_margin) },
    { label: 'Net margin', value: formatPct(f.net_margin) },
    { label: 'Revenue growth', value: formatPct(f.revenue_growth) },
    { label: 'FCF yield', value: formatPct(f.free_cash_flow_yield) },
  ];

  return (
    <div className={cn('space-y-2', className)}>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {items.map((item) => (
          <KV key={item.label} label={item.label} value={item.value} />
        ))}
      </div>
      <div className="text-[10px] text-muted-foreground">
        TTM · period {f.report_period || '—'}
      </div>
    </div>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">
        {label}
      </div>
      <Badge variant="outline" className="font-mono text-[11px] w-full justify-start">
        {value}
      </Badge>
    </div>
  );
}

function SkeletonChip() {
  return (
    <div>
      <div className="h-2 w-12 mb-1 rounded bg-muted-foreground/20 animate-pulse" />
      <div className="h-5 w-full rounded bg-muted-foreground/10 animate-pulse" />
    </div>
  );
}

// ─── formatters ──────────────────────────────────────────────────────────────

function formatCurrency(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  // Tn / Bn / Mn / Kn for compactness in a small KV chip.
  const abs = Math.abs(n);
  if (abs >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `$${(n / 1e3).toFixed(2)}K`;
  return `$${n.toFixed(2)}`;
}

function formatRatio(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  return n.toFixed(2);
}

function formatPct(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  // Backend stores fractional rates (0.123 = 12.3%) — keep the convention.
  return `${(n * 100).toFixed(1)}%`;
}
