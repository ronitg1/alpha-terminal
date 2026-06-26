/**
 * SleevesBacktestPanel — sleeves backtest UI with LLM-agent decisions.
 *
 * Form: date range, sleeve picker, ticker filter, initial capital.
 * Run streams the SSE backtest. On 'complete', renders:
 *   - Headline metric cards (final value, total return, Sharpe, Sortino,
 *     max DD, days simulated, trades closed)
 *   - Equity curve LineChart with initial-capital baseline + trade-entry/exit
 *     vertical markers
 *   - Trade table — every closed trade with entry/exit dates, P&L, %
 *   - SleeveAttributionTable
 *
 * Cost-aware defaults (1 ticker × 2 weeks) so an accidental click doesn't
 * drain the LLM budget. The button label spells out what's about to run.
 */

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { cn } from '@/lib/utils';
import { postSse } from '@/services/sleeves-api';
import {
  BacktestAttribution,
  BacktestDayResult,
  SleevesBacktestSummary,
  SleevesBacktestSummaryHeader,
  SleevesBacktestTrade,
} from '@/types/sleeves';
import { AlertTriangle, Play, Square } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { LineChart, LineMarker, LinePoint } from '../charts/line-chart';
import { SleeveAttributionTable } from './sleeve-attribution-table';

type RunStatus = 'idle' | 'running' | 'done' | 'error';

function isoNDaysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

export function SleevesBacktestPanel() {
  const { config } = useSleevesContext();
  const [startDate, setStartDate] = useState(isoNDaysAgo(14));
  const [endDate, setEndDate] = useState(isoNDaysAgo(0));
  // Empty until config loads; seeded to the first real portfolio below so a
  // renamed/removed portfolio can't leave this pointing at a dead name.
  const [sleeve, setSleeve] = useState<string>('');
  const [tickerInput, setTickerInput] = useState('NVDA');
  const [initialCapital, setInitialCapital] = useState(100_000);

  const [status, setStatus] = useState<RunStatus>('idle');
  const [progress, setProgress] = useState<string>('');
  const [days, setDays] = useState<BacktestDayResult[]>([]);
  const [trades, setTrades] = useState<SleevesBacktestTrade[]>([]);
  const [attribution, setAttribution] = useState<BacktestAttribution | null>(null);
  const [headerSummary, setHeaderSummary] =
    useState<SleevesBacktestSummaryHeader | null>(null);
  const [finalMetrics, setFinalMetrics] = useState<
    SleevesBacktestSummary['performance_metrics'] | null
  >(null);
  const [missingTickers, setMissingTickers] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  // Seed the portfolio to the first one once config arrives (never a hardcoded
  // name). Runs only while empty, so it can't override a user choice.
  useEffect(() => {
    if (sleeve) return;
    const first = config?.sleeves?.[0]?.name;
    if (first) setSleeve(first);
  }, [config, sleeve]);

  // Equity-curve points + per-trade entry/exit markers, suitable for LineChart.
  const equityPoints: LinePoint[] = useMemo(
    () => days.map((d) => ({ x: d.date, y: d.portfolio_value })),
    [days],
  );
  const tradeMarkers: LineMarker[] = useMemo(() => {
    const markers: LineMarker[] = [];
    for (const t of trades) {
      markers.push({
        x: t.open_date,
        kind: 'entry',
        label: `Open ${t.ticker} · entry $${t.entry_value.toFixed(0)}`,
      });
      markers.push({
        x: t.close_date,
        kind: 'exit',
        label: `Close ${t.ticker} · P&L $${t.pnl.toFixed(2)}`,
      });
    }
    return markers;
  }, [trades]);

  const handleRun = async () => {
    if (status === 'running') return;
    setStatus('running');
    setProgress('Starting…');
    setDays([]);
    setTrades([]);
    setAttribution(null);
    setHeaderSummary(null);
    setFinalMetrics(null);
    setMissingTickers([]);
    setError(null);

    const tickers = tickerInput
      .split(/[,\s]+/)
      .map((t) => t.trim().toUpperCase())
      .filter(Boolean);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      await postSse(
        '/sleeves/backtest/run',
        {
          start_date: startDate,
          end_date: endDate,
          sleeves: [sleeve],
          tickers: tickers.length ? tickers : null,
          initial_capital: initialCapital,
        },
        (event, data) => {
          if (event === 'start') {
            const d = data as { data?: { missing_tickers?: string[] } } & {
              missing_tickers?: string[];
            };
            // Backend's StartEvent wraps the payload under .data; older shapes
            // emit it flat. Tolerate both.
            const missing = d.data?.missing_tickers ?? d.missing_tickers ?? [];
            setMissingTickers(missing);
          } else if (event === 'progress') {
            const d = data as { status?: string; analysis?: string | null };
            if (d.status) setProgress(d.status);
            if (d.analysis) {
              try {
                const parsed = JSON.parse(d.analysis);
                // Agent-progress events also ride this channel with their own
                // structured-signal JSON in `analysis`. Only treat the payload
                // as a BacktestDayResult if it actually has the day shape —
                // otherwise the equity curve / final-value metrics crash with
                // undefined.toLocaleString().
                if (
                  parsed &&
                  typeof parsed.portfolio_value === 'number' &&
                  typeof parsed.date === 'string'
                ) {
                  setDays((prev) => [...prev, parsed as BacktestDayResult]);
                }
              } catch {
                // Non-day-result progress event; ignore parse failure.
              }
            }
          } else if (event === 'complete') {
            const payload = (data as { data: SleevesBacktestSummary }).data;
            setAttribution(payload.attribution);
            setFinalMetrics(payload.performance_metrics);
            setHeaderSummary(payload.summary ?? null);
            setTrades(payload.trades ?? []);
            if (payload.results?.length) setDays(payload.results);
            setStatus('done');
            setProgress(`Complete — ${payload.results?.length ?? 0} days simulated`);
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

  return (
    <div className="space-y-5">
      <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-xs">
        <strong className="text-amber-700 dark:text-amber-400">Cost warning:</strong>{' '}
        Each trading day fires the selected portfolio's full agent panel on every ticker.
        A 2-week × 1-ticker run is roughly 10 days × 3 agents = ~30 LLM calls. Scale up
        cautiously.
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
        <Field label="Start" hint="Backtest start date (inclusive).">
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="bg-background border border-border rounded px-2 py-1 w-full font-mono"
          />
        </Field>
        <Field label="End" hint="Backtest end date (inclusive).">
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="bg-background border border-border rounded px-2 py-1 w-full font-mono"
          />
        </Field>
        <Field label="Portfolio" hint="Which portfolio's agent panel to run.">
          <select
            value={sleeve}
            onChange={(e) => setSleeve(e.target.value)}
            className="bg-background border border-border rounded px-2 py-1 w-full font-mono"
          >
            {(config?.sleeves ?? []).map((s) => (
              <option key={s.name} value={s.name}>
                {s.name.replace(/_/g, ' ')}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Tickers" hint="Comma-separated. Leave blank = whole portfolio. Use 1–3 to keep LLM cost low.">
          <input
            value={tickerInput}
            onChange={(e) => setTickerInput(e.target.value)}
            placeholder="NVDA, MSFT"
            className="bg-background border border-border rounded px-2 py-1 w-full font-mono"
          />
        </Field>
        <Field label="Capital ($)" hint="Starting cash. P&L and total-return are computed against this.">
          <input
            type="number"
            value={initialCapital}
            onChange={(e) => setInitialCapital(Number(e.target.value) || 0)}
            className="bg-background border border-border rounded px-2 py-1 w-full font-mono"
          />
        </Field>
      </div>

      <div className="flex items-center gap-3">
        <Button onClick={handleRun} disabled={status === 'running'}>
          <Play className="h-3.5 w-3.5 mr-1.5" />
          {status === 'running' ? 'Running…' : 'Run sleeves backtest'}
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

      {missingTickers.length > 0 && (
        <div className="text-xs flex items-start gap-2 px-2 py-2 rounded border border-amber-500/30 bg-amber-500/5">
          <AlertTriangle className="h-3.5 w-3.5 text-amber-500 flex-shrink-0 mt-0.5" />
          <div>
            <strong className="text-amber-700 dark:text-amber-400">Skipped tickers with no price data:</strong>{' '}
            {missingTickers.join(', ')}. These were excluded from the backtest. Check the date range or
            ticker symbols.
          </div>
        </div>
      )}

      {/* HEADLINE METRICS — always visible during/after a run so the user
          sees portfolio progress in real time. */}
      {(days.length > 0 || headerSummary) && (
        <section className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <Metric
            label="Final value"
            value={
              headerSummary
                ? `$${headerSummary.final_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
                : days.length
                  ? `$${days[days.length - 1].portfolio_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
                  : '—'
            }
            sub={`from $${initialCapital.toLocaleString()}`}
            tooltip="Portfolio value at the end of the simulated window. The starting cash is the 'Capital' you set above."
          />
          <Metric
            label="Total return"
            value={
              headerSummary
                ? `${headerSummary.total_return_pct >= 0 ? '+' : ''}${headerSummary.total_return_pct.toFixed(2)}%`
                : days.length
                  ? `${(((days[days.length - 1].portfolio_value - initialCapital) / initialCapital) * 100).toFixed(2)}%`
                  : '—'
            }
            sub={`over ${headerSummary?.n_days_simulated ?? days.length} days`}
            tooltip="(Final value − Capital) / Capital. Includes realized P&L from closed trades plus unrealized mark-to-market on open positions."
            color={
              headerSummary
                ? headerSummary.total_return_pct >= 0
                  ? 'positive'
                  : 'negative'
                : undefined
            }
          />
          <Metric
            label="Sharpe (annualized)"
            value={fmt(finalMetrics?.sharpe_ratio)}
            sub="risk-adjusted"
            tooltip="Annualized risk-adjusted return — mean excess daily return / std dev, scaled by √252. >1 is good; >2 is great. Negative = lost money for the risk taken."
            color={
              finalMetrics?.sharpe_ratio === undefined || finalMetrics?.sharpe_ratio === null
                ? undefined
                : finalMetrics.sharpe_ratio >= 0
                  ? 'positive'
                  : 'negative'
            }
          />
          <Metric
            label="Max drawdown"
            value={fmtPct(finalMetrics?.max_drawdown)}
            sub={finalMetrics?.max_drawdown_date ?? 'worst trough'}
            tooltip="Worst peak-to-trough portfolio loss during the window. -10% means the portfolio fell 10% from its highest point at some point before recovering."
            color="negative"
          />
          <Metric
            label="Closed trades"
            value={`${headerSummary?.n_trades ?? trades.length}`}
            sub={trades.length ? 'buy → sell cycles' : 'no closes yet'}
            tooltip="Number of buy→sell round-trips that completed inside the window. A position bought and not yet sold is NOT counted here — it shows as unrealized P&L in Final value."
          />
          <Metric
            label="Sortino"
            value={fmt(finalMetrics?.sortino_ratio)}
            sub="downside-only Sharpe"
            tooltip="Like Sharpe but only penalises downside volatility. Higher is better. Compare against Sharpe — if Sortino is much higher, your losses are smaller / less frequent than the std-dev would suggest."
          />
          <Metric
            label="Days simulated"
            value={`${headerSummary?.n_days_simulated ?? days.length}`}
            sub="trading days"
            tooltip="Number of trading days the agent panel ran on. Skips weekends and days with missing data."
          />
          <Metric
            label="Gross exposure"
            value={fmtMoney(finalMetrics?.gross_exposure ?? null)}
            sub="long + short notional"
            tooltip="Sum of long + short position values at the final day. Reflects how 'leveraged' the portfolio ended."
          />
        </section>
      )}

      {/* EQUITY CURVE — proper chart with axes + trade markers. */}
      {equityPoints.length > 1 && (
        <section>
          <div className="flex items-baseline gap-2 mb-1">
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
              Equity curve
            </div>
            <div className="text-[10px] text-muted-foreground">
              · amber markers = trade entries · blue markers = exits
            </div>
          </div>
          <LineChart
            points={equityPoints}
            baseline={initialCapital}
            markers={tradeMarkers}
            yPrefix="$"
            height={240}
          />
        </section>
      )}

      {/* TRADES TABLE — every closed trade visible with entry/exit + P&L. */}
      {trades.length > 0 && (
        <section>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
            Closed trades ({trades.length})
          </div>
          <div className="max-h-80 overflow-y-auto rounded border border-border">
            <table className="w-full text-[10px] font-mono">
              <thead className="sticky top-0 bg-background border-b border-border">
                <tr className="text-muted-foreground">
                  <th className="text-left px-2 py-1">Ticker</th>
                  <th className="text-left px-2 py-1">Portfolio</th>
                  <th className="text-left px-2 py-1">Best signal</th>
                  <th className="text-left px-2 py-1">Open</th>
                  <th className="text-left px-2 py-1">Close</th>
                  <th className="text-right px-2 py-1">Held</th>
                  <th className="text-right px-2 py-1">Entry $</th>
                  <th className="text-right px-2 py-1">P&amp;L</th>
                  <th className="text-right px-2 py-1">Return</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr key={i} className="border-b border-border/40 last:border-0">
                    <td className="px-2 py-1 font-semibold">{t.ticker}</td>
                    <td className="px-2 py-1">{t.sleeve}</td>
                    <td className="px-2 py-1">
                      {t.agent ? (
                        <Badge variant="outline" className="text-[9px] font-mono">
                          {t.agent}
                        </Badge>
                      ) : (
                        <span className="text-muted-foreground italic">—</span>
                      )}
                    </td>
                    <td className="px-2 py-1">{t.open_date}</td>
                    <td className="px-2 py-1">{t.close_date}</td>
                    <td className="px-2 py-1 text-right">{t.hold_days}d</td>
                    <td className="px-2 py-1 text-right">
                      ${t.entry_value.toFixed(0)}
                    </td>
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
                      {(t.return_pct * 100).toFixed(2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {attribution && (
        <section>
          <SleeveAttributionTable attribution={attribution} />
        </section>
      )}
    </div>
  );
}

// ─── helpers ────────────────────────────────────────────────────────────────

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
    <label className="block" title={hint}>
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <div className="mt-0.5">{children}</div>
    </label>
  );
}

function Metric({
  label,
  value,
  sub,
  tooltip,
  color,
}: {
  label: string;
  value: string;
  sub?: string;
  tooltip?: string;
  color?: 'positive' | 'negative';
}) {
  return (
    <div className="rounded border border-border px-2 py-1.5" title={tooltip}>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div
        className={cn(
          'font-semibold text-sm font-mono',
          color === 'positive' && 'text-emerald-500',
          color === 'negative' && 'text-rose-500',
        )}
      >
        {value}
      </div>
      {sub && <div className="text-[10px] text-muted-foreground">{sub}</div>}
    </div>
  );
}

function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  return n.toFixed(2);
}

function fmtPct(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  return `${n.toFixed(2)}%`;
}

function fmtMoney(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}
