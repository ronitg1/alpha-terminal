import { useEffect, useState } from 'react';
import { getSignalAnalysis, getTradePlan } from '@/services/patterns-api';
import type {
  OptionsStrategy,
  PatternTimeframe,
  RiskTolerance,
  SignalAnalysisData,
  TradePlanResponse,
} from '@/types/patterns';

// ─── Trade plan: entry / stop / target sized to risk tolerance + ATR ─────────

const RISK_OPTIONS: { key: RiskTolerance; label: string }[] = [
  { key: 'conservative', label: 'Cons' },
  { key: 'moderate', label: 'Mod' },
  { key: 'aggressive', label: 'Aggr' },
];

function money(v: number | null | undefined): string {
  return v == null ? '—' : `$${v.toFixed(2)}`;
}

function expLabel(exp: string | null): string {
  if (!exp) return '';
  const [y, m, d] = exp.split('-');
  return `${Number(m)}/${Number(d)}/${y.slice(2)}`;
}

/** "+$611" / "−$611" — avoids the "+$-611" double-sign artifact. */
function signedMoney(v: number): string {
  return `${v < 0 ? '−' : '+'}$${Math.abs(v).toFixed(0)}`;
}

const STATUS_STYLES: Record<string, { label: string; cls: string }> = {
  live: { label: 'LIVE', cls: 'bg-emerald-900/40 text-emerald-400 border-emerald-700' },
  watch: { label: 'WATCH', cls: 'bg-amber-900/40 text-amber-400 border-amber-700' },
  stale: { label: 'STALE', cls: 'bg-gray-800 text-gray-500 border-gray-700' },
};

function StatusPill({ status }: { status?: string }) {
  const s = STATUS_STYLES[status ?? ''];
  if (!s) return null;
  return (
    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${s.cls}`}>{s.label}</span>
  );
}

export function TradePlanCard({
  ticker, pattern, timeframe, onPlan,
}: {
  ticker: string;
  pattern: string;
  timeframe: PatternTimeframe;
  /** Fires when the plan loads — lets a parent (e.g. the inline Contract
   *  panel) reuse the same recommended contract without a second fetch. */
  onPlan?: (data: TradePlanResponse) => void;
}) {
  const [risk, setRisk] = useState<RiskTolerance>('moderate');
  const [data, setData] = useState<TradePlanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Position sizer inputs (persist across risk changes within a session).
  const [account, setAccount] = useState('25000');
  const [riskPct, setRiskPct] = useState('1');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getTradePlan(ticker, pattern, risk, timeframe)
      .then((res) => { if (!cancelled) { setData(res); onPlan?.(res); } })
      .catch((err: Error) => { if (!cancelled) setError(err.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [ticker, pattern, risk, timeframe, onPlan]);

  const plan = data?.plan ?? null;
  const opt = data?.option ?? null;

  // Position sizing in CONTRACTS: lose ≈ account × risk% if the premium stop hits.
  let contracts: number | null = null;
  let dollarsAtRisk: number | null = null;
  if (opt && opt.risk_per_contract > 0) {
    const acct = parseFloat(account);
    const pct = parseFloat(riskPct);
    if (Number.isFinite(acct) && Number.isFinite(pct) && acct > 0 && pct > 0) {
      dollarsAtRisk = acct * (pct / 100);
      contracts = Math.floor(dollarsAtRisk / opt.risk_per_contract);
    }
  }

  const rrColor = (rr: number | null | undefined) =>
    (rr ?? 0) >= 2 ? 'text-emerald-400' : (rr ?? 0) >= 1 ? 'text-amber-400' : 'text-red-400';

  return (
    <div className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Trade Plan</p>
          <StatusPill status={plan?.status} />
        </div>
        <div className="flex gap-1">
          {RISK_OPTIONS.map((o) => (
            <button
              key={o.key}
              type="button"
              onClick={() => setRisk(o.key)}
              title={`${o.key} stop = ${o.key === 'conservative' ? '1.0' : o.key === 'moderate' ? '1.5' : '2.5'}× ATR on the underlying`}
              className={`px-2 py-0.5 text-[11px] rounded-md border transition-colors ${
                risk === o.key
                  ? 'bg-indigo-600 text-white border-indigo-500'
                  : 'bg-gray-800 text-gray-400 border-gray-700 hover:text-gray-200'
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <p className="text-xs text-gray-500 py-2">Sizing the play…</p>
      ) : error ? (
        <p className="text-xs text-red-400 py-2">{error}</p>
      ) : !plan ? (
        <p className="text-xs text-gray-600 py-2">
          No actionable signal in the recent window — the trade plan reflects the latest breakout, and none is fresh enough on this timeframe.
        </p>
      ) : (
        <div className="space-y-3">
          {opt ? (
            <>
              {plan.reanchored && (
                <p className="text-[11px] text-amber-500/90 leading-relaxed">
                  ↻ The original breakout already played out — this sizes a <em>fresh entry at the current price</em> toward the pattern&apos;s projected target.
                </p>
              )}
              {plan.status === 'watch' && (
                <p className="text-[11px] text-amber-500/90 leading-relaxed">
                  ⏳ {plan.status_reason}. Premiums below are estimates <em>at the trigger</em> — refresh when price approaches the level.
                </p>
              )}
              <div className="flex items-center gap-2 flex-wrap">
                <span className={`text-xs font-mono font-bold px-2 py-0.5 rounded border ${opt.type === 'call' ? 'bg-emerald-900/30 text-emerald-400 border-emerald-800' : 'bg-red-900/30 text-red-400 border-red-800'}`}>
                  {ticker} ${opt.strike}{opt.type === 'call' ? 'C' : 'P'} {expLabel(opt.expiration)}
                </span>
                <span className="text-[11px] text-gray-500">{opt.dte}d{opt.iv_pct != null && ` · IV ${opt.iv_pct}%`}{opt.delta != null && ` · Δ ${opt.delta}`}</span>
                <span className="text-[11px] text-gray-600">mid {money(opt.current_mid)}</span>
              </div>

              {/* Premium entry / stop / target */}
              <div className="grid grid-cols-3 gap-2">
                <div className="bg-gray-900/60 rounded-lg px-2 py-1.5">
                  <div className="text-sm font-bold font-mono text-white">{money(opt.entry_premium)}</div>
                  <div className="text-[10px] text-gray-600">Buy at {plan.reanchored ? 'current' : plan.already_triggered ? '(triggered)' : 'breakout'}</div>
                </div>
                <div className="bg-gray-900/60 rounded-lg px-2 py-1.5">
                  <div className="text-sm font-bold font-mono text-red-400">{money(opt.stop_premium)}</div>
                  <div className="text-[10px] text-gray-600">Cut ({signedMoney(-opt.risk_per_contract)}/ct)</div>
                </div>
                <div className={`rounded-lg px-2 py-1.5 ${opt.viable ? 'bg-gray-900/60' : 'bg-red-950/40 border border-red-900/50'}`}>
                  <div className={`text-sm font-bold font-mono ${opt.viable ? 'text-emerald-400' : 'text-red-400'}`}>{money(opt.target_premium)}</div>
                  <div className="text-[10px] text-gray-600">At target ({signedMoney(opt.reward_per_contract)}/ct)</div>
                </div>
              </div>

              {/* Theta-negative: even a winning pattern loses on this contract. */}
              {!opt.viable && (
                <p className="text-[11px] text-red-400/90 leading-relaxed">
                  ⚠ Not viable as a long option: over the expected hold, theta decay outruns the pattern&apos;s
                  measured move — even if the underlying reaches the target, this contract is worth less than
                  entry (no longer-dated expiry cleared theta either). Consider trading the underlying or a spread.
                </p>
              )}

              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                <span className="text-gray-500">
                  R/R <span className={`font-mono font-bold ${rrColor(opt.risk_reward)}`}>
                    {opt.risk_reward != null ? `${opt.risk_reward}:1` : '—'}
                  </span>
                </span>
                <span className="text-gray-500">max loss <span className="font-mono text-gray-300">${opt.max_loss_per_contract.toFixed(0)}/ct</span></span>
                {data?.atr != null && (
                  <span className="text-gray-500">ATR <span className="font-mono text-gray-300">${data.atr}{data.atr_pct != null ? ` (${data.atr_pct}%)` : ''}</span></span>
                )}
                {data?.hist_vol_annual_pct != null && (
                  <span className="text-gray-500">vol <span className="font-mono text-gray-300">{data.hist_vol_annual_pct}%</span></span>
                )}
              </div>

              {/* Underlying levels driving the premium plan */}
              <div className="text-[11px] text-gray-600 space-y-0.5 leading-relaxed">
                <p>
                  Underlying: enter {money(plan.entry)} · stop {money(plan.stop)} ({plan.stop_basis}) · target {money(plan.target)}.
                  {plan.structural_invalidation != null && ` Pattern invalidates near ${money(plan.structural_invalidation)}.`}
                </p>
                <p>Premiums: {opt.pricing_basis}.</p>
              </div>

              {/* Position sizer — contracts (suppressed when theta-negative) */}
              {opt.viable && (
              <div className="bg-gray-900/40 rounded-lg p-2.5">
                <div className="flex items-center gap-2 flex-wrap text-[11px] text-gray-500">
                  <span>Account $</span>
                  <input
                    value={account}
                    onChange={(e) => setAccount(e.target.value)}
                    className="bg-gray-800 border border-gray-700 rounded px-1.5 py-0.5 w-20 text-gray-200 font-mono outline-none focus:border-indigo-500"
                  />
                  <span>risk %</span>
                  <input
                    value={riskPct}
                    onChange={(e) => setRiskPct(e.target.value)}
                    className="bg-gray-800 border border-gray-700 rounded px-1.5 py-0.5 w-12 text-gray-200 font-mono outline-none focus:border-indigo-500"
                  />
                </div>
                {contracts != null && dollarsAtRisk != null && (
                  <p className="text-xs text-gray-300 mt-1.5">
                    ≈ <span className="font-mono font-bold text-white">{contracts.toLocaleString()} contract{contracts === 1 ? '' : 's'}</span>
                    <span className="text-gray-600">
                      {' '}· ~${(contracts * opt.entry_premium * 100).toLocaleString(undefined, { maximumFractionDigits: 0 })} premium
                      · risks ~${(contracts * opt.risk_per_contract).toFixed(0)} at the cut
                      · ${(contracts * opt.max_loss_per_contract).toLocaleString(undefined, { maximumFractionDigits: 0 })} max if it expires worthless
                    </span>
                  </p>
                )}
                {contracts === 0 && (
                  <p className="text-[11px] text-amber-500/90 mt-1.5">
                    One contract risks more than your budget — raise risk % or skip the play.
                  </p>
                )}
              </div>
              )}
            </>
          ) : plan.status === 'stale' ? (
            /* Played out AND no chain to re-anchor against — say so plainly. */
            <div className="space-y-2">
              <p className="text-xs text-gray-400 leading-relaxed">{plan.status_reason}</p>
              <p className="text-[11px] text-gray-600 leading-relaxed">
                No option chain available to re-anchor a fresh entry. Run a fresh scan for setups that are still in play.
              </p>
            </div>
          ) : (
            <>
              {/* Chain unavailable — show the underlying levels so the plan is still usable. */}
              <p className="text-[11px] text-amber-500/90">
                Option chain unavailable for this name right now — showing underlying levels.
              </p>
              <div className="grid grid-cols-3 gap-2">
                <div className="bg-gray-900/60 rounded-lg px-2 py-1.5">
                  <div className="text-sm font-bold font-mono text-white">{money(plan.entry)}</div>
                  <div className="text-[10px] text-gray-600">Entry {plan.already_triggered && '· triggered'}</div>
                </div>
                <div className="bg-gray-900/60 rounded-lg px-2 py-1.5">
                  <div className="text-sm font-bold font-mono text-red-400">{money(plan.stop)}</div>
                  <div className="text-[10px] text-gray-600">Stop {plan.stop_pct > 0 ? '+' : ''}{plan.stop_pct}%</div>
                </div>
                <div className="bg-gray-900/60 rounded-lg px-2 py-1.5">
                  <div className="text-sm font-bold font-mono text-emerald-400">{money(plan.target)}</div>
                  <div className="text-[10px] text-gray-600">Target {plan.target_pct > 0 ? '+' : ''}{plan.target_pct}%</div>
                </div>
              </div>
              <div className="text-[11px] text-gray-600 leading-relaxed">
                <p>Stop: {plan.stop_basis}. Target: {plan.target_basis}.</p>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

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

      {/* Trade plan — entry / stop / target sized to risk + volatility */}
      {pattern && <TradePlanCard ticker={ticker} pattern={pattern} timeframe={timeframe} />}

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
