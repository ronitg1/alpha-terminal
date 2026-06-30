import { useEffect, useState } from 'react';
import { getSignalAnalysis } from '@/services/patterns-api';
import type { HistoricalStats, PatternTimeframe, ScanResult } from '@/types/patterns';
import { ScannerPanel } from './scanner-panel';
import { ResultsTable } from './results-table';
import { ChartModal } from './chart-modal';
import { PatternBacktestPanel } from './pattern-backtest-panel';

interface ChartTarget {
  ticker: string;
  pattern: string | null;
  endDate: string | null;
}

function QuickStats({
  results,
  onTickerClick,
}: {
  results: ScanResult[];
  onTickerClick: (ticker: string) => void;
}) {
  const avgConf = Math.round(results.reduce((s, r) => s + r.confidence, 0) / results.length);
  const bullishCount = results.filter((r) => r.bullish).length;
  const bearishCount = results.length - bullishCount;

  const tickerCounts: Record<string, number> = {};
  results.forEach((r) => { tickerCounts[r.ticker] = (tickerCounts[r.ticker] || 0) + 1; });
  const topTickers = Object.entries(tickerCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Quick Stats</h3>
      <div className="grid grid-cols-2 gap-3">
        {[
          { label: 'Total Signals', value: String(results.length), color: 'text-white' },
          { label: 'Avg Confidence', value: `${avgConf}%`, color: 'text-indigo-400' },
          { label: 'Bullish', value: String(bullishCount), color: 'text-emerald-400' },
          { label: 'Bearish', value: String(bearishCount), color: 'text-red-400' },
        ].map((s) => (
          <div key={s.label} className="bg-gray-800/60 rounded-lg p-3">
            <div className={`text-lg font-bold font-mono ${s.color}`}>{s.value}</div>
            <div className="text-xs text-gray-500 mt-0.5">{s.label}</div>
          </div>
        ))}
      </div>

      {topTickers.length > 0 && (
        <div>
          <p className="text-xs text-gray-600 mb-2">Top tickers by signal count</p>
          <div className="space-y-1.5">
            {topTickers.map(([ticker, count]) => (
              <div key={ticker} className="flex items-center justify-between">
                <button
                  onClick={() => onTickerClick(ticker)}
                  className="font-mono text-xs text-indigo-400 hover:text-indigo-300"
                >
                  {ticker}
                </button>
                <div className="flex items-center gap-2">
                  <div className="h-1 bg-indigo-600/40 rounded-full" style={{ width: `${count * 12}px` }} />
                  <span className="text-xs text-gray-500">{count}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function PatternsTab() {
  const [view, setView] = useState<'scanner' | 'backtest'>('scanner');
  const [results, setResults] = useState<ScanResult[]>([]);
  const [timeframe, setTimeframe] = useState<PatternTimeframe>('day');
  const [isScanning, setIsScanning] = useState(false);
  const [chart, setChart] = useState<ChartTarget | null>(null);
  const [winRates, setWinRates] = useState<Map<string, HistoricalStats>>(new Map());

  const handleResults = (rows: ScanResult[], tf: PatternTimeframe) => {
    setTimeframe(tf);
    setResults(rows);
  };

  // Background-fetch win rates for all unique ticker+pattern pairs after a scan
  useEffect(() => {
    if (results.length === 0) { setWinRates(new Map()); return; }

    const seen = new Set<string>();
    const pairs: { ticker: string; pattern: string; key: string }[] = [];
    for (const r of results) {
      const key = `${r.ticker}:${r.pattern}`;
      if (!seen.has(key)) { seen.add(key); pairs.push({ ticker: r.ticker, pattern: r.pattern, key }); }
    }

    let cancelled = false;
    setWinRates(new Map());

    const fetchAll = async () => {
      const BATCH = 5;
      for (let i = 0; i < pairs.length && !cancelled; i += BATCH) {
        await Promise.all(
          pairs.slice(i, i + BATCH).map(async ({ ticker, pattern, key }) => {
            try {
              const data = await getSignalAnalysis(ticker, pattern, timeframe);
              if (!cancelled)
                setWinRates((prev) => new Map(prev).set(key, data.historical));
            } catch {
              // skip on error — non-critical
            }
          })
        );
      }
    };

    fetchAll();
    return () => { cancelled = true; };
    // timeframe is set atomically with results in handleResults, so results
    // is the only trigger that matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [results]);

  return (
    <div className="h-full flex flex-col bg-background overflow-hidden">
      {/* Mode toggle — Scanner (live signals) vs Backtest (historical study) */}
      <div className="flex items-center gap-1 px-4 pt-3 border-b border-border">
        {(['scanner', 'backtest'] as const).map((v) => (
          <button
            key={v}
            onClick={() => setView(v)}
            className={
              'px-3 py-1.5 text-sm border-b-2 -mb-px transition-colors ' +
              (view === v
                ? 'border-foreground text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground')
            }
          >
            {v === 'scanner' ? 'Scanner' : 'Backtest'}
          </button>
        ))}
      </div>

      {view === 'backtest' ? (
        <div className="flex-1 min-h-0 overflow-hidden">
          <PatternBacktestPanel />
        </div>
      ) : (
      <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-[320px_1fr] gap-4 p-3 md:p-4 overflow-y-auto md:overflow-hidden">
        {/* Left: scanner + stats */}
        <div className="space-y-4 md:overflow-y-auto md:pr-1">
          <ScannerPanel
            onResults={handleResults}
            isScanning={isScanning}
            setIsScanning={setIsScanning}
          />
          {results.length > 0 && (
            <QuickStats
              results={results}
              onTickerClick={(ticker) => setChart({ ticker, pattern: null, endDate: null })}
            />
          )}
        </div>

        {/* Right: results table */}
        <ResultsTable
          results={results}
          onRowClick={(row) =>
            setChart({ ticker: row.ticker, pattern: row.pattern, endDate: row.end_date })
          }
          winRates={winRates}
          timeframe={timeframe}
        />
      </div>
      )}

      {/* Chart modal — fixed overlay */}
      {chart && (
        <ChartModal
          ticker={chart.ticker}
          activePattern={chart.pattern}
          activeEndDate={chart.endDate}
          timeframe={timeframe}
          onClose={() => setChart(null)}
        />
      )}
    </div>
  );
}
