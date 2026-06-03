import React, { useState } from 'react';
import { ConfidenceBadge } from './confidence-badge';
import type { ScanResult, HistoricalStats } from '@/types/patterns';
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

/**
 * ContractPanel — full option chain for a pattern hit, with the expiry
 * selector and recommended-contract highlight from the shared
 * OptionChainViewer. Bullish patterns lean calls, bearish lean puts; the
 * preferred DTE is the pattern's historical average hold so the default
 * expiry lands near the window the move is expected to play out in.
 */
function ContractPanel({
  ticker,
  bullish,
  horizonDays,
  onClose,
}: {
  ticker: string;
  bullish: boolean;
  horizonDays: number;
  onClose: () => void;
}) {
  const direction: 'call' | 'put' = bullish ? 'call' : 'put';
  const dte = Math.max(14, Math.round(horizonDays));
  const expiryLean: ScreenerRecommendation['expiry_lean'] =
    dte <= 14 ? 'near' : dte <= 28 ? 'mid' : 'far';

  const recommendation: ScreenerRecommendation = {
    direction,
    strike_offset_pct: 0, // ATM — clearest expression of a directional pattern bet
    expiry_lean: expiryLean,
    reasoning: `${bullish ? 'Bullish' : 'Bearish'} pattern — an ATM ${direction} expresses the directional bet. This pattern historically resolves over ~${Math.round(horizonDays)} trading days, so an expiry near ${dte}d gives the move room to play out before theta bites.`,
  };

  return (
    <div className="mx-4 mb-3 p-3 rounded-lg bg-indigo-900/20 border border-indigo-800/40 text-xs">
      <div className="flex items-center justify-between mb-1">
        <span className="font-semibold text-indigo-300">
          Recommended {direction.toUpperCase()} · {ticker}
        </span>
        <button type="button" onClick={onClose} className="text-gray-500 hover:text-gray-300">✕</button>
      </div>
      <OptionChainViewer ticker={ticker} recommendation={recommendation} preferredDte={dte} />
    </div>
  );
}

type SortKey = 'ticker' | 'pattern' | 'end_date' | 'confidence';

const COLS: { key: SortKey | 'win_rate' | 'description' | 'contract'; label: string; noSort?: boolean }[] = [
  { key: 'ticker', label: 'Ticker' },
  { key: 'pattern', label: 'Pattern' },
  { key: 'end_date', label: 'Signal Date' },
  { key: 'confidence', label: 'Confidence' },
  { key: 'win_rate', label: 'Win Rate', noSort: true },
  { key: 'description', label: 'Description', noSort: true },
  { key: 'contract', label: 'Contract', noSort: true },
];

interface ResultsTableProps {
  results: ScanResult[];
  onRowClick: (row: ScanResult) => void;
  winRates: Map<string, HistoricalStats>;
}

export function ResultsTable({ results, onRowClick, winRates }: ResultsTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('confidence');
  const [sortAsc, setSortAsc] = useState(false);
  const [filterPattern, setFilterPattern] = useState('');
  const [filterTicker, setFilterTicker] = useState('');
  const [contractRow, setContractRow] = useState<string | null>(null); // "ticker:pattern:idx"

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(key !== 'confidence'); }
  };

  const filtered = results
    .filter((r) => {
      const pat = filterPattern.toLowerCase();
      const tick = filterTicker.toUpperCase();
      return (
        (!pat || r.pattern.toLowerCase().includes(pat)) &&
        (!tick || r.ticker.includes(tick))
      );
    })
    .sort((a, b) => {
      const av = a[sortKey as SortKey];
      const bv = b[sortKey as SortKey];
      if (typeof av === 'string' && typeof bv === 'string')
        return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortAsc ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
    });

  const totalUniquePairs = new Set(results.map((r) => `${r.ticker}:${r.pattern}`)).size;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl flex flex-col overflow-hidden">
      {/* Header + filters */}
      <div className="flex items-center justify-between gap-4 px-4 py-3 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-bold text-white">Results</h2>
          <span className="bg-gray-800 text-gray-400 text-xs px-2 py-0.5 rounded-full">
            {filtered.length}
          </span>
        </div>
        <div className="flex gap-2">
          <input
            className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white placeholder-gray-600 outline-none focus:border-indigo-500 w-24"
            placeholder="Ticker…"
            value={filterTicker}
            onChange={(e) => setFilterTicker(e.target.value)}
          />
          <input
            className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white placeholder-gray-600 outline-none focus:border-indigo-500 w-36"
            placeholder="Pattern…"
            value={filterPattern}
            onChange={(e) => setFilterPattern(e.target.value)}
          />
        </div>
      </div>

      {/* Table */}
      <div className="overflow-auto flex-1">
        {results.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-gray-600">
            <svg className="w-12 h-12 mb-3 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1}>
              <path strokeLinecap="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
            <p className="text-sm">No results yet — run a scan to find patterns</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="py-12 text-center text-gray-500 text-sm">No matches for current filters</div>
        ) : (
          <table className="w-full text-sm border-collapse">
            <thead className="sticky top-0 bg-gray-900 z-10">
              <tr>
                {COLS.map((col) => (
                  <th
                    key={col.key}
                    onClick={() => !col.noSort && handleSort(col.key as SortKey)}
                    className={`text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-3 select-none border-b border-gray-800 whitespace-nowrap ${col.noSort ? 'cursor-default' : 'cursor-pointer hover:text-gray-300'}`}
                  >
                    {col.label}
                    {!col.noSort && <SortIcon active={sortKey === col.key} asc={sortAsc} />}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((r, idx) => {
                const accent = rowAccent(r.pattern);
                const bullish = BULLISH_PATTERNS.has(r.pattern);
                const rowKey = `${r.ticker}:${r.pattern}:${idx}`;
                const stats = winRates.get(`${r.ticker}:${r.pattern}`);
                const horizonDays = stats ? stats.outcome_bars * 1.5 : 30;
                const contractOpen = contractRow === rowKey;
                return (
                  <React.Fragment key={rowKey}>
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
                      <td className="px-4 py-3 text-gray-400 font-mono text-xs whitespace-nowrap">{r.end_date}</td>
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
                            bullish={bullish}
                            horizonDays={horizonDays}
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
        )}
      </div>

      {/* Footer */}
      {results.length > 0 && (
        <div className="px-4 py-2 border-t border-gray-800 flex items-center justify-between">
          <span className="text-xs text-gray-600">
            Sorted by confidence · Click row for chart · Contract for options rec
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
