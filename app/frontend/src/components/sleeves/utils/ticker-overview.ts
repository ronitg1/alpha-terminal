/**
 * Helpers for rendering a concise company overview from Polygon's
 * /v3/reference/tickers/{ticker} payload (exposed by the backend as
 * `details` on TickerData).
 */
import type { Fundamentals, TickerDetails } from '@/types/sleeves';

/** Return the first N sentences of a description. Naive sentence split on
 *  `. ! ?` — good enough for SEC-style filings, which is what Polygon
 *  serves up. Falls back to a hard 240-char cut if no terminator found. */
export function firstSentences(text: string, n = 2): string {
  if (!text) return '';
  const sentences = text
    .replace(/\s+/g, ' ')
    .trim()
    .match(/[^.!?]+[.!?]+(?:\s|$)/g);
  if (!sentences || sentences.length === 0) {
    return text.length > 240 ? text.slice(0, 240).trimEnd() + '…' : text;
  }
  return sentences
    .slice(0, n)
    .join(' ')
    .replace(/\s+$/, '');
}

export interface KeyFinancial {
  label: string;
  value: string;
  /** Optional accent for the value cell. */
  accent?: 'positive' | 'negative' | 'neutral';
  /** Optional secondary detail line under the value. */
  sub?: string;
}

/** Pick the most meaningful slice of fundamentals for a compact KPI grid.
 *  Always tries the Polygon ticker-details payload as a fallback for
 *  market_cap (the field FDS most commonly omits for small/foreign caps). */
export function pickKeyFinancials(
  fundamentals: Fundamentals | null,
  details: TickerDetails | null | undefined,
): KeyFinancial[] {
  const out: KeyFinancial[] = [];
  const f = fundamentals;

  // Market cap: prefer FDS (full TTM context), fall back to Polygon ref.
  const mcap = f?.market_cap ?? details?.market_cap ?? null;
  if (mcap != null) {
    out.push({
      label: 'Market cap',
      value: formatMoney(mcap),
      sub:
        details?.currency_name &&
        details.currency_name.toLowerCase() !== 'usd'
          ? details.currency_name.toUpperCase()
          : undefined,
    });
  }
  if (!f) {
    // Without FDS fundamentals we can still surface a couple of fields from
    // the Polygon reference payload — better than rendering nothing.
    if (details?.share_class_shares_outstanding != null) {
      out.push({
        label: 'Shares out',
        value: formatCount(details.share_class_shares_outstanding),
      });
    }
    if (details?.total_employees != null) {
      out.push({
        label: 'Employees',
        value: formatCount(details.total_employees),
      });
    }
    if (details?.list_date) {
      out.push({ label: 'Listed', value: details.list_date });
    }
    if (details?.primary_exchange) {
      out.push({ label: 'Exchange', value: details.primary_exchange });
    }
    return out;
  }
  if (f.price_to_earnings_ratio != null) {
    out.push({
      label: 'P/E',
      value: formatRatio(f.price_to_earnings_ratio),
    });
  }
  if (f.price_to_sales_ratio != null) {
    out.push({ label: 'P/S', value: formatRatio(f.price_to_sales_ratio) });
  }
  if (f.enterprise_value_to_ebitda_ratio != null) {
    out.push({
      label: 'EV/EBITDA',
      value: formatRatio(f.enterprise_value_to_ebitda_ratio),
    });
  }
  if (f.gross_margin != null) {
    out.push({
      label: 'Gross margin',
      value: formatPct(f.gross_margin),
      accent: f.gross_margin > 0.3 ? 'positive' : undefined,
    });
  }
  if (f.operating_margin != null) {
    out.push({
      label: 'Op margin',
      value: formatPct(f.operating_margin),
      accent:
        f.operating_margin > 0
          ? f.operating_margin > 0.15
            ? 'positive'
            : 'neutral'
          : 'negative',
    });
  }
  if (f.net_margin != null) {
    out.push({
      label: 'Net margin',
      value: formatPct(f.net_margin),
      accent: f.net_margin > 0 ? 'positive' : 'negative',
    });
  }
  if (f.return_on_equity != null) {
    out.push({ label: 'ROE', value: formatPct(f.return_on_equity) });
  }
  if (f.revenue_growth != null) {
    out.push({
      label: 'Rev growth (YoY)',
      value: formatPct(f.revenue_growth),
      accent: f.revenue_growth > 0 ? 'positive' : 'negative',
    });
  }
  if (f.free_cash_flow_yield != null) {
    out.push({
      label: 'FCF yield',
      value: formatPct(f.free_cash_flow_yield),
    });
  }
  if (f.debt_to_equity != null) {
    out.push({
      label: 'D/E',
      value: formatRatio(f.debt_to_equity),
      accent: f.debt_to_equity < 1 ? 'positive' : 'negative',
    });
  }
  if (f.current_ratio != null) {
    out.push({ label: 'Current ratio', value: formatRatio(f.current_ratio) });
  }
  if (details?.total_employees != null) {
    out.push({
      label: 'Employees',
      value: formatCount(details.total_employees),
    });
  }
  return out;
}

function formatMoney(n: number): string {
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  return `$${n.toFixed(0)}`;
}

function formatRatio(n: number): string {
  return n.toFixed(2);
}

function formatPct(n: number): string {
  return `${(n * 100).toFixed(1)}%`;
}

function formatCount(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return `${Math.round(n)}`;
}
