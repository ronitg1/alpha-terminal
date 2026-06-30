import React, { useState } from 'react';
import { ConfidenceBadge } from './confidence-badge';
import { TradePlanCard } from './signal-analysis';
import type { PatternTimeframe, ScanResult, HistoricalStats, TradePlanResponse } from '@/types/patterns';
import type { ScreenerRecommendation } from '@/types/sleeves';
import { OptionChainViewer } from '@/components/sleeves/options/option-chain-viewer';

const BULLISH_PATTERNS = new Set([
  'Bullish Flag', 'Bull Pennant', 'Double Bottom', 'Inverse Head and Shoulders',
  'Ascending Triangle', 'Cup and Handle', 'Falling Wedge',
]);

const BEARISH_PATTERNS = new Set([
  'Head and Shoulders', 'Double Top', 'Descending Triangle', 'Rising Wedge', 'Bearish Flag',
]);

function rowAccent(pattern: string): string {
  if (BULLISH_PATTERNS.has(pattern)) return 'border-l-emerald-500 hover:bg-emerald-900/10';
  if (BEARISH_PATTERNS.has(pattern)) return 'border-l-red-500 hover:bg-red-900/10';
  return 'border-l-amber-500 hover:bg-amber-900/10';
}

function SortIcon({ active, asc }: { active: boolean; asc: boolean }) {
  return (
    <svg className={`inline ml-1 w-3 h-3 ${active ? 'text-indigo-400' : 'text-gray-600'}`} viewBox="0 0 24 24" fill="currentColor">
      {asc ? <path d="M12 4l8 16H4z" /> : <path d="M12 20L4 4h16z" />}
    </svg>
  );
}

// ─── Freshness helpers ───────────────────────────────────────────────────────
// end_date is the breakout bar: "YYYY-MM-DD" (daily) or "YYYY-MM-DDTHH:MM"
// (intraday). "Today's plays" are the freshest breakouts — we surface those
// first and bucket the rest by recency.

/** Calendar days between the signal's breakout date and today (0 = today). */
function daysAgo(endDate: string): number {
  const d = new Date(`${endDate.slice(0, 10)}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((today.getTime() - d.getTime()) / 86_400_000);
}

type Bucket = { key: string; label: string };

function bucketOf(days: number): Bucket {
  if (days <= 0) return { key: 'today', label: 'Today' };
  if (days === 1) return { key: 'yesterday', label: 'Yesterday' };
  if (days <= 4) return { key: 'days', label: 'Last few days' };
  if (days <= 10) return { key: 'week', label: 'This week' };
  if (days <= 31) return { key: 'month', label: 'This month' };
  return { key: 'earlier', label: 'Earlier' };
}

const RECENCY_OPTIONS: { key: string; label: string; maxDays: number }[] = [
  { key: 'all', label: 'All', maxDays: Infinity },
  { key: 'today', label: 'Today', maxDays: 0 },
  { key: '3d', label: '3d', maxDays: 4 },
  { key: '1w', label: '1w', maxDays: 10 },
  { key: '1m', label: '1mo', maxDays: 31 },
];

const MIN_CONF_OPTIONS = [0, 50, 70, 90];

function WinRateBadge({ stats }: { stats: HistoricalStats | undefined }) {
  if (!stats) return <span className="text-gray-700 text-xs font-mono">—</span>;
  if (stats.total_signals === 0 || stats.win_rate == null)
    return <span className="text-gray-600 text-xs">n/a</span>;

  const rate = stats.win_rate;
  const color = rate >= 60 ? 'text-emerald-400' : rate >= 45 ? 'text-amber-400' : 'text-red-400';
  const barColor = rate >= 60 ? 'bg-emerald-500' : rate >= 45 ? 'bg-amber-500' : 'bg-red-500';

  return (
    <div className="flex items-center gap-2">
      <span className={`text-xs font-bold font-mono ${color}`}>{rate}%</span>
      <div className="flex-1 h-1 bg-gray-800 rounded-full w-10 overflow-hidden">
        <div className={`h-full ${barColor} rounded-full`} style={{ width: `${rate}%` }} />
      </div>
      <span className="text-gray-600 text-xs">{stats.wins}W/{stats.losses}L</span>
    </div>
  );
}

// ─── Filter chips ────────────────────────────────────────────────────────────

function Chip({
  active, onClick, children, tone,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  tone?: 'bull' | 'bear';
}) {
  const activeCls =
    tone === 'bull' ? 'bg-emerald-600 text-white border-emerald-500'
    : tone === 'bear' ? 'bg-red-600 text-white border-red-500'
    : 'bg-indigo-600 text-white border-indigo-500';
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-2 py-0.5 text-[11px] rounded-md border transition-colors ${
        active ? activeCls : 'bg-gray-800 text-gray-400 border-gray-700 hover:text-gray-200 hover:border-gray-600'
      }`}
    >
      {children}
    </button>
  );
}

function ChipGroup({ label, children }: { label: string; children: React.ReactNode }) {
  // On phones each group takes the full width (max-md:w-full) so its chips wrap
  // within the screen instead of running off the right edge; on desktop the
  // group is content-width and the groups sit inline.
  return (
    <div className="flex flex-wrap items-center gap-1 max-md:w-full">
      <span className="text-[10px] uppercase tracking-wider text-gray-600 mr-0.5">{label}</span>
      {children}
    </div>
  );
}

/**
 * ContractPanel — the recommended play for a pattern hit. Shows the trade plan
 * (what the pattern implies for the move + take-profit / stop-loss on the
 * contract) via the shared TradePlanCard, then the full chain with that exact
 * recommended contract highlighted — so the inline panel matches the chart
 * modal's recommendation. Both derive from the same /trade-plan call, which
 * picks the best payoff-per-dollar option in the 0.40-0.50 delta / 25-30 DTE
 * band.
 */
function ContractPanel({
  ticker,
  pattern,
  timeframe,
  bullish,
  onClose,
}: {
  ticker: string;
  pattern: string;
  timeframe: PatternTimeframe;
  bullish: boolean;
  onClose: () => void;
}) {
  const [plan, setPlan] = useState<TradePlanResponse | null>(null);
  const opt = plan?.option ?? null;
  const direction: 'call' | 'put' = opt ? opt.type : bullish ? 'call' : 'put';
  // Pin the chain to the recommended contract when we have one; otherwise fall
  // back to an ATM lean at the band's mid-DTE so the chain still renders.
  const dte = opt ? opt.dte : 27;
  const lean: ScreenerRecommendation['expiry_lean'] = dte <= 14 ? 'near' : dte <= 28 ? 'mid' : 'far';
  const recommendation: ScreenerRecommendation = {
    direction,
    strike_offset_pct: 0,
    strike_abs: opt ? opt.strike : undefined,
    expiry_lean: lean,
    reasoning: opt
      ? `Recommended: $${opt.strike}${direction === 'call' ? 'C' : 'P'} · ${opt.dte}d${opt.delta != null ? ` · Δ ${opt.delta}` : ''} — best payoff-per-dollar in the 0.40–0.50Δ / 25–30 DTE band if the pattern reaches target.`
      : `${bullish ? 'Bullish' : 'Bearish'} pattern — an ATM ${direction} expresses the directional bet.`,
  };

  return (
    <div className="mx-4 mb-3 p-3 rounded-lg bg-indigo-900/20 border border-indigo-800/40 text-xs space-y-3">
      <div className="flex items-center justify-between">
        <span className="font-semibold text-indigo-300">
          Recommended {direction.toUpperCase()} · {ticker}
        </span>
        <button type="button" onClick={onClose} className="text-gray-500 hover:text-gray-300">✕</button>
      </div>
      {/* Implied move + contract take-profit / stop-loss */}
      <TradePlanCard ticker={ticker} pattern={pattern} timeframe={timeframe} onPlan={setPlan} />
      {/* Full chain, recommended contract highlighted */}
      <OptionChainViewer ticker={ticker} recommendation={recommendation} preferredDte={dte} />
    </div>
  );
}

type SortKey = 'ticker' | 'pattern' | 'end_date' | 'confidence' | 'win_rate';
/** 'fresh' = today's plays first, highest confidence within each day. */
type SortMode = SortKey | 'fresh';

const COLS: { key: SortKey | 'description' | 'contract'; label: string; noSort?: boolean }[] = [
  { key: 'ticker', label: 'Ticker' },
  { key: 'pattern', label: 'Pattern' },
  { key: 'end_date', label: 'Signal Date' },
  { key: 'confidence', label: 'Confidence' },
  { key: 'win_rate', label: 'Win Rate' },
  { key: 'description', label: 'Description', noSort: true },
  { key: 'contract', label: 'Contract', noSort: true },
];

interface ResultsTableProps {
  results: ScanResult[];
  onRowClick: (row: ScanResult) => void;
  winRates: Map<string, HistoricalStats>;
  timeframe: PatternTimeframe;
}

export function ResultsTable({ results, onRowClick, winRates, timeframe }: ResultsTableProps) {
  // Default: today's plays first, highest confidence within each day.
  const [sortMode, setSortMode] = useState<SortMode>('fresh');
  const [sortAsc, setSortAsc] = useState(false);
  const [recency, setRecency] = useState('all');
  const [minConf, setMinConf] = useState(0);
  const [direction, setDirection] = useState<'all' | 'bull' | 'bear'>('all');
  const [filterPattern, setFilterPattern] = useState('');
  const [filterTicker, setFilterTicker] = useState('');
  const [contractRow, setContractRow] = useState<string | null>(null); // "ticker:pattern:idx"

  const handleSort = (key: SortKey) => {
    if (sortMode === key) setSortAsc(!sortAsc);
    // Date + numeric columns default to descending (newest / highest first).
    else { setSortMode(key); setSortAsc(key === 'ticker' || key === 'pattern'); }
  };

  const recencyMax = RECENCY_OPTIONS.find((o) => o.key === recency)?.maxDays ?? Infinity;

  const filtered = results.filter((r) => {
    const pat = filterPattern.toLowerCase();
    const tick = filterTicker.toUpperCase();
    if (pat && !r.pattern.toLowerCase().includes(pat)) return false;
    if (tick && !r.ticker.includes(tick)) return false;
    if (r.confidence < minConf) return false;
    if (recencyMax !== Infinity && daysAgo(r.end_date) > recencyMax) return false;
    if (direction === 'bull' && !BULLISH_PATTERNS.has(r.pattern)) return false;
    if (direction === 'bear' && !BEARISH_PATTERNS.has(r.pattern)) return false;
    return true;
  });

  const winRate = (r: ScanResult): number => winRates.get(`${r.ticker}:${r.pattern}`)?.win_rate ?? -1;

  const sorted = [...filtered].sort((a, b) => {
    if (sortMode === 'fresh') {
      // Today first; ties broken by highest confidence.
      const da = daysAgo(a.end_date), db = daysAgo(b.end_date);
      if (da !== db) return da - db;
      return b.confidence - a.confidence;
    }
    if (sortMode === 'win_rate') {
      const diff = winRate(a) - winRate(b);
      return sortAsc ? diff : -diff;
    }
    const av = a[sortMode];
    const bv = b[sortMode];
    if (typeof av === 'string' && typeof bv === 'string')
      return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
    return sortAsc ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
  });

  // Per-bucket counts for the group headers (only shown in 'fresh' mode).
  const bucketCounts: Record<string, number> = {};
  if (sortMode === 'fresh') {
    for (const r of sorted) {
      const k = bucketOf(daysAgo(r.end_date)).key;
      bucketCounts[k] = (bucketCounts[k] ?? 0) + 1;
    }
  }

  const totalUniquePairs = new Set(results.map((r) => `${r.ticker}:${r.pattern}`)).size;
  const sortLabel =
    sortMode === 'fresh' ? "Today's plays first, highest confidence each day"
    : sortMode === 'confidence' ? 'Highest confidence'
    : sortMode === 'win_rate' ? 'Highest win rate'
    : `${sortMode}`;

  let lastBucket = '';

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl flex flex-col overflow-hidden max-md:overflow-visible">
      {/* Header + text filters — stacks on phones; filters share the width */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-4 px-3 sm:px-4 py-3 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-bold text-white">Results</h2>
          <span className="bg-gray-800 text-gray-400 text-xs px-2 py-0.5 rounded-full">
            {filtered.length}
          </span>
        </div>
        <div className="flex gap-2 w-full sm:w-auto">
          <input
            className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white placeholder-gray-600 outline-none focus:border-indigo-500 min-w-0 flex-1 sm:flex-none sm:w-24"
            placeholder="Ticker…"
            value={filterTicker}
            onChange={(e) => setFilterTicker(e.target.value)}
          />
          <input
            className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white placeholder-gray-600 outline-none focus:border-indigo-500 min-w-0 flex-1 sm:flex-none sm:w-36"
            placeholder="Pattern…"
            value={filterPattern}
            onChange={(e) => setFilterPattern(e.target.value)}
          />
        </div>
      </div>

      {/* Sort + filter chips */}
      <div className="flex flex-wrap items-center gap-x-3 sm:gap-x-4 gap-y-2 px-3 sm:px-4 py-2.5 border-b border-gray-800 bg-gray-900/60">
        <ChipGroup label="Sort">
          <Chip active={sortMode === 'fresh'} onClick={() => setSortMode('fresh')}>Today&apos;s plays</Chip>
          <Chip active={sortMode === 'confidence'} onClick={() => { setSortMode('confidence'); setSortAsc(false); }}>Confidence</Chip>
          <Chip active={sortMode === 'win_rate'} onClick={() => { setSortMode('win_rate'); setSortAsc(false); }}>Win rate</Chip>
        </ChipGroup>
        <ChipGroup label="When">
          {RECENCY_OPTIONS.map((o) => (
            <Chip key={o.key} active={recency === o.key} onClick={() => setRecency(o.key)}>{o.label}</Chip>
          ))}
        </ChipGroup>
        <ChipGroup label="Min conf">
          {MIN_CONF_OPTIONS.map((c) => (
            <Chip key={c} active={minConf === c} onClick={() => setMinConf(c)}>{c === 0 ? 'Any' : `${c}+`}</Chip>
          ))}
        </ChipGroup>
        <ChipGroup label="Bias">
          <Chip active={direction === 'all'} onClick={() => setDirection('all')}>All</Chip>
          <Chip active={direction === 'bull'} onClick={() => setDirection('bull')} tone="bull">▲</Chip>
          <Chip active={direction === 'bear'} onClick={() => setDirection('bear')} tone="bear">▼</Chip>
        </ChipGroup>
      </div>

      {/* Table — fills the card on desktop (scrolls inside); on mobile it takes
          natural height and the page scrolls, since the card has no fixed height
          in the stacked single-column layout (a flex-1 child would collapse). */}
      <div className="overflow-auto flex-1 max-md:flex-none max-md:overflow-visible">
        {results.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-gray-600">
            <svg className="w-12 h-12 mb-3 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1}>
              <path strokeLinecap="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
            <p className="text-sm">No results yet — run a scan to find patterns</p>
          </div>
        ) : sorted.length === 0 ? (
          <div className="py-12 text-center text-gray-500 text-sm">No matches for current filters</div>
        ) : (
          <>
            {/* Mobile: stacked cards — a multi-column table is unreadable at phone
                width, so each result is a self-contained card here. */}
            <div className="md:hidden p-3 space-y-2">
              {(() => {
                let mb = '';
                return sorted.map((r, idx) => {
                  const bullish = BULLISH_PATTERNS.has(r.pattern);
                  const accent = bullish
                    ? 'border-l-emerald-500'
                    : BEARISH_PATTERNS.has(r.pattern)
                      ? 'border-l-red-500'
                      : 'border-l-amber-500';
                  const rowKey = `${r.ticker}:${r.pattern}:${idx}`;
                  const stats = winRates.get(`${r.ticker}:${r.pattern}`);
                  const open = contractRow === rowKey;
                  const days = daysAgo(r.end_date);

                  let head: React.ReactNode = null;
                  if (sortMode === 'fresh') {
                    const b = bucketOf(days);
                    if (b.key !== mb) {
                      mb = b.key;
                      head = (
                        <div className="px-1 pt-3 first:pt-0">
                          <span className="text-[10px] font-bold uppercase tracking-wider text-indigo-400/90">{b.label}</span>
                          <span className="text-[10px] text-gray-600 ml-2">· {bucketCounts[b.key]} signal{bucketCounts[b.key] === 1 ? '' : 's'}</span>
                        </div>
                      );
                    }
                  }

                  return (
                    <React.Fragment key={`m-${rowKey}`}>
                      {head}
                      <div
                        onClick={() => onRowClick(r)}
                        className={`rounded-lg border border-gray-800 border-l-2 ${accent} bg-gray-900/60 p-3 cursor-pointer active:bg-gray-800/40 transition-colors`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="font-mono font-bold text-white text-sm">{r.ticker}</span>
                            <span className="text-[11px] font-mono text-gray-500 truncate">{r.end_date.replace('T', ' ').slice(0, 16)}</span>
                            {days <= 1 && (
                              <span className="text-[9px] font-bold uppercase text-indigo-400 flex-shrink-0">{days <= 0 ? 'new' : '1d'}</span>
                            )}
                          </div>
                          <ConfidenceBadge confidence={r.confidence} />
                        </div>
                        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                          <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${bullish ? 'bg-emerald-900/30 text-emerald-400 border-emerald-800' : 'bg-red-900/30 text-red-400 border-red-800'}`}>
                            {bullish ? '▲' : '▼'} {r.pattern}
                          </span>
                          <WinRateBadge stats={stats} />
                        </div>
                        {r.description && (
                          <p className="mt-2 text-xs text-gray-500 line-clamp-2">{r.description}</p>
                        )}
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setContractRow((prev) => (prev === rowKey ? null : rowKey));
                          }}
                          className={`mt-2 w-full text-xs px-2 py-1.5 rounded border transition-colors ${
                            open
                              ? 'bg-indigo-600/30 border-indigo-500 text-indigo-300'
                              : 'bg-gray-800 border-gray-700 text-gray-400'
                          }`}
                        >
                          {open ? 'Hide contract' : 'View contract'}
                        </button>
                        {open && (
                          <div className="mt-2 -mx-3" onClick={(e) => e.stopPropagation()}>
                            <ContractPanel
                              ticker={r.ticker}
                              pattern={r.pattern}
                              timeframe={timeframe}
                              bullish={bullish}
                              onClose={() => setContractRow(null)}
                            />
                          </div>
                        )}
                      </div>
                    </React.Fragment>
                  );
                });
              })()}
            </div>

            {/* Desktop: full table */}
            <table className="hidden md:table w-full text-sm border-collapse">
            <thead className="sticky top-0 bg-gray-900 z-10">
              <tr>
                {COLS.map((col) => (
                  <th
                    key={col.key}
                    onClick={() => !col.noSort && handleSort(col.key as SortKey)}
                    className={`text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-3 select-none border-b border-gray-800 whitespace-nowrap ${col.noSort ? 'cursor-default' : 'cursor-pointer hover:text-gray-300'}`}
                  >
                    {col.label}
                    {!col.noSort && <SortIcon active={sortMode === col.key} asc={sortAsc} />}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, idx) => {
                const accent = rowAccent(r.pattern);
                const bullish = BULLISH_PATTERNS.has(r.pattern);
                const rowKey = `${r.ticker}:${r.pattern}:${idx}`;
                const stats = winRates.get(`${r.ticker}:${r.pattern}`);
                const contractOpen = contractRow === rowKey;
                const days = daysAgo(r.end_date);

                // Day-group separator (only when sorted by freshness).
                let header: React.ReactNode = null;
                if (sortMode === 'fresh') {
                  const b = bucketOf(days);
                  if (b.key !== lastBucket) {
                    lastBucket = b.key;
                    header = (
                      <tr className="bg-gray-800/40">
                        <td colSpan={COLS.length} className="px-4 py-1.5 border-y border-gray-800">
                          <span className="text-[10px] font-bold uppercase tracking-wider text-indigo-400/90">{b.label}</span>
                          <span className="text-[10px] text-gray-600 ml-2">· {bucketCounts[b.key]} signal{bucketCounts[b.key] === 1 ? '' : 's'}</span>
                        </td>
                      </tr>
                    );
                  }
                }

                return (
                  <React.Fragment key={rowKey}>
                    {header}
                    <tr
                      onClick={() => onRowClick(r)}
                      className={`border-l-2 cursor-pointer transition-colors ${accent} border-b border-gray-800/50`}
                    >
                      <td className="px-4 py-3 font-mono font-bold text-white whitespace-nowrap">{r.ticker}</td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${bullish ? 'bg-emerald-900/30 text-emerald-400 border-emerald-800' : 'bg-red-900/30 text-red-400 border-red-800'}`}>
                          {bullish ? '▲' : '▼'} {r.pattern}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-gray-400 font-mono text-xs whitespace-nowrap">
                        {r.end_date.replace('T', ' ')}
                        {days <= 1 && (
                          <span className="ml-1.5 text-[9px] font-sans font-bold uppercase text-indigo-400">
                            {days <= 0 ? 'new' : '1d'}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <ConfidenceBadge confidence={r.confidence} />
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap min-w-[140px]">
                        <WinRateBadge stats={stats} />
                      </td>
                      <td className="px-4 py-3 text-gray-500 text-xs max-w-sm truncate">{r.description}</td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setContractRow((prev) => (prev === rowKey ? null : rowKey));
                          }}
                          className={`text-xs px-2 py-1 rounded border transition-colors ${
                            contractOpen
                              ? 'bg-indigo-600/30 border-indigo-500 text-indigo-300'
                              : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-indigo-600 hover:text-indigo-400'
                          }`}
                        >
                          {contractOpen ? 'Hide' : 'Contract'}
                        </button>
                      </td>
                    </tr>
                    {contractOpen && (
                      <tr className="border-b border-gray-800/30">
                        <td colSpan={COLS.length} className="p-0">
                          <ContractPanel
                            ticker={r.ticker}
                            pattern={r.pattern}
                            timeframe={timeframe}
                            bullish={bullish}
                            onClose={() => setContractRow(null)}
                          />
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
          </>
        )}
      </div>

      {/* Footer */}
      {results.length > 0 && (
        <div className="px-4 py-2 border-t border-gray-800 flex items-center justify-between">
          <span className="text-xs text-gray-600">
            {sortLabel} · Click row for chart · Contract for options rec
            {winRates.size < totalUniquePairs && (
              <span className="ml-2 text-indigo-600 animate-pulse">· Loading win rates…</span>
            )}
          </span>
          <div className="flex gap-3 text-xs text-gray-600">
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              {results.filter((r) => BULLISH_PATTERNS.has(r.pattern)).length} bullish
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-red-500" />
              {results.filter((r) => !BULLISH_PATTERNS.has(r.pattern)).length} bearish
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
