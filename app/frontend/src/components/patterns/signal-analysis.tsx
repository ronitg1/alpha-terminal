import { useEffect, useState } from 'react';
import { getSignalAnalysis } from '@/services/patterns-api';
import type { OptionsStrategy, PatternTimeframe, SignalAnalysisData } from '@/types/patterns';

function WinRateGauge({ rate }: { rate: number | null }) {
  const r = 36;
  const circ = 2 * Math.PI * r;
  const filled = rate != null ? (rate / 100) * circ : 0;
  const color =
    rate == null ? '#4b5563' : rate >= 60 ? '#22c55e' : rate >= 45 ? '#f59e0b' : '#ef4444';

  return (
    <div className="flex flex-col items-center gap-1">
      <svg width="96" height="96" viewBox="0 0 96 96">
        <circle cx="48" cy="48" r={r} fill="none" stroke="#1f2937" strokeWidth="8" />
        <circle
          cx="48" cy="48" r={r}
          fill="none"
          stroke={color}
          strokeWidth="8"
          strokeDasharray={`${filled} ${circ}`}
          strokeLinecap="round"
          transform="rotate(-90 48 48)"
          style={{ transition: 'stroke-dasharray 0.6s ease' }}
        />
        <text x="48" y="44" textAnchor="middle" fill="white" fontSize="14" fontWeight="bold" fontFamily="monospace">
          {rate != null ? `${rate}%` : '—'}
        </text>
        <text x="48" y="58" textAnchor="middle" fill="#6b7280" fontSize="9" fontFamily="sans-serif">
          WIN RATE
        </text>
      </svg>
    </div>
  );
}

const GRADE_COLOR: Record<string, string> = {
  A: 'text-emerald-400 border-emerald-600',
  'B+': 'text-indigo-400 border-indigo-600',
  B: 'text-amber-400 border-amber-600',
};

function StrategyCard({ s }: { s: OptionsStrategy }) {
  const gc = GRADE_COLOR[s.grade] ?? 'text-gray-400 border-gray-600';
  return (
    <div className="bg-gray-800/60 border border-gray-700/60 rounded-xl p-3 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-white">{s.name}</span>
        <span className={`text-xs font-bold font-mono border rounded px-1.5 py-0.5 ${gc}`}>{s.grade}</span>
      </div>
      <p className="text-xs font-mono text-indigo-300 bg-gray-900/60 rounded px-2 py-1">{s.structure}</p>
      <p className="text-xs text-gray-400 leading-relaxed">{s.rationale}</p>
      <div className="flex gap-3 text-xs text-gray-500">
        <span><span className="text-gray-600">R/R </span>{s.risk_reward}</span>
        <span><span className="text-gray-600">IV rank </span>{s.ideal_iv_rank}</span>
      </div>
    </div>
  );
}

interface SignalAnalysisProps {
  ticker: string;
  pattern: string | null;
  timeframe?: PatternTimeframe;
}

export function SignalAnalysis({ ticker, pattern, timeframe = 'day' }: SignalAnalysisProps) {
  const [data, setData] = useState<SignalAnalysisData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ticker || !pattern) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);

    getSignalAnalysis(ticker, pattern, timeframe)
      .then((res) => { if (!cancelled) setData(res); })
      .catch((err: Error) => { if (!cancelled) setError(err.message); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [ticker, pattern, timeframe]);

  if (!pattern) {
    return (
      <div className="flex items-center justify-center h-full text-xs text-gray-600 text-center px-4">
        Click a row in the table to see signal analysis
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full gap-2 text-xs text-gray-500">
        <svg className="animate-spin h-4 w-4 text-indigo-500" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        Analyzing…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-xs text-red-400 px-4 text-center">{error}</div>
    );
  }

  if (!data) return null;

  const { historical: h, options, bullish } = data;
  const winRateColor =
    h.win_rate == null ? 'text-gray-400'
    : h.win_rate >= 60 ? 'text-emerald-400'
    : h.win_rate >= 45 ? 'text-amber-400'
    : 'text-red-400';

  return (
    <div className="flex flex-col gap-4 overflow-y-auto h-full pr-1">
      {/* Historical performance */}
      <div className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-4">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Historical Performance</p>
        <div className="flex items-center gap-4">
          <WinRateGauge rate={h.win_rate} />
          <div className="flex-1 space-y-2">
            <div className="grid grid-cols-2 gap-2">
              {[
                { label: 'Signals', val: String(h.total_signals), color: 'text-white' },
                { label: 'Win Rate', val: h.win_rate != null ? `${h.win_rate}%` : '—', color: winRateColor },
                { label: 'Avg Win', val: h.avg_win_pct != null ? `+${h.avg_win_pct}%` : '—', color: 'text-emerald-400' },
                { label: 'Avg Loss', val: h.avg_loss_pct != null ? `${h.avg_loss_pct}%` : '—', color: 'text-red-400' },
              ].map(({ label, val, color }) => (
                <div key={label} className="bg-gray-900/60 rounded-lg px-2 py-1.5">
                  <div className={`text-sm font-bold font-mono ${color}`}>{val}</div>
                  <div className="text-xs text-gray-600">{label}</div>
                </div>
              ))}
            </div>
            {h.total_signals > 0 && (
              <div className="flex rounded-full overflow-hidden h-2 mt-1">
                <div className="bg-emerald-500 transition-all" style={{ width: `${(h.wins / h.total_signals) * 100}%` }} />
                <div className="bg-red-500 flex-1" />
              </div>
            )}
            <p className="text-xs text-gray-600">
              {h.wins}W / {h.losses}L over {h.outcome_bars}-bar windows · &ge;{h.win_threshold_pct}% MFE = win
            </p>
          </div>
        </div>
      </div>

      {/* Options plays */}
      <div>
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
          {bullish ? '▲' : '▼'} Options Plays
        </p>
        <div className="space-y-2">
          {options.map((s) => <StrategyCard key={s.name} s={s} />)}
        </div>
      </div>

      <p className="text-xs text-gray-700 pb-2">
        Not financial advice. Past performance does not guarantee future results.
      </p>
    </div>
  );
}
