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
