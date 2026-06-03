/**
 * OptionsBacktestPanel — D2 frontend, generalised to all 10 backtestable
 * strategies (everything except Unusual Options Activity, which needs
 * historical option-chain data we don't have on the current plan).
 *
 * Form: date range, strategy picker (from /sleeves/options/strategies),
 * sleeve picker, optional ticker subset, direction (auto/straddle/calls/
 * puts), conviction min, hold days.
 *
 * Run streams the SSE backtest; we show per-trade rows live, then a full
 * summary block once 'complete' arrives.
 *
 * Headline visualisations once complete:
 *   - Win/loss split bar
 *   - Cumulative-PnL line chart over trade index
 *   - PnL-per-trade histogram
 *   - By-conviction-tier breakdown table
 *   - Full trades table (scrollable)
 */

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { cn } from '@/lib/utils';
import { postSse, sleevesApi } from '@/services/sleeves-api';
import {
  OptionsBacktestSummary,
  OptionsBacktestTrade,
  OptionsStrategyMeta,
} from '@/types/sleeves';
import { Info, Play, Square } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Histogram } from '../charts/histogram';
import { LineChart, LinePoint } from '../charts/line-chart';
import { WinLossBar } from '../charts/win-loss-bar';

type RunStatus = 'idle' | 'running' | 'done' | 'error';
type Direction = 'auto' | 'straddle' | 'calls' | 'puts';
type Pricing = 'real' | 'bsm';

// Display order + styling for the exit-reason breakdown and per-trade tag.
const EXIT_REASON_ORDER = ['target', 'stop', 'dte', 'expiry', 'time'] as const;
const EXIT_REASON_META: Record<string, { label: string; tip: string; cls: string }> = {
  target: {
    label: 'Profit target',
    tip: 'Closed when the premium hit the profit-target gain.',
    cls: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
  },
  stop: {
    label: 'Stop-loss',
    tip: 'Closed when the premium fell to the stop threshold.',
    cls: 'border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-400',
  },
  dte: {
    label: 'DTE roll',
    tip: 'Closed at the days-to-expiry threshold to avoid the gamma/theta cliff.',
    cls: 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400',
  },
  expiry: {
    label: 'Expiry',
    tip: 'Held to expiration and settled at intrinsic value.',
    cls: 'border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-400',
  },
  time: {
    label: 'Hold backstop',
    tip: 'Hit the max hold-days backstop — no other trigger fired.',
    cls: 'border-border bg-muted/40 text-muted-foreground',
  },
};

function isoNDaysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

// Render the exact contract a trade entered, e.g. "470C 2026-06-20" (calls),
// "200P 2026-07-18" (puts), or "470±  2026-06-20" (straddle = both legs).
function contractLabel(t: OptionsBacktestTrade): string {
  const code = t.direction === 'calls' ? 'C' : t.direction === 'puts' ? 'P' : '±';
  const strike = t.strike.toFixed(0);
  const exp = t.contract_expiry ? ` ${t.contract_expiry}` : '';
  return `${strike}${code}${exp}`;
}

export function OptionsBacktestPanel() {
  const { config } = useSleevesContext();

  const [strategies, setStrategies] = useState<OptionsStrategyMeta[]>([]);
  // Default to ~6 months so the window spans more than one regime — a 1-2 month
  // window overfits to whatever the market just did and produces unrealistic
  // (often 100%) win rates.
  const [startDate, setStartDate] = useState(isoNDaysAgo(180));
  const [endDate, setEndDate] = useState(isoNDaysAgo(0));
  const [sleeve, setSleeve] = useState<string>('mega_tech');
  const [tickerInput, setTickerInput] = useState('');
  const [strategy, setStrategy] = useState<string>('weakness');
  const [direction, setDirection] = useState<Direction>('auto');
  // Conviction gate is now percentage-based (magnitude-weighted 0-100).
  const [minConvictionPct, setMinConvictionPct] = useState(40);
  // hold_days is now the max-hold backstop, not a fixed exit. Realistic exits
  // (target / stop / DTE) close most trades before this.
  const [holdDays, setHoldDays] = useState(30);
  const [pricing, setPricing] = useState<Pricing>('real');
  // Exit policy — all stored as positive fractions (0.5 = 50%). null = off.
  const [stopLossPct, setStopLossPct] = useState<number | null>(0.5);
  const [profitTargetPct, setProfitTargetPct] = useState<number | null>(0.5);
  // DTE-based exit: close when contract reaches this many days-to-expiry. null = off.
  const [dteExit, setDteExit] = useState<number | null>(21);
  // Transaction-cost model — round-trip spread as a fraction of premium. null = frictionless.
  const [slippagePct, setSlippagePct] = useState<number | null>(0.05);

  const [status, setStatus] = useState<RunStatus>('idle');
  const [progress, setProgress] = useState<string>('');
  const [trades, setTrades] = useState<OptionsBacktestTrade[]>([]);
  const [summary, setSummary] = useState<OptionsBacktestSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  // Strategies catalog — same endpoint the Options Screener uses, filtered
  // to backtestable ones (server already excludes UOA from the registry
  // for backtest; we display all and surface the error if user picks UOA).
  useEffect(() => {
    void sleevesApi
      .getOptionsStrategies()
      .then((r) => {
        setStrategies(r.strategies);
      })
      .catch((err) => {
        console.error('Failed to load strategies:', err);
      });
  }, []);

  const activeStrategyMeta = useMemo(
    () => strategies.find((s) => s.key === strategy),
    [strategies, strategy],
  );

  // Cumulative P&L per-trade-index for the line chart.
  const cumulativePoints: LinePoint[] = useMemo(() => {
    let cum = 0;
    return trades.map((t, i) => {
      cum += t.pnl;
      return { x: `#${i + 1}`, y: cum };
    });
  }, [trades]);

  const handleRun = async () => {
    if (status === 'running') return;
    setStatus('running');
    setProgress('Starting…');
    setTrades([]);
    setSummary(null);
    setError(null);

    const tickers = tickerInput
      .split(/[,\s]+/)
      .map((t) => t.trim().toUpperCase())
      .filter(Boolean);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      await postSse(
        '/sleeves/backtest/options-strategy',
        {
          start_date: startDate,
          end_date: endDate,
          sleeve,
          tickers: tickers.length ? tickers : null,
          strategy,
          direction,
          min_conviction_pct: minConvictionPct,
          hold_days: holdDays,
          pricing,
          stop_loss_pct: stopLossPct,
          profit_target_pct: profitTargetPct,
          dte_exit: dteExit,
          slippage_pct: slippagePct,
        },
        (event, data) => {
          if (event === 'progress') {
            const d = data as { status?: string };
            if (d.status) setProgress(d.status);
          } else if (event === 'sleeve_complete') {
            const payload = data as { sleeve: string; rows: OptionsBacktestTrade[] };
            if (payload.sleeve === 'trade' && Array.isArray(payload.rows)) {
              setTrades((prev) => [...prev, ...payload.rows]);
            }
          } else if (event === 'complete') {
            const payload = (data as { data: OptionsBacktestSummary }).data;
            setSummary(payload);
            setTrades(payload.trades ?? []);
            setStatus('done');
            setProgress(`Complete — ${payload.n_trades} trades simulated`);
          } else if (event === 'error') {
            const d = data as { message?: string };
            setError(d.message ?? 'Backtest error');
            setStatus('error');
          }
        },
        ctrl.signal,
      );
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        setStatus('idle');
        setProgress('Cancelled');
        return;
      }
      setError(err instanceof Error ? err.message : String(err));
      setStatus('error');
    } finally {
      if (abortRef.current === ctrl) abortRef.current = null;
    }
  };

  const handleStop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
  };

  const wins = summary?.n_wins ?? 0;
  const losses = (summary?.n_trades ?? 0) - wins;
  const breakevens = trades.filter((t) => t.pnl === 0).length;
  // Adjust losses to exclude break-evens.
  const lossesAdj = Math.max(0, losses - breakevens);

  return (
    <div className="space-y-5">
      <div className="rounded-md border border-sky-500/30 bg-sky-500/5 p-3 text-xs flex items-start gap-2">
        <Info className="h-3.5 w-3.5 text-sky-600 dark:text-sky-400 flex-shrink-0 mt-0.5" />
        <div>
          <strong className="text-sky-700 dark:text-sky-400">Pricing model:</strong>{' '}
          {pricing === 'real' ? (
            <>
              Real Polygon historical fills — picks the listed contract closest to
              the strategy's target strike + expiry (~2.5× hold-days out), then
              entry/exit at the actual daily close. Falls back to BSM per-trade
              when a contract or bar is missing. Daily-close fills don't model
              bid/ask spread; the Polygon Advanced tier would be needed for NBBO.
            </>
          ) : (
            <>
              Black-Scholes proxy — premiums priced against the underlying's
              trailing 30-day realized vol. Deterministic, no API calls.
              Useful for ranking; not a substitute for a live-quote backtest.
            </>
          )}
          <div className="mt-1 text-foreground/80">
            <strong>Exit model:</strong> each trade is checked every day and
            closes on the first trigger — profit target, stop-loss, DTE roll-out,
            or the hold-days backstop. When a day could hit both the stop and the
            target, the stop is assumed first (conservative, biases returns
            slightly down). In BSM mode the DTE exit is approximate (expiry is
            synthesized from the contract's target DTE).
          </div>
          {activeStrategyMeta && (
            <div className="mt-1 text-foreground/80">
              <strong>Active strategy ({activeStrategyMeta.label}):</strong>{' '}
              {activeStrategyMeta.description}
            </div>
          )}
        </div>
      </div>

      {/* Pricing-mode pill toggle + Stop-loss. Pricing is the highest-impact
          knob for backtest interpretation; stop-loss sits next to it because
          it's a risk-management mirror — both shape the realized P&L curve. */}
      <div className="flex flex-wrap items-center gap-4 text-xs">
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground uppercase tracking-wide text-[10px]">
            Pricing:
          </span>
          <div className="inline-flex rounded-md border border-border overflow-hidden">
            <button
              type="button"
              onClick={() => setPricing('real')}
              className={cn(
                'px-3 py-1 font-mono',
                pricing === 'real'
                  ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              Real (Polygon)
            </button>
            <button
              type="button"
              onClick={() => setPricing('bsm')}
              className={cn(
                'px-3 py-1 font-mono border-l border-border',
                pricing === 'bsm'
                  ? 'bg-sky-500/10 text-sky-700 dark:text-sky-400'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              BSM proxy
            </button>
          </div>
        </div>

        <div
          className="flex items-center gap-2"
          title={
            'Take-profit. Closes on the first day the option premium closes at ' +
            'or above entry × (1 + target %). Straddles use combined premium. ' +
            'Off = no target.'
          }
        >
          <span className="text-muted-foreground uppercase tracking-wide text-[10px]">
            Profit target:
          </span>
          <div className="inline-flex rounded-md border border-border overflow-hidden font-mono">
            {([
              { label: 'Off', value: null },
              { label: '+25%', value: 0.25 },
              { label: '+50%', value: 0.5 },
              { label: '+100%', value: 1.0 },
            ] as { label: string; value: number | null }[]).map((opt, i) => (
              <button
                key={opt.label}
                type="button"
                onClick={() => setProfitTargetPct(opt.value)}
                className={cn(
                  'px-2.5 py-1',
                  i > 0 && 'border-l border-border',
                  profitTargetPct === opt.value
                    ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        <div
          className="flex items-center gap-2"
          title={
            'Per-contract stop-loss. Exits early on the first day the option ' +
            'premium closes at or below entry × (1 − stop %). Straddles stop ' +
            'on combined premium. Off = no stop.'
          }
        >
          <span className="text-muted-foreground uppercase tracking-wide text-[10px]">
            Stop-loss:
          </span>
          <div className="inline-flex rounded-md border border-border overflow-hidden font-mono">
            {([
              { label: 'Off', value: null },
              { label: '-25%', value: 0.25 },
              { label: '-40%', value: 0.4 },
              { label: '-50%', value: 0.5 },
              { label: '-75%', value: 0.75 },
            ] as { label: string; value: number | null }[]).map((opt, i) => (
              <button
                key={opt.label}
                type="button"
                onClick={() => setStopLossPct(opt.value)}
                className={cn(
                  'px-2.5 py-1',
                  i > 0 && 'border-l border-border',
                  stopLossPct === opt.value
                    ? 'bg-rose-500/10 text-rose-700 dark:text-rose-400'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        <div
          className="flex items-center gap-2"
          title={
            'Transaction cost. Models the bid/ask spread you cross: buy at ' +
            'entry x (1 + slippage/2), sell at exit x (1 - slippage/2). ' +
            'Frictionless fills overstate the win rate — keep this on for ' +
            'realistic numbers.'
          }
        >
          <span className="text-muted-foreground uppercase tracking-wide text-[10px]">
            Slippage:
          </span>
          <div className="inline-flex rounded-md border border-border overflow-hidden font-mono">
            {([
              { label: 'Off', value: null },
              { label: '5%', value: 0.05 },
              { label: '10%', value: 0.1 },
              { label: '15%', value: 0.15 },
            ] as { label: string; value: number | null }[]).map((opt, i) => (
              <button
                key={opt.label}
                type="button"
                onClick={() => setSlippagePct(opt.value)}
                className={cn(
                  'px-2.5 py-1',
                  i > 0 && 'border-l border-border',
                  slippagePct === opt.value
                    ? 'bg-amber-500/10 text-amber-700 dark:text-amber-400'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3 text-xs">
        <Field label="Start">
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="bg-background border border-border rounded px-2 py-1 w-full font-mono"
          />
        </Field>
        <Field label="End">
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="bg-background border border-border rounded px-2 py-1 w-full font-mono"
          />
        </Field>
        <Field label="Strategy">
          <select
            value={strategy}
            onChange={(e) => setStrategy(e.target.value)}
            className="bg-background border border-border rounded px-2 py-1 w-full font-mono"
          >
            {strategies.map((s) => (
              <option key={s.key} value={s.key} disabled={s.key === 'unusual_options_activity'}>
                {s.label}
                {s.key === 'unusual_options_activity' ? ' (live only)' : ''}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Sleeve">
          <select
            value={sleeve}
            onChange={(e) => setSleeve(e.target.value)}
            className="bg-background border border-border rounded px-2 py-1 w-full font-mono"
          >
            {(config?.sleeves ?? [{ name: 'mega_tech' } as { name: string }]).map((s) => (
              <option key={s.name} value={s.name}>
                {s.name.replace(/_/g, ' ')}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Tickers (optional)"
          hint="Comma-separated subset of the sleeve. Leave blank = whole sleeve."
        >
          <input
            value={tickerInput}
            onChange={(e) => setTickerInput(e.target.value)}
            placeholder="NVDA, MSFT"
            className="bg-background border border-border rounded px-2 py-1 w-full font-mono"
          />
        </Field>
        <Field label="Direction">
          <select
            value={direction}
            onChange={(e) => setDirection(e.target.value as Direction)}
            className="bg-background border border-border rounded px-2 py-1 w-full font-mono"
          >
            <option value="auto">auto (per strategy)</option>
            <option value="straddle">straddle</option>
            <option value="calls">calls</option>
            <option value="puts">puts</option>
          </select>
        </Field>
        <Field
          label="Min conviction %"
          hint="Only open trades when the candidate's magnitude-weighted conviction percentage is at or above this."
        >
          <select
            value={minConvictionPct}
            onChange={(e) => setMinConvictionPct(Number(e.target.value))}
            className="bg-background border border-border rounded px-2 py-1 w-full font-mono"
          >
            <option value={0}>0% (all)</option>
            <option value={40}>40%</option>
            <option value={50}>50%</option>
            <option value={60}>60%</option>
            <option value={70}>70%</option>
            <option value={80}>80% (strictest)</option>
          </select>
        </Field>
        <Field
          label="Hold days (max)"
          hint="Backstop exit if no target/stop/DTE trigger fires first. Realistic trades usually close sooner."
        >
          <input
            type="number"
            min={1}
            max={60}
            value={holdDays}
            onChange={(e) => setHoldDays(Number(e.target.value) || 30)}
            className="bg-background border border-border rounded px-2 py-1 w-full font-mono"
          />
        </Field>
        <Field
          label="DTE exit"
          hint="Close when the contract reaches this many days-to-expiry (steps out before the gamma/theta cliff). 0 = off."
        >
          <input
            type="number"
            min={0}
            max={60}
            value={dteExit ?? 0}
            onChange={(e) => {
              const v = Number(e.target.value);
              setDteExit(v > 0 ? v : null);
            }}
            className="bg-background border border-border rounded px-2 py-1 w-full font-mono"
          />
        </Field>
      </div>

      <div className="flex items-center gap-3">
        <Button onClick={handleRun} disabled={status === 'running'}>
          <Play className="h-3.5 w-3.5 mr-1.5" />
          {status === 'running' ? 'Running…' : 'Run backtest'}
        </Button>
        {status === 'running' && (
          <Button variant="outline" onClick={handleStop}>
            <Square className="h-3.5 w-3.5 mr-1.5" />
            Stop
          </Button>
        )}
        {progress && (
          <span className="text-xs text-muted-foreground font-mono">{progress}</span>
        )}
      </div>

      {error && (
        <div className="text-xs text-rose-500 italic px-2 py-2 rounded border border-rose-500/30 bg-rose-500/5">
          {error}
        </div>
      )}

      {summary && summary.n_trades === 0 && (
        <div className="text-xs text-muted-foreground italic px-2 py-3 rounded border border-dashed">
          No trades fired during the window. Try lowering the conviction threshold, picking a
          different strategy, or expanding the date range.
        </div>
      )}

      {summary && summary.n_trades > 0 && (
        <>
          {(summary.pricing === 'bsm' || !summary.slippage_pct) && (
            <div className="text-xs px-2 py-2 rounded border border-amber-500/30 bg-amber-500/5 text-amber-700 dark:text-amber-400">
              <strong>Reality check:</strong>{' '}
              {summary.pricing === 'bsm'
                ? 'BSM proxy produces a smooth premium path with no intraday noise, so it overstates win rate — especially in a trending window. '
                : ''}
              {!summary.slippage_pct
                ? 'Slippage is off, so fills are frictionless (no bid/ask cost). '
                : ''}
              For believable numbers, run with Real (Polygon) pricing and slippage on.
            </div>
          )}

          {summary.n_trades < 20 && (
            <div className="text-xs px-2 py-2 rounded border border-sky-500/30 bg-sky-500/5 text-sky-700 dark:text-sky-400">
              <strong>Small sample ({summary.n_trades} trades).</strong> A win rate
              from this few trades isn't statistically meaningful and is heavily
              swayed by the market regime in this window. Widen the date range,
              add tickers, or lower the conviction gate for a representative read.
            </div>
          )}

          {summary.pricing === 'real' &&
            typeof summary.n_synthetic === 'number' &&
            summary.n_synthetic > 0 && (
              <div className="text-xs px-2 py-2 rounded border border-amber-500/30 bg-amber-500/5 text-amber-700 dark:text-amber-400">
                <strong>{summary.n_synthetic}</strong> of {summary.n_trades} trades
                fell back to BSM (real contract or daily bar missing for that
                entry date). Synthetic trades are marked with a ⚠ in the trades
                table.
              </div>
            )}

          {typeof summary.stop_loss_pct === 'number' &&
            summary.stop_loss_pct > 0 &&
            typeof summary.n_stopped === 'number' && (
              <div className="text-xs px-2 py-2 rounded border border-rose-500/30 bg-rose-500/5">
                <strong className="text-rose-700 dark:text-rose-400">
                  Stop-loss −{Math.round(summary.stop_loss_pct * 100)}%:
                </strong>{' '}
                <strong>{summary.n_stopped}</strong> of {summary.n_trades} trades
                hit the stop and exited early
                {typeof summary.avg_return_when_stopped === 'number' && (
                  <>
                    {' '}
                    (avg realized{' '}
                    <span
                      className={
                        summary.avg_return_when_stopped >= 0
                          ? 'text-emerald-600 dark:text-emerald-400'
                          : 'text-rose-600 dark:text-rose-400'
                      }
                    >
                      {(summary.avg_return_when_stopped * 100).toFixed(1)}%
                    </span>
                    )
                  </>
                )}
                . Stopped rows are marked 🛑 in the trades table.
              </div>
            )}

          {summary.by_exit_reason && Object.keys(summary.by_exit_reason).length > 0 && (
            <section>
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
                How trades closed
              </div>
              <div className="flex flex-wrap gap-1.5">
                {EXIT_REASON_ORDER.filter((r) => (summary.by_exit_reason?.[r] ?? 0) > 0).map((r) => {
                  const meta = EXIT_REASON_META[r];
                  const n = summary.by_exit_reason?.[r] ?? 0;
                  const pct = summary.n_trades ? (n / summary.n_trades) * 100 : 0;
                  return (
                    <span
                      key={r}
                      title={meta.tip}
                      className={cn(
                        'inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-mono',
                        meta.cls,
                      )}
                    >
                      {meta.label}
                      <span className="font-semibold">{n}</span>
                      <span className="opacity-60">({pct.toFixed(0)}%)</span>
                    </span>
                  );
                })}
              </div>
            </section>
          )}

          <section className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px] font-mono">
            <Metric
              label="Trades"
              value={`${summary.n_trades}`}
              tooltip="Total simulated trades across all candidate-days."
            />
            <Metric
              label="Win rate"
              value={`${(summary.win_rate * 100).toFixed(1)}%`}
              tooltip="Pct of trades that ended above breakeven."
              color={summary.win_rate >= 0.5 ? 'positive' : 'negative'}
            />
            <Metric
              label="Avg return / trade"
              value={`${(summary.avg_return_pct * 100).toFixed(1)}%`}
              tooltip="Mean of (exit premium − entry premium) / entry premium across all trades."
              color={summary.avg_return_pct >= 0 ? 'positive' : 'negative'}
            />
            <Metric
              label="Σ P&L (per-share)"
              value={`$${summary.total_pnl_per_share.toFixed(2)}`}
              tooltip="Sum of per-share P&L. Per-contract is 100× this."
              color={summary.total_pnl_per_share >= 0 ? 'positive' : 'negative'}
            />
          </section>

          <section>
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
              Win / loss split
            </div>
            <WinLossBar wins={wins} losses={lossesAdj} breakevens={breakevens} />
          </section>

          <section>
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
              Cumulative P&L over trade sequence (per share)
            </div>
            <LineChart
              points={cumulativePoints}
              baseline={0}
              yPrefix="$"
              height={200}
            />
          </section>

          <section>
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
              P&L distribution (per-trade, per share)
            </div>
            <Histogram values={trades.map((t) => t.pnl)} prefix="$" buckets={24} />
          </section>

          {Object.keys(summary.by_conviction).length > 0 && (
            <section>
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
                Performance by conviction tier
              </div>
              <table className="w-full text-[11px] font-mono">
                <thead>
                  <tr className="text-muted-foreground border-b border-border">
                    <th className="text-left px-2 py-1">Conviction band</th>
                    <th className="text-right px-2 py-1">Trades</th>
                    <th className="text-right px-2 py-1">Win%</th>
                    <th className="text-right px-2 py-1">Avg return</th>
                    <th className="text-right px-2 py-1">Total P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(summary.by_conviction)
                    .map(([k, v]) => (
                      <tr key={k} className="border-b border-border/40 last:border-0">
                        <td className="px-2 py-1">
                          <Badge variant="outline">{k}</Badge>
                        </td>
                        <td className="px-2 py-1 text-right">{v.n_trades}</td>
                        <td className="px-2 py-1 text-right">{(v.win_rate * 100).toFixed(1)}%</td>
                        <td
                          className={cn(
                            'px-2 py-1 text-right',
                            v.avg_return_pct >= 0 ? 'text-emerald-500' : 'text-rose-500',
                          )}
                        >
                          {(v.avg_return_pct * 100).toFixed(1)}%
                        </td>
                        <td
                          className={cn(
                            'px-2 py-1 text-right',
                            v.total_pnl >= 0 ? 'text-emerald-500' : 'text-rose-500',
                          )}
                        >
                          ${v.total_pnl.toFixed(2)}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </section>
          )}
        </>
      )}

      {trades.length > 0 && (
        <section>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
            Trades ({trades.length})
          </div>
          <div className="max-h-80 overflow-y-auto rounded border border-border">
            <table className="w-full text-[10px] font-mono">
              <thead className="sticky top-0 bg-background border-b border-border">
                <tr className="text-muted-foreground">
                  <th className="text-left px-2 py-1">Ticker</th>
                  <th className="text-left px-2 py-1">Contract</th>
                  <th className="text-left px-2 py-1">Dir</th>
                  <th className="text-right px-2 py-1">Conv%</th>
                  <th className="text-left px-2 py-1">Entered</th>
                  <th className="text-left px-2 py-1">Exited</th>
                  <th className="text-right px-2 py-1">σ</th>
                  <th className="text-right px-2 py-1">Entry $</th>
                  <th className="text-right px-2 py-1">Exit $</th>
                  <th className="text-right px-2 py-1">P&amp;L</th>
                  <th className="text-right px-2 py-1">Return</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr key={i} className="border-b border-border/40 last:border-0">
                    <td className="px-2 py-1">
                      {t.ticker}
                      {t.synthetic && (
                        <span
                          className="ml-1 text-amber-500"
                          title={
                            t.contract_ticker
                              ? `Fell back to BSM — real fill missing for ${t.contract_ticker}`
                              : 'Priced via BSM proxy (no real-fill lookup)'
                          }
                        >
                          ⚠
                        </span>
                      )}
                    </td>
                    <td
                      className="px-2 py-1 whitespace-nowrap"
                      title={t.contract_ticker ?? 'BSM-priced — synthetic contract'}
                    >
                      {contractLabel(t)}
                    </td>
                    <td className="px-2 py-1 lowercase">{t.direction}</td>
                    <td className="px-2 py-1 text-right">
                      {t.conviction_pct != null ? `${t.conviction_pct.toFixed(0)}%` : '—'}
                    </td>
                    <td className="px-2 py-1 whitespace-nowrap">{t.open_date}</td>
                    <td className="px-2 py-1 whitespace-nowrap">
                      {t.close_date}
                      {t.exit_reason && EXIT_REASON_META[t.exit_reason] && (
                        <span
                          title={EXIT_REASON_META[t.exit_reason].tip}
                          className={cn(
                            'ml-1 px-1 rounded-sm border text-[8px] font-bold align-middle',
                            EXIT_REASON_META[t.exit_reason].cls,
                          )}
                        >
                          {EXIT_REASON_META[t.exit_reason].label}
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-1 text-right">{(t.sigma * 100).toFixed(0)}%</td>
                    <td className="px-2 py-1 text-right">${t.entry_premium.toFixed(2)}</td>
                    <td className="px-2 py-1 text-right">${t.exit_premium.toFixed(2)}</td>
                    <td
                      className={cn(
                        'px-2 py-1 text-right',
                        t.pnl >= 0 ? 'text-emerald-500' : 'text-rose-500',
                      )}
                    >
                      ${t.pnl.toFixed(2)}
                    </td>
                    <td
                      className={cn(
                        'px-2 py-1 text-right',
                        t.return_pct >= 0 ? 'text-emerald-500' : 'text-rose-500',
                      )}
                    >
                      {(t.return_pct * 100).toFixed(0)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground" title={hint}>
        {label}
      </span>
      <div className="mt-0.5">{children}</div>
    </label>
  );
}

function Metric({
  label,
  value,
  tooltip,
  color,
}: {
  label: string;
  value: string;
  tooltip?: string;
  color?: 'positive' | 'negative';
}) {
  return (
    <div
      className="rounded border border-border px-2 py-1.5"
      title={tooltip}
    >
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div
        className={cn(
          'font-semibold text-sm',
          color === 'positive' && 'text-emerald-500',
          color === 'negative' && 'text-rose-500',
        )}
      >
        {value}
      </div>
    </div>
  );
}
