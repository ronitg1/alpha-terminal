/**
 * Wire-format types for the /patterns/* backend endpoints.
 * Keep in sync with app/backend/routes/patterns.py Pydantic models.
 */

/** Bar size for scans/charts. Daily bars carry YYYY-MM-DD dates; intraday
 *  bars carry YYYY-MM-DDTHH:MM in US-Eastern wall-clock time. */
export type PatternTimeframe = 'day' | '1h' | '15m';

export interface CandleBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  vwap?: number;
}

export interface Trendline {
  time_start: string;
  time_end: string;
  value_start: number;
  value_end: number;
  label: string;
}

export interface ScanResult {
  ticker: string;
  pattern: string;
  start_date: string;
  end_date: string;
  confidence: number;
  description: string;
  key_levels: Record<string, number>;
  bullish: boolean;
  trendlines?: Trendline[];
}

export interface PatternDetection extends ScanResult {
  trendlines: Trendline[];
}

export interface ChartData {
  ticker: string;
  candles: CandleBar[];
  patterns: PatternDetection[];
}

export interface HistoricalStats {
  total_signals: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  avg_win_pct: number | null;
  avg_loss_pct: number | null;
  outcome_bars: number;
  win_threshold_pct: number;
}

export interface OptionsStrategy {
  name: string;
  grade: string;
  structure: string;
  rationale: string;
  risk_reward: string;
  ideal_iv_rank: string;
}

export interface SignalAnalysisData {
  ticker: string;
  pattern: string;
  bullish: boolean;
  current_price: number;
  historical: HistoricalStats;
  options: OptionsStrategy[];
}

export interface PatternsListResponse {
  patterns: string[];
  bullish: string[];
}

export type RiskTolerance = 'conservative' | 'moderate' | 'aggressive';

export interface TradePlan {
  /** Actionability of the latest detection: 'live' = tradeable now,
   *  'watch' = valid setup but the trigger is far from price,
   *  'stale' = played out / invalidated / too old — rescan. */
  status?: 'live' | 'watch' | 'stale';
  status_reason?: string;
  direction: 'long' | 'short';
  risk: RiskTolerance;
  atr_multiple: number;
  entry: number;
  entry_basis: string;
  already_triggered: boolean;
  stop: number;
  stop_pct: number;
  stop_basis: string;
  structural_invalidation: number | null;
  target: number;
  target_pct: number;
  target_basis: string;
  risk_per_share: number;
  reward_per_share: number;
  risk_reward: number | null;
}

/** Premium-space plan for the play's contract (ATM call/put at the breakout). */
export interface OptionTradePlan {
  /** False when the repriced target doesn't exceed entry — theta outruns the
   *  measured move and the contract loses even if the pattern works. */
  viable: boolean;
  contract_ticker: string | null;
  type: 'call' | 'put';
  strike: number;
  expiration: string | null;
  dte: number;
  iv_pct: number | null;
  delta: number | null;
  current_mid: number;
  entry_premium: number;
  stop_premium: number;
  target_premium: number;
  risk_per_contract: number;
  reward_per_contract: number;
  max_loss_per_contract: number;
  risk_reward: number | null;
  pricing_basis: string;
}

export interface TradePlanResponse {
  ticker: string;
  pattern: string;
  bullish: boolean;
  current_price: number;
  atr: number | null;
  atr_pct: number | null;
  hist_vol_annual_pct: number | null;
  timeframe: string;
  signal_date: string | null;
  confidence?: number;
  plan: TradePlan | null;
  option: OptionTradePlan | null;
}
