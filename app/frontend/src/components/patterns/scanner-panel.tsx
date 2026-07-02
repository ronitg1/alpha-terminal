import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { usePatternScan } from '@/contexts/pattern-scan-context';
import { useSleevesContext } from '@/contexts/sleeves-context';
import type { PatternTimeframe } from '@/types/patterns';

const ALL_PATTERNS = [
  'Bullish Flag', 'Bull Pennant', 'Double Bottom', 'Inverse Head and Shoulders',
  'Ascending Triangle', 'Cup and Handle', 'Falling Wedge',
  'Head and Shoulders', 'Double Top', 'Descending Triangle', 'Rising Wedge', 'Bearish Flag',
];

const BULLISH_SET = new Set([
  'Bullish Flag', 'Bull Pennant', 'Double Bottom', 'Inverse Head and Shoulders',
  'Ascending Triangle', 'Cup and Handle', 'Falling Wedge',
]);

const LOOKBACK_OPTIONS: { label: string; value: number }[] = [
  { label: '30d', value: 30 },
  { label: '60d', value: 60 },
  { label: '90d', value: 90 },
  { label: '180d', value: 180 },
  { label: '1yr', value: 365 },
];

// Per-timeframe lookback menus — intraday scans are clamped server-side
// (1h: 90d max, 15m: 30d max), so only offer choices that fit.
const TIMEFRAME_OPTIONS: {
  value: PatternTimeframe;
  label: string;
  lookbacks: { label: string; value: number }[];
  defaultLookback: number;
}[] = [
  {
    value: 'week',
    label: 'Weekly',
    lookbacks: [
      { label: '1yr', value: 365 },
      { label: '2yr', value: 730 },
      { label: '3yr', value: 1095 },
      { label: '5yr', value: 1825 },
    ],
    defaultLookback: 1095,
  },
  { value: 'day', label: 'Daily', lookbacks: LOOKBACK_OPTIONS, defaultLookback: 180 },
  {
    value: '1h',
    label: '1h',
    lookbacks: [
      { label: '5d', value: 5 },
      { label: '10d', value: 10 },
      { label: '30d', value: 30 },
      { label: '60d', value: 60 },
      { label: '90d', value: 90 },
    ],
    defaultLookback: 30,
  },
  {
    value: '15m',
    label: '15m',
    lookbacks: [
      { label: '2d', value: 2 },
      { label: '5d', value: 5 },
      { label: '10d', value: 10 },
      { label: '20d', value: 20 },
      { label: '30d', value: 30 },
    ],
    defaultLookback: 10,
  },
];

type TabId = 'watchlist' | 'sleeves' | 'custom';

function dedupe(arr: string[]): string[] {
  return [...new Set(arr.map((t) => t.toUpperCase().trim()).filter(Boolean))];
}

function parseCustomTickers(text: string): string[] {
  return dedupe(text.split(/[\s,;]+/));
}

export function ScannerPanel() {
  const { watchlists, config } = useSleevesContext();
  const { isScanning, runScan } = usePatternScan();

  const [tab, setTab] = useState<TabId>('watchlist');
  const [selectedWatchlist, setSelectedWatchlist] = useState<string>('all');
  const [selectedSleeve, setSelectedSleeve] = useState<string>('all');
  const [customText, setCustomText] = useState<string>('');
  const [selectedPatterns, setSelectedPatterns] = useState<string[]>([...ALL_PATTERNS]);
  const [timeframe, setTimeframe] = useState<PatternTimeframe>('day');
  const [lookback, setLookback] = useState<number>(180);
  const [patternOpen, setPatternOpen] = useState(false);

  const tfConfig = TIMEFRAME_OPTIONS.find((t) => t.value === timeframe) ?? TIMEFRAME_OPTIONS[0];

  const selectTimeframe = (tf: PatternTimeframe) => {
    setTimeframe(tf);
    const cfg = TIMEFRAME_OPTIONS.find((t) => t.value === tf);
    // Reset lookback to the timeframe's default — the previous value is
    // usually out of range for the new bar size.
    if (cfg) setLookback(cfg.defaultLookback);
  };

  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setPatternOpen(false);
      }
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, []);

  const resolvedTickers = useCallback((): string[] => {
    if (tab === 'watchlist') {
      if (selectedWatchlist === 'all') {
        return dedupe(watchlists.flatMap((w) => w.tickers.map((t) => t.ticker)));
      }
      const wl = watchlists.find((w) => w.name === selectedWatchlist);
      return wl ? dedupe(wl.tickers.map((t) => t.ticker)) : [];
    }
    if (tab === 'sleeves') {
      if (selectedSleeve === 'all') {
        return dedupe(config?.sleeves.flatMap((s) => s.tickers) ?? []);
      }
      const sl = config?.sleeves.find((s) => s.name === selectedSleeve);
      return sl ? dedupe(sl.tickers) : [];
    }
    return parseCustomTickers(customText);
  }, [tab, selectedWatchlist, selectedSleeve, customText, watchlists, config]);

  const tickerCount = resolvedTickers().length;

  const togglePattern = (p: string) => {
    setSelectedPatterns((prev) =>
      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p],
    );
  };

  const toggleAll = () => {
    setSelectedPatterns((prev) => (prev.length === ALL_PATTERNS.length ? [] : [...ALL_PATTERNS]));
  };

  const handleScan = () => {
    const tickers = resolvedTickers();
    if (tickers.length === 0) {
      toast.error('No tickers selected. Add tickers to a watchlist, portfolio, or enter them manually.');
      return;
    }
    if (selectedPatterns.length === 0) {
      toast.error('Select at least one pattern.');
      return;
    }
    // Hand off to the provider, which runs the scan detached from this component
    // so it keeps going (and lands its results) even if you navigate away.
    runScan({ tickers, patterns: selectedPatterns, lookback, timeframe });
  };

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-200">Pattern Scanner</h2>
      </div>

      {/* ── Tabs ── */}
      <div className="flex border-b border-gray-800">
        {([
          { id: 'watchlist', label: 'Watchlist' },
          { id: 'sleeves', label: 'My Portfolios' },
          { id: 'custom', label: 'Custom' },
        ] as { id: TabId; label: string }[]).map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={
              'flex-1 py-2 text-xs font-medium transition-colors ' +
              (tab === t.id
                ? 'text-indigo-400 border-b-2 border-indigo-500 bg-gray-800/40'
                : 'text-gray-500 hover:text-gray-300')
            }
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="p-4 space-y-4">
        {/* ── Tab content ── */}
        {tab === 'watchlist' && (
          <div className="space-y-2">
            <label className="text-xs text-gray-400 block">Watchlist</label>
            <select
              value={selectedWatchlist}
              onChange={(e) => setSelectedWatchlist(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-indigo-500"
            >
              <option value="all">All Watchlists</option>
              {watchlists.map((w) => (
                <option key={w.name} value={w.name}>
                  {w.name}
                </option>
              ))}
            </select>
            {watchlists.length === 0 && (
              <p className="text-xs text-gray-600 italic">No watchlists — create one in Market.</p>
            )}
          </div>
        )}

        {tab === 'sleeves' && (
          <div className="space-y-2">
            <label className="text-xs text-gray-400 block">Portfolio</label>
            <select
              value={selectedSleeve}
              onChange={(e) => setSelectedSleeve(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-indigo-500"
            >
              <option value="all">All Portfolios</option>
              {(config?.sleeves ?? []).map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name.replace(/_/g, ' ')}
                </option>
              ))}
            </select>
            {(!config || config.sleeves.length === 0) && (
              <p className="text-xs text-gray-600 italic">No portfolios configured.</p>
            )}
          </div>
        )}

        {tab === 'custom' && (
          <div className="space-y-2">
            <label className="text-xs text-gray-400 block">Tickers (comma or space separated)</label>
            <textarea
              value={customText}
              onChange={(e) => setCustomText(e.target.value)}
              placeholder="AAPL MSFT NVDA, TSLA"
              rows={3}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-indigo-500 resize-none font-mono"
            />
          </div>
        )}

        {/* ── Ticker count ── */}
        <div className="flex items-center gap-2">
          <div className="h-px flex-1 bg-gray-800" />
          <span className="text-xs text-gray-500">
            {tickerCount === 0 ? 'No tickers' : `${tickerCount} ticker${tickerCount !== 1 ? 's' : ''}`}
          </span>
          <div className="h-px flex-1 bg-gray-800" />
        </div>

        {/* ── Pattern multiselect ── */}
        <div>
          <label className="text-xs text-gray-400 block mb-1.5">Patterns</label>
          <div className="relative" ref={dropdownRef}>
            <button
              type="button"
              onClick={() => setPatternOpen((o) => !o)}
              className="w-full flex items-center justify-between bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200 hover:border-indigo-500 focus:outline-none"
            >
              <span className="truncate">
                {selectedPatterns.length === ALL_PATTERNS.length
                  ? 'All patterns'
                  : selectedPatterns.length === 0
                  ? 'None selected'
                  : `${selectedPatterns.length} selected`}
              </span>
              <svg className="w-4 h-4 text-gray-500 flex-shrink-0 ml-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {patternOpen && (
              <div className="absolute z-50 mt-1 w-full bg-gray-800 border border-gray-700 rounded-lg shadow-xl overflow-hidden">
                <div className="p-2 border-b border-gray-700">
                  <button
                    type="button"
                    onClick={toggleAll}
                    className="text-xs text-indigo-400 hover:text-indigo-300"
                  >
                    {selectedPatterns.length === ALL_PATTERNS.length ? 'Deselect all' : 'Select all'}
                  </button>
                </div>
                <div className="max-h-52 overflow-y-auto">
                  {ALL_PATTERNS.map((p) => (
                    <label
                      key={p}
                      className="flex items-center gap-2.5 px-3 py-1.5 hover:bg-gray-700/50 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={selectedPatterns.includes(p)}
                        onChange={() => togglePattern(p)}
                        className="accent-indigo-500 w-3.5 h-3.5"
                      />
                      <span
                        className={`text-xs ${BULLISH_SET.has(p) ? 'text-emerald-400' : 'text-red-400'}`}
                      >
                        {p}
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ── Timeframe (bar size) ── */}
        <div>
          <label className="text-xs text-gray-400 block mb-1.5">Timeframe</label>
          <div className="flex gap-1.5">
            {TIMEFRAME_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => selectTimeframe(opt.value)}
                title={
                  opt.value === 'week'
                    ? 'Weekly bars — long-base / position setups (months)'
                    : opt.value === 'day'
                      ? 'Daily bars — swing / position setups'
                      : opt.value === '1h'
                        ? 'Hourly bars — multi-day swing setups'
                        : '15-minute bars — day-trade setups'
                }
                className={
                  'flex-1 py-1 text-xs rounded-md font-medium transition-colors ' +
                  (timeframe === opt.value
                    ? 'bg-indigo-600 text-white'
                    : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-gray-200')
                }
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* ── Lookback ── */}
        <div>
          <label className="text-xs text-gray-400 block mb-1.5">Lookback Period</label>
          <div className="flex gap-1.5">
            {tfConfig.lookbacks.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setLookback(opt.value)}
                className={
                  'flex-1 py-1 text-xs rounded-md font-medium transition-colors ' +
                  (lookback === opt.value
                    ? 'bg-indigo-600 text-white'
                    : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-gray-200')
                }
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* ── Scan button ── */}
        <button
          type="button"
          onClick={handleScan}
          disabled={isScanning || tickerCount === 0}
          className="w-full py-2 rounded-lg text-sm font-semibold transition-all disabled:opacity-40 disabled:cursor-not-allowed bg-indigo-600 hover:bg-indigo-500 text-white"
        >
          {isScanning ? (
            <span className="flex items-center justify-center gap-2">
              <svg className="animate-spin h-3.5 w-3.5" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              Scanning…
            </span>
          ) : (
            `Run Scan${tickerCount > 0 ? ` (${tickerCount})` : ''}`
          )}
        </button>

        {/* ── Legend ── */}
        <div className="flex gap-4 justify-center">
          <span className="text-xs text-emerald-500 flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> Bullish
          </span>
          <span className="text-xs text-red-400 flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-red-500 inline-block" /> Bearish
          </span>
        </div>
      </div>
    </div>
  );
}
