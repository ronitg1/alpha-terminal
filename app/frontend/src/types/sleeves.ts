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
