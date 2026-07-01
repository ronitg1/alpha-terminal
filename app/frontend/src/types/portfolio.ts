// Wire-format mirror of GET /portfolio/overview (app/backend/routes/portfolio.py).

export interface PortfolioPosition {
  readonly symbol: string;
  readonly underlying: string;
  readonly name: string | null;
  readonly kind: 'stock' | 'option';
  readonly quantity: number | null;
  readonly last_price: number | null;
  readonly day_change: number | null;
  readonly day_change_pct: number | null;
  readonly current_value: number | null;
  readonly pct_of_account: number | null;
  readonly avg_cost: number | null;
  readonly cost_basis_total: number | null;
  readonly total_gain: number | null;
  readonly total_gain_pct: number | null;
  readonly week52_low: number | null;
  readonly week52_high: number | null;
  readonly option_type: string | null;
  readonly strike: number | null;
  readonly expiration: string | null;
}

export interface PortfolioAccount {
  readonly id: string;
  readonly label: string;
  readonly source: 'snaptrade' | 'robinhood' | 'combined';
  readonly institution: string | null;
  readonly cash: number | null;
  readonly total_value: number | null;
  readonly day_change: number | null;
  readonly day_change_pct: number | null;
  readonly total_gain: number | null;
  readonly total_gain_pct: number | null;
  readonly positions: readonly PortfolioPosition[];
}

export interface PortfolioOverview {
  readonly connected: boolean;
  readonly sources: readonly string[];
  readonly accounts: readonly PortfolioAccount[];
  readonly combined: PortfolioAccount | null;
}
