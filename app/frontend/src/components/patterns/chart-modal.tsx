import { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, CrosshairMode, LineStyle } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import { getChart } from '@/services/patterns-api';
import { SignalAnalysis } from './signal-analysis';
import type { ChartData, PatternTimeframe } from '@/types/patterns';

/** Chart x-axis value for a backend bar label.
 *
 * Daily bars are `YYYY-MM-DD` strings (lightweight-charts business days).
 * Intraday bars are `YYYY-MM-DDTHH:MM` in US-Eastern wall-clock; converting
 * them as-if-UTC makes the chart's UTC clock display the ET wall time —
 * exactly what a US-equity trader expects to read. */
function toChartTime(date: string): Time {
  if (date.includes('T')) {
    return Math.floor(Date.parse(`${date}:00Z`) / 1000) as Time;
  }
  return date as Time;
}

/** Per-timeframe chart history window (server clamps further). */
const CHART_LOOKBACK: Record<PatternTimeframe, number> = {
  week: 1095, // 3y of weekly bars
  day: 365,
  '1h': 90,
  '15m': 30,
};

const BULLISH_PATTERNS = new Set([
  'Bullish Flag', 'Bull Pennant', 'Double Bottom', 'Inverse Head and Shoulders',
  'Ascending Triangle', 'Cup and Handle', 'Falling Wedge',
]);

const CHART_THEME = {
  layout: { background: { type: ColorType.Solid, color: '#0f1117' }, textColor: '#9ca3af' },
  grid: { vertLines: { color: '#1f2937' }, horzLines: { color: '#1f2937' } },
  rightPriceScale: { borderColor: '#374151' },
  timeScale: { borderColor: '#374151', timeVisible: false },
};

interface OHLCInfo {
  open: number;
  high: number;
  low: number;
  close: number;
}

interface ChartModalProps {
  ticker: string;
  activePattern: string | null;
  activeEndDate: string | null;
  timeframe?: PatternTimeframe;
  onClose: () => void;
}

export function ChartModal({ ticker, activePattern, activeEndDate, timeframe = 'day', onClose }: ChartModalProps) {
  const priceRef = useRef<HTMLDivElement>(null);
  const volRef = useRef<HTMLDivElement>(null);
  const priceChartRef = useRef<IChartApi | null>(null);
  const volChartRef = useRef<IChartApi | null>(null);
  const [chartData, setChartData] = useState<ChartData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hoverInfo, setHoverInfo] = useState<OHLCInfo | null>(null);

  // Close on Escape
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Build charts
  useEffect(() => {
    if (!ticker || !priceRef.current || !volRef.current) return;

    let destroyed = false;

    const init = async () => {
      setLoading(true);
      setError(null);

      try {
        const data = await getChart(ticker, CHART_LOOKBACK[timeframe], timeframe);
        if (destroyed) return;
        setChartData(data);

        // Weekly + daily are date-labeled; only sub-daily needs HH:MM + intraday scaling.
        const intraday = timeframe === '1h' || timeframe === '15m';

        // ── Price chart ──────────────────────────────────────────
        const priceChart = createChart(priceRef.current!, {
          ...CHART_THEME,
          width: priceRef.current!.clientWidth,
          height: priceRef.current!.clientHeight,
          crosshair: { mode: CrosshairMode.Normal },
          timeScale: { ...CHART_THEME.timeScale, timeVisible: intraday },
        });
        priceChartRef.current = priceChart;

        const candleSeries: ISeriesApi<'Candlestick'> = priceChart.addCandlestickSeries({
          upColor: '#22c55e',
          downColor: '#ef4444',
          borderVisible: false,
          wickUpColor: '#22c55e',
          wickDownColor: '#ef4444',
        });

        candleSeries.setData(
          data.candles.map((c) => ({
            time: toChartTime(c.date),
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
          }))
        );

        // ── Pattern markers ──────────────────────────────────────
        const markers: Array<{
          time: Time;
          position: 'belowBar' | 'aboveBar';
          color: string;
          shape: 'arrowUp' | 'arrowDown' | 'circle';
          text: string;
          size: number;
        }> = [];

        for (const p of data.patterns) {
          const bullish = BULLISH_PATTERNS.has(p.pattern);
          const isActive =
            p.pattern === activePattern && (!activeEndDate || p.end_date === activeEndDate);
          const color = bullish ? '#22c55e' : '#ef4444';
          const dimColor = bullish ? '#15803d' : '#991b1b';

          markers.push({
            time: toChartTime(p.start_date),
            position: bullish ? 'belowBar' : 'aboveBar',
            color: isActive ? color : dimColor,
            shape: bullish ? 'arrowUp' : 'arrowDown',
            text: p.pattern.substring(0, 5),
            size: isActive ? 2 : 1,
          });
          markers.push({
            time: toChartTime(p.end_date),
            position: bullish ? 'aboveBar' : 'belowBar',
            color: isActive ? color : dimColor,
            shape: 'circle',
            text: `${Math.round(p.confidence)}%`,
            size: isActive ? 2 : 1,
          });

          // Key level price lines — only for the active signal
          if (isActive) {
            for (const [levelName, levelVal] of Object.entries(p.key_levels ?? {})) {
              if (typeof levelVal !== 'number' || levelVal <= 0) continue;
              candleSeries.createPriceLine({
                price: levelVal,
                color,
                lineWidth: 2,
                lineStyle: LineStyle.Dashed,
                axisLabelVisible: true,
                title: levelName.replace(/_/g, ' '),
              });
            }
          }
        }

        // Deduplicate markers at same time+position
        const seen = new Set<string>();
        const dedupedMarkers = markers.filter((m) => {
          const key = `${m.time}|${m.position}|${m.shape}`;
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
        dedupedMarkers.sort((a, b) => (a.time < b.time ? -1 : 1));
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        candleSeries.setMarkers(dedupedMarkers as any);

        // ── Active pattern trendlines ────────────────────────────
        const activePat = data.patterns.find(
          (p) =>
            p.pattern === activePattern &&
            (!activeEndDate || p.end_date === activeEndDate)
        );
        if (activePat?.trendlines?.length) {
          const lineColor = BULLISH_PATTERNS.has(activePat.pattern) ? '#22c55e' : '#ef4444';
          for (const tl of activePat.trendlines) {
            if (
              !tl.time_start || !tl.time_end ||
              tl.time_start === tl.time_end ||
              tl.value_start == null || tl.value_end == null
            ) continue;
            const ls = priceChart.addLineSeries({
              color: lineColor,
              lineWidth: 2,
              lineStyle: LineStyle.Solid,
              crosshairMarkerVisible: false,
              priceLineVisible: false,
              lastValueVisible: false,
            });
            ls.setData([
              { time: toChartTime(tl.time_start), value: tl.value_start },
              { time: toChartTime(tl.time_end), value: tl.value_end },
            ]);
          }
        }

        // Crosshair hover info
        priceChart.subscribeCrosshairMove((param) => {
          if (!param.time || !param.seriesData) { setHoverInfo(null); return; }
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const bar = param.seriesData.get(candleSeries) as any;
          if (bar && 'open' in bar) setHoverInfo(bar as OHLCInfo);
          else setHoverInfo(null);
        });

        // ── Volume chart ─────────────────────────────────────────
        const volChart = createChart(volRef.current!, {
          ...CHART_THEME,
          width: volRef.current!.clientWidth,
          height: volRef.current!.clientHeight,
          timeScale: { ...CHART_THEME.timeScale, timeVisible: true },
          rightPriceScale: {
            ...CHART_THEME.rightPriceScale,
            scaleMargins: { top: 0.1, bottom: 0 },
          },
        });
        volChartRef.current = volChart;

        const volSeries = volChart.addHistogramSeries({
          priceFormat: { type: 'volume' },
          priceScaleId: 'vol',
          color: '#4b5563',
        });
        volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0 } });
        volSeries.setData(
          data.candles.map((c) => ({
            time: toChartTime(c.date),
            value: c.volume || 0,
            color: c.close >= c.open ? '#22c55e55' : '#ef444455',
          }))
        );

        // ── Sync time scales ──────────────────────────────────────
        let syncingPrice = false, syncingVol = false;
        priceChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
          if (syncingVol || !range) return;
          syncingPrice = true;
          volChart.timeScale().setVisibleLogicalRange(range);
          syncingPrice = false;
        });
        volChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
          if (syncingPrice || !range) return;
          syncingVol = true;
          priceChart.timeScale().setVisibleLogicalRange(range);
          syncingVol = false;
        });

        priceChart.timeScale().fitContent();

      } catch (err) {
        if (!destroyed) setError((err as Error).message);
      } finally {
        if (!destroyed) setLoading(false);
      }
    };

    init();

    return () => {
      destroyed = true;
      priceChartRef.current?.remove();
      volChartRef.current?.remove();
      priceChartRef.current = null;
      volChartRef.current = null;
    };
  }, [ticker, activePattern, activeEndDate, timeframe]);

  // Handle window resize
  useEffect(() => {
    const onResize = () => {
      if (priceRef.current && priceChartRef.current)
        priceChartRef.current.applyOptions({ width: priceRef.current.clientWidth });
      if (volRef.current && volChartRef.current)
        volChartRef.current.applyOptions({ width: volRef.current.clientWidth });
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const bullish = activePattern ? BULLISH_PATTERNS.has(activePattern) : false;
  const patternColor = activePattern ? (bullish ? 'text-emerald-400' : 'text-red-400') : '';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="bg-gray-900 border border-gray-700 rounded-2xl flex flex-col overflow-hidden shadow-2xl"
        style={{ width: '95vw', maxWidth: '1400px', height: '85vh' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 flex-shrink-0">
          <div className="flex items-center gap-4">
            <div>
              <h2 className="text-xl font-bold text-white font-mono">{ticker}</h2>
              {activePattern && (
                <p className={`text-sm font-medium ${patternColor}`}>
                  {bullish ? '▲' : '▼'} {activePattern}
                </p>
              )}
            </div>
            {hoverInfo && (
              <div className="flex gap-4 text-xs font-mono bg-gray-800 rounded-lg px-3 py-1.5 border border-gray-700">
                {([
                  { label: 'O', val: hoverInfo.open, color: 'text-gray-300' },
                  { label: 'H', val: hoverInfo.high, color: 'text-emerald-400' },
                  { label: 'L', val: hoverInfo.low, color: 'text-red-400' },
                  {
                    label: 'C',
                    val: hoverInfo.close,
                    color: hoverInfo.close >= hoverInfo.open ? 'text-emerald-400' : 'text-red-400',
                  },
                ] as const).map(({ label, val, color }) => (
                  <span key={label}>
                    <span className="text-gray-600">{label}: </span>
                    <span className={color}>{typeof val === 'number' ? val.toFixed(2) : '—'}</span>
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="flex items-center gap-3">
            {chartData && chartData.patterns.length > 0 && (
              <span className="text-xs text-gray-500 hidden sm:block">
                {chartData.patterns.length} pattern{chartData.patterns.length !== 1 ? 's' : ''} detected
              </span>
            )}
            <button
              onClick={onClose}
              className="w-8 h-8 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-white transition-colors flex items-center justify-center text-sm"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Chart area + analysis panel */}
        <div className="flex-1 flex min-h-0">
          {/* Charts column */}
          <div className="flex-1 flex flex-col min-h-0 relative">
            {(loading || error) && (
              <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-gray-900">
                {loading ? (
                  <>
                    <svg className="animate-spin h-8 w-8 text-indigo-500 mb-3" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                    <span className="text-sm text-gray-500">Loading {ticker} chart data…</span>
                  </>
                ) : (
                  <>
                    <svg className="w-8 h-8 opacity-70 text-red-400 mb-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
                      <path strokeLinecap="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
                    </svg>
                    <span className="text-sm text-red-400">{error}</span>
                  </>
                )}
              </div>
            )}
            <div ref={priceRef} style={{ flex: 7 }} className="min-h-0" />
            <div ref={volRef} style={{ flex: 3 }} className="min-h-0 border-t border-gray-800" />
          </div>

          {/* Signal analysis side panel */}
          {activePattern && (
            <div className="w-80 flex-shrink-0 border-l border-gray-800 flex flex-col min-h-0">
              <div className="px-4 py-3 border-b border-gray-800 flex-shrink-0">
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Signal Analysis</p>
              </div>
              <div className="flex-1 min-h-0 overflow-y-auto p-4">
                <SignalAnalysis ticker={ticker} pattern={activePattern} timeframe={timeframe} />
              </div>
            </div>
          )}
        </div>

        {/* Footer: key levels */}
        {!loading && !error && chartData && activePattern && (() => {
          const active = chartData.patterns.find((p) => p.pattern === activePattern);
          if (!active || Object.keys(active.key_levels ?? {}).length === 0) return null;
          return (
            <div className="flex-shrink-0 border-t border-gray-800 px-6 py-3 flex items-center gap-6 bg-gray-900/50">
              <span className="text-xs text-gray-500 font-semibold uppercase tracking-wider">Key Levels</span>
              {Object.entries(active.key_levels).map(([k, v]) => (
                <div key={k} className="text-xs">
                  <span className="text-gray-500">{k.replace(/_/g, ' ')}: </span>
                  <span className="font-mono text-gray-200">{typeof v === 'number' ? v.toFixed(2) : String(v)}</span>
                </div>
              ))}
            </div>
          );
        })()}
      </div>
    </div>
  );
}
