/**
 * Type definitions for the Sleeves Dashboard.
 *
 * These mirror the response shape of `app/backend/routes/sleeves.py`.
 * Keep this file in sync with the backend — both are the wire format
 * the dashboard depends on.
 */

export type Signal = 'bullish' | 'bearish' | 'neutral';
export type Highlight = 'green' | 'red' | 'yellow' | 'neutral';

export interface PerAgentVerdict {
  agent: string;
  signal: Signal;
  confidence: number; // 0-100
  /** Full agent output dict (variant_perception, catalysts, kill_switch,
   *  ira_credit_stack, feoc_risk, s_curve_position, etc). Present on rows
   *  from live scans; empty {} on rows hydrated from CSV (Phase 1 history). */
  raw?: Record<string, unknown>;
}

export interface TickerRow {
  ticker: string;
  sleeve: string;
  consensus: Signal;
  weighted_score: number; // roughly [-100, +100]
  avg_confidence: number; // 0-100
  highlight: Highlight;
  position_type: string;
  hold_period: string;
  has_variant_perception: boolean;
  variant_perception: string;
  per_agent: PerAgentVerdict[];
}

export interface ScanSummary {
  date: string;
  path: string;
  row_count: number;
  rows: TickerRow[];
}

export interface SleeveConfig {
  name: string;
  allocation_pct: number;
  agents: string[];
  agent_weights: Record<string, number>;
  tickers: string[];
}

export interface SleevesConfig {
  sleeves: SleeveConfig[];
  cash_reserve_pct: number;
}

export interface ScanListItem {
  date: string;
  path: string;
  size_bytes: number;
}

export interface WatchlistEntry {
  ticker: string;
  comment: string;
}

export interface AnalystMetadata {
  key: string;
  display_name: string;
  description: string;
  investing_style: string;
  order: number;
}

// ─── Thesis synthesis payloads ──────────────────────────────────────────────

/** LLM-synthesized thesis at portfolio or sleeve scope. */
export interface Thesis {
  condensed: string;
  full: string;
  bias: 'bullish' | 'bearish' | 'mixed' | 'neutral';
  top_long?: string | null;
  top_short?: string | null;
  generated_at: string;
  scope: string;
  scan_date: string;
}


// ─── Per-ticker drill drawer payload ─────────────────────────────────────────
// Backed by GET /sleeves/ticker/{ticker}. Each section may be empty/null if
// the underlying provider call failed — the drawer renders around the gap.

export interface PriceBar {
  time: string; // YYYY-MM-DD
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
}

/** Latest TTM financial metrics. Mirrors src.data.models.FinancialMetrics —
 *  many fields are nullable since not every provider returns every ratio. */
export interface Fundamentals {
  ticker: string;
  report_period: string;
  period: string;
  currency: string;
  market_cap: number | null;
  enterprise_value: number | null;
  price_to_earnings_ratio: number | null;
  price_to_book_ratio: number | null;
  price_to_sales_ratio: number | null;
  enterprise_value_to_ebitda_ratio: number | null;
  enterprise_value_to_revenue_ratio: number | null;
  free_cash_flow_yield: number | null;
  peg_ratio: number | null;
  gross_margin: number | null;
  operating_margin: number | null;
  net_margin: number | null;
  return_on_equity: number | null;
  return_on_assets: number | null;
  revenue_growth: number | null;
  earnings_growth: number | null;
  debt_to_equity: number | null;
  current_ratio: number | null;
  earnings_per_share: number | null;
  // The backend may include other fields; treat anything we don't read here
  // as opaque.
  [key: string]: unknown;
}

export interface NewsItem {
  ticker: string;
  title: string;
  author: string | null;
  source: string;
  date: string; // ISO 8601 from Polygon (published_utc)
  url: string;
  sentiment: string | null;
}

/** Reference data from Polygon /v3/reference/tickers/{ticker}. Used to
 *  render a one-paragraph "what does this company do" overview. */
export interface TickerDetails {
  name?: string | null;
  description?: string | null;
  sic_description?: string | null;
  homepage_url?: string | null;
  primary_exchange?: string | null;
  list_date?: string | null;
  total_employees?: number | null;
  share_class_shares_outstanding?: number | null;
  /** Polygon publishes this for most tickers — used as a fallback when
   *  FDS fundamentals don't include market_cap (e.g. foreign / small caps). */
  market_cap?: number | null;
  currency_name?: string | null;
}

export interface TickerData {
  ticker: string;
  price_history: PriceBar[];
  fundamentals: Fundamentals | null;
  recent_news: NewsItem[];
  details?: TickerDetails | null;
}

// ─── Options screener + chain (Phase E) ──────────────────────────────────────
// Backed by GET /sleeves/options/screener and /sleeves/options/chain/{ticker}.

/** Single signal chip — one of N rules within a strategy. */
export interface ScreenerSignal {
  /** Short label rendered on the chip, e.g. "20d vs QQQ" or "RSI extreme". */
  label: string;
  /** Pre-formatted current value, e.g. "−14.8%" or "1.45". */
  value_text: string;
  /** Whether the rule trips at the current value. */
  fired: boolean;
  /** Plain-English explanation, surfaced in a hover tooltip. */
  tooltip: string;
}

/** Per-strategy contract recommendation. Tells the chain viewer which leg
 *  to highlight and why this is the recommended trade for this setup. */
export interface ScreenerRecommendation {
  direction: 'call' | 'put';
  /** 0 = ATM, positive = OTM call strike, negative = OTM put strike. */
  strike_offset_pct: number;
  /** Hint to the user about which expiry tier to look at. */
  expiry_lean: 'near' | 'mid' | 'far';
  /** Plain-English explanation of why this contract is the pick. */
  reasoning: string;
}

export interface ScreenerCandidate {
  ticker: string;
  /** 0..N where N = signals.length (typically 3). */
  conviction: number;
  signals: ScreenerSignal[];
  last_price: number | null;
  /** Sort tiebreaker — lower ranks earlier within same conviction. */
  sort_key: number;
  recommendation: ScreenerRecommendation;
}

/** Strategy descriptor returned by GET /sleeves/options/strategies. */
export interface OptionsStrategyMeta {
  key: string;
  label: string;
  subtitle: string;
  description: string;
}

/** Open type — strategies are registered server-side. Frontend hits
 *  /sleeves/options/strategies to discover them. */
export type OptionsStrategy = string;

export interface OptionsScreenerResponse {
  sleeve: string;
  strategy: OptionsStrategy;
  benchmark: string; // 'QQQ'
  generated_at: string; // ISO
  candidates: ScreenerCandidate[];
}

export interface OptionContract {
  type: 'call' | 'put';
  ticker: string | null; // Polygon contract ticker, e.g. O:MSFT260606C00470000
  strike: number;
  expiration: string; // YYYY-MM-DD
  bid: number | null;
  ask: number | null;
  last: number | null;
  iv: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  volume: number | null;
  open_interest: number | null;
}

export interface OptionsChainResponse {
  ticker: string;
  spot: number;
  expiration: string | null;
  /** Every expiry the backend pulled in the requested horizon. Frontend
   *  uses this to populate the expiry dropdown. */
  available_expirations: string[];
  atm_window_pct: number;
  horizon_days: number;
  strike_low: number;
  strike_high: number;
  calls: OptionContract[];
  puts: OptionContract[];
  generated_at: string;
}

// ─── Backtests (Phase D) ────────────────────────────────────────────────────

export interface OptionsBacktestRequest {
  start_date: string;
  end_date: string;
  sleeve?: string;
  tickers?: string[] | null;
  strategy?: string;
  conviction_min?: number;
  direction?: 'auto' | 'straddle' | 'calls' | 'puts';
  hold_days?: number;
}

export interface OptionsBacktestTrade {
  ticker: string;
  strategy: string;
  direction: string;
  open_date: string;
  close_date: string;
  conviction: number;
  strike: number;
  sigma: number;
  entry_spot: number;
  exit_spot: number;
  entry_premium: number;
  exit_premium: number;
  pnl: number;
  return_pct: number;
  /** True if this trade was priced via BSM proxy (either because pricing='bsm'
   *  or because real-fill lookup failed and fell back). */
  synthetic?: boolean;
  /** Polygon contract symbol(s) used when pricing='real'. Null on synthetic trades. */
  contract_ticker?: string | null;
  contract_expiry?: string | null;
  /** True if the trade exited early because the per-contract drawdown stop
   *  fired. Pairs with exit_reason='stop'. */
  stopped_out?: boolean;
  exit_reason?: 'time' | 'stop';
}

export interface OptionsBacktestSummary {
  n_trades: number;
  n_wins: number;
  win_rate: number;
  total_pnl_per_share: number;
  avg_return_pct: number;
  by_conviction: Record<
    string,
    { n_trades: number; win_rate: number; avg_return_pct: number; total_pnl: number }
  >;
  trades: OptionsBacktestTrade[];
  /** Echo of the requested pricing mode for display ('real' | 'bsm'). */
  pricing?: 'real' | 'bsm';
  /** Number of trades that fell back to BSM (only meaningful when pricing='real'). */
  n_synthetic?: number;
  /** Echo of the configured stop-loss threshold (positive fraction; null = off). */
  stop_loss_pct?: number | null;
  /** Count of trades exited early by the stop-loss rule. */
  n_stopped?: number;
  /** Average return % across stopped-out trades only. Negative when the stop is doing its job. */
  avg_return_when_stopped?: number | null;
}

/** One closed-trade entry surfaced by the sleeves backtest. Built by the
 *  backend from BacktestService day-results via extract_trades_from_day_results. */
export interface SleevesBacktestTrade {
  ticker: string;
  sleeve: string;
  agent: string;
  open_date: string;
  close_date: string;
  side: string;
  hold_days: number;
  pnl: number;
  entry_value: number;
  return_pct: number;
}

export interface SleevesBacktestSummaryHeader {
  initial_capital: number;
  final_value: number;
  total_return_pct: number;
  n_days_simulated: number;
  n_trades: number;
  missing_tickers: string[];
}

export interface SleevesBacktestRequest {
  start_date: string;
  end_date: string;
  sleeves?: string[];
  tickers?: string[];
  initial_capital?: number;
  margin_requirement?: number;
  model_name?: string;
  model_provider?: string;
}

export interface BacktestDayResult {
  date: string;
  portfolio_value: number;
  cash: number;
  decisions: Record<string, { action?: string; quantity?: number }>;
  executed_trades: Record<string, number>;
  analyst_signals?: Record<string, Record<string, { signal?: string; confidence?: number }>>;
  current_prices: Record<string, number>;
  long_exposure: number;
  short_exposure: number;
  gross_exposure: number;
  net_exposure: number;
  long_short_ratio?: number | null;
  portfolio_return?: number;
}

export interface BacktestAttributionSleeve {
  n_trades: number;
  win_rate: number;
  avg_hold_days: number;
  total_pnl: number;
  sharpe: number | null;
  max_drawdown: number;
}

export interface BacktestAttributionAgent {
  n_trades: number;
  win_rate: number;
  total_pnl_attributed: number;
  avg_return_pct: number;
}

export interface BacktestAttribution {
  n_trades: number;
  sleeves: Record<string, BacktestAttributionSleeve>;
  agents: Record<string, BacktestAttributionAgent>;
  warnings: string[];
}

export interface SleevesBacktestSummary {
  /** Headline numbers — total return, days simulated, etc. Populated by
   *  the backend so the frontend doesn't need to recompute. */
  summary?: SleevesBacktestSummaryHeader;
  performance_metrics: {
    sharpe_ratio?: number | null;
    sortino_ratio?: number | null;
    max_drawdown?: number | null;
    max_drawdown_date?: string | null;
    long_short_ratio?: number | null;
    gross_exposure?: number | null;
    net_exposure?: number | null;
  };
  final_portfolio: Record<string, unknown>;
  results: BacktestDayResult[];
  /** Per-trade entry/exit/P&L. */
  trades?: SleevesBacktestTrade[];
  attribution: BacktestAttribution;
}
