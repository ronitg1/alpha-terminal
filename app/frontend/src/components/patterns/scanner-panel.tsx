import { useCallback, useEffect, useRef, useState } from 'react';
import { scanTickers, scanWatchlist } from '@/services/patterns-api';
import type { ScanResult } from '@/types/patterns';

const ALL_PATTERNS = [
  'Bullish Flag', 'Bearish Flag', 'Bull Pennant',
  'Double Bottom', 'Double Top',
  'Head and Shoulders', 'Inverse Head and Shoulders',
  'Ascending Triangle', 'Descending Triangle',
  'Cup and Handle', 'Rising Wedge', 'Falling Wedge',
];

const BULLISH_SET = new Set([
  'Bullish Flag', 'Bull Pennant', 'Double Bottom', 'Inverse Head and Shoulders',
  'Ascending Triangle', 'Cup and Handle', 'Falling Wedge',
]);

const LOOKBACK_OPTIONS = [
  { value: 30, label: '30d' },
  { value: 60, label: '60d' },
  { value: 90, label: '90d' },
  { value: 180, label: '180d' },
  { value: 365, label: '1yr' },
];

type TickerSource = 'watchlist' | 'sleeves' | 'custom';

interface SleeveInfo {
  name: string;
  tickers: string[];
}

interface ScannerPanelProps {
  onResults: (results: ScanResult[]) => void;
  isScanning: boolean;
  setIsScanning: (v: boolean) => void;
}

function formatSleeveName(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function ScannerPanel({ onResults, isScanning, setIsScanning }: ScannerPanelProps) {
  const [tickerSource, setTickerSource] = useState<TickerSource>('watchlist');
  const [tickerInput, setTickerInput] = useState('');
  const [selectedPatterns, setSelectedPatterns] = useState<string[]>([...ALL_PATTERNS]);
  const [lookback, setLookback] = useState(180);
  const [patternOpen, setPatternOpen] = useState(false);

  // Sleeve state
  const [sleeves, setSleeves] = useState<SleeveInfo[]>([]);
  const [selectedSleeve, setSelectedSleeve] = useState<string>('all');
  const [sleevesLoading, setSleevesLoading] = useState(false);
  const [sleevesError, setSleevesError] = useState<string | null>(null);

  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close pattern dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node))
        setPatternOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Populate ticker input from sleeve list + selected sleeve
  const applySleeveSelection = useCallback((data: SleeveInfo[], sleeve: string) => {
    const tickers =
      sleeve === 'all'
        ? [...new Set(data.flatMap((s) => s.tickers))]
        : data.find((s) => s.name === sleeve)?.tickers ?? [];
    setTickerInput(tickers.join(', '));
  }, []);

  // Fetch sleeve config from the backend
  const fetchSleeves = useCallback(
    (sleeve: string) => {
      setSleevesLoading(true);
      setSleevesError(null);
      fetch('http://localhost:8000/sleeves/config')
        .then((r) => r.json())
        .then((data: { sleeves: SleeveInfo[] }) => {
          setSleeves(data.sleeves);
          setSelectedSleeve(sleeve);
          applySleeveSelection(data.sleeves, sleeve);
        })
        .catch(() => setSleevesError('Could not load sleeve config — check backend'))
        .finally(() => setSleevesLoading(false));
    },
    [applySleeveSelection],
  );

  // Re-fetch whenever the user switches to "My Sleeves"
  useEffect(() => {
    if (tickerSource !== 'sleeves') return;
    fetchSleeves(selectedSleeve);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tickerSource]);

  // When the selected sleeve changes, re-populate without a network call
  const handleSleeveChange = (sleeve: string) => {
    setSelectedSleeve(sleeve);
    applySleeveSelection(sleeves, sleeve);
  };

  const togglePattern = (p: string) =>
    setSelectedPatterns((prev) =>
      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p],
    );

  const toggleAll = () =>
    setSelectedPatterns(selectedPatterns.length === ALL_PATTERNS.length ? [] : [...ALL_PATTERNS]);

  const handleScan = async () => {
    const patterns = selectedPatterns.length === ALL_PATTERNS.length ? [] : selectedPatterns;

    if (tickerSource === 'watchlist') {
      setIsScanning(true);
      try {
        onResults(await scanWatchlist(patterns, lookback));
      } catch (err) {
        alert(`Scan failed: ${(err as Error).message}`);
      } finally {
        setIsScanning(false);
      }
      return;
    }

    const tickers = tickerInput
      .toUpperCase()
      .split(/[\s,]+/)
      .map((t) => t.trim())
      .filter(Boolean);

    if (tickers.length === 0) {
      alert('Enter at least one ticker symbol.');
      return;
    }

    setIsScanning(true);
    try {
      onResults(await scanTickers(tickers, patterns, lookback));
    } catch (err) {
      alert(`Scan failed: ${(err as Error).message}`);
    } finally {
      setIsScanning(false);
    }
  };

  const sourceLabels: Record<TickerSource, string> = {
    watchlist: 'Market Watchlist (50)',
    sleeves: 'My Sleeves',
    custom: 'Custom Tickers',
  };

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-5 space-y-5">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-lg bg-indigo-600/20 border border-indigo-500/30 flex items-center justify-center">
          <svg className="w-4 h-4 text-indigo-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <circle cx="11" cy="11" r="8" />
            <path strokeLinecap="round" d="m21 21-4.35-4.35" />
          </svg>
        </div>
        <div>
          <h2 className="text-sm font-bold text-white">Pattern Scanner</h2>
          <p className="text-xs text-gray-500">12 chart pattern detectors</p>
        </div>
      </div>

      {/* Ticker source */}
      <div>
        <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
          Ticker Source
        </label>
        <div className="flex rounded-lg overflow-hidden border border-gray-700 text-xs">
          {(['watchlist', 'sleeves', 'custom'] as TickerSource[]).map((src) => (
            <button
              key={src}
              onClick={() => setTickerSource(src)}
              className={`flex-1 py-2 font-medium transition-colors ${
                tickerSource === src
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:text-gray-200'
              }`}
            >
              {sourceLabels[src]}
            </button>
          ))}
        </div>
      </div>

      {/* Sleeve selector — shown when My Sleeves is active */}
      {tickerSource === 'sleeves' && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
              Sleeve
            </label>
            <button
              onClick={() => fetchSleeves(selectedSleeve)}
              disabled={sleevesLoading}
              className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300 disabled:opacity-50 transition-colors"
              title="Refresh sleeve list"
            >
              <svg
                className={`w-3 h-3 ${sleevesLoading ? 'animate-spin' : ''}`}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                />
              </svg>
              {sleevesLoading ? 'Loading…' : 'Refresh'}
            </button>
          </div>

          {sleevesError ? (
            <p className="text-xs text-red-400">{sleevesError}</p>
          ) : (
            <select
              value={selectedSleeve}
              onChange={(e) => handleSleeveChange(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-indigo-500"
            >
              <option value="all">All Sleeves ({[...new Set(sleeves.flatMap((s) => s.tickers))].length} tickers)</option>
              {sleeves.map((s) => (
                <option key={s.name} value={s.name}>
                  {formatSleeveName(s.name)} ({s.tickers.length} tickers)
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      {/* Ticker input — editable for sleeves (pre-populated) and custom */}
      {tickerSource !== 'watchlist' && (
        <div>
          <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            Tickers
          </label>
          <textarea
            className="w-full h-20 resize-none font-mono text-xs leading-relaxed bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white placeholder-gray-600 outline-none focus:border-indigo-500"
            placeholder="AAPL, MSFT, NVDA, TSLA..."
            value={tickerInput}
            onChange={(e) => setTickerInput(e.target.value)}
          />
          <p className="text-xs text-gray-600 mt-1">
            {tickerSource === 'sleeves'
              ? 'Pre-populated from sleeve — edit freely before scanning'
              : 'Comma or space separated'}
          </p>
        </div>
      )}

      {/* Pattern multiselect */}
      <div ref={dropdownRef} className="relative">
        <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
          Patterns
        </label>
        <button
          onClick={() => setPatternOpen(!patternOpen)}
          className="w-full text-left flex items-center justify-between bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-indigo-500"
        >
          <span className="text-gray-300 text-xs">
            {selectedPatterns.length === ALL_PATTERNS.length
              ? 'All patterns selected'
              : selectedPatterns.length === 0
              ? 'No patterns selected'
              : `${selectedPatterns.length} of ${ALL_PATTERNS.length} selected`}
          </span>
          <svg
            className={`w-4 h-4 text-gray-500 transition-transform ${patternOpen ? 'rotate-180' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {patternOpen && (
          <div className="absolute z-20 top-full mt-1 w-full bg-gray-800 border border-gray-700 rounded-lg shadow-xl py-1 max-h-64 overflow-y-auto">
            <button
              onClick={toggleAll}
              className="w-full text-left px-3 py-2 text-xs font-semibold text-indigo-400 hover:bg-gray-700 border-b border-gray-700"
            >
              {selectedPatterns.length === ALL_PATTERNS.length ? '✓ Deselect All' : '○ Select All'}
            </button>
            {ALL_PATTERNS.map((p) => {
              const checked = selectedPatterns.includes(p);
              const bullish = BULLISH_SET.has(p);
              return (
                <button
                  key={p}
                  onClick={() => togglePattern(p)}
                  className={`w-full text-left px-3 py-2 text-xs flex items-center gap-2.5 hover:bg-gray-700 transition-colors ${checked ? 'text-gray-100' : 'text-gray-500'}`}
                >
                  <span
                    className={`w-3.5 h-3.5 rounded border flex-shrink-0 flex items-center justify-center text-[9px] ${checked ? 'bg-indigo-600 border-indigo-500 text-white' : 'border-gray-600'}`}
                  >
                    {checked && '✓'}
                  </span>
                  <span
                    className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${bullish ? 'bg-emerald-500' : 'bg-red-500'}`}
                  />
                  {p}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Lookback period */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <label className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
            Lookback Period
          </label>
          <span className="text-indigo-400 text-xs font-mono font-bold">
            {LOOKBACK_OPTIONS.find((o) => o.value === lookback)?.label}
          </span>
        </div>
        <div className="flex gap-1">
          {LOOKBACK_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setLookback(opt.value)}
              className={`flex-1 py-1.5 text-xs font-medium rounded transition-colors ${
                lookback === opt.value
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-800 text-gray-500 hover:text-gray-300 border border-gray-700'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Scan button */}
      <button
        onClick={handleScan}
        disabled={isScanning || selectedPatterns.length === 0}
        className="w-full flex items-center justify-center gap-2 h-11 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold rounded-lg transition-colors text-sm"
      >
        {isScanning ? (
          <>
            <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
              />
            </svg>
            Scanning markets…
          </>
        ) : (
          <>
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <circle cx="11" cy="11" r="8" />
              <path strokeLinecap="round" d="m21 21-4.35-4.35" />
            </svg>
            Run Scan
          </>
        )}
      </button>

      {/* Legend */}
      <div className="flex gap-4 pt-1 border-t border-gray-800">
        <div className="flex items-center gap-1.5 text-xs text-gray-500">
          <span className="w-2 h-2 rounded-full bg-emerald-500" /> Bullish
        </div>
        <div className="flex items-center gap-1.5 text-xs text-gray-500">
          <span className="w-2 h-2 rounded-full bg-red-500" /> Bearish
        </div>
      </div>
    </div>
  );
}
