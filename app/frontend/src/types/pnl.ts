/**
 * Wire-format types for the /pnl/* backend endpoints.
 * Keep in sync with app/backend/routes/pnl.py + services/pnl_service.py.
 */

export type PositionKind = 'option' | 'stock';
export type PositionSide = 'long' | 'short';
export type PositionStatus = 'open' | 'closed';
export type PositionSource = 'manual' | 'screener' | 'pattern' | 'fidelity';

export interface OptionLeg {
  type: 'call' | 'put';
  strike: number;
  expiration: string; // YYYY-MM-DD
  contract_ticker?: string | null;
}

export interface PnlPosition {
  id: string;
  kind: PositionKind;
  ticker: string;
  side: PositionSide;
  qty: number;
  option: OptionLeg | null;
  /** Per share — options carry the 100x multiplier in math, not in price. */
  entry_price: number;
  entry_date: string | null;
  status: PositionStatus;
  exit_price: number | null;
  exit_date: string | null;
  source: PositionSource;
  /** true = actual fill (e.g. Fidelity import), false = paper idea. */
  real: boolean;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface PnlMark {
  mark: number | null;
  source: 'mid_quote' | 'last_trade' | 'day_close' | 'contract_close' | 'last_close' | 'unavailable';
}

export interface PnlSummary {
  n_open: number;
  n_closed: number;
  realized_total: number;
  unrealized_total: number;
  n_wins: number;
  n_losses: number;
  win_rate: number | null;
  by_underlying: Record<string, { realized: number; unrealized: number }>;
  equity_curve: { date: string; cum_realized: number }[];
  marks: Record<string, PnlMark>;
  asof: string;
}

export interface PositionCreatePayload {
  kind: PositionKind;
  ticker: string;
  side?: PositionSide;
  qty: number;
  option?: OptionLeg | null;
  entry_price: number;
  entry_date?: string | null;
  source?: PositionSource;
  real?: boolean;
  notes?: string;
}
