/**
 * PatternBacktestPanel — backtests the Pattern Scanner as an options strategy.
 *
 * For every historical bar where a detector fires, the backend simulates
 * buying an option (target delta + DTE) and selling it `hold` candles later,
 * then aggregates win rate / return / expectancy. "Optimize" sweeps
 * delta x DTE x hold and ranks the combinations so you can see which option to
 * buy and how long to hold.
 *
 * Pricing defaults to real historical fills (the plan exposes intraday option
 * aggregates); BSM is a fast fallback, flagged because it diverges from real
 * premiums (~24% median, worse for OTM / high-IV names).
 *
 * Universe comes from the shared SleevesContext (portfolios + watchlists),
 * resolved to a ticker list client-side; a custom ticker box overrides it.
 */

import { useSleevesContext } from '@/contexts/sleeves-context';
import { listPatterns } from '@/services/patterns-api';
import { postSse } from '@/services/sleeves-api';
import type {
  PatternBacktestSummary,
  PatternBacktestTrade,
  PatternTimeframe,
} from '@/types/patterns';
import { parseUniverse, UniversePicker } from '../sleeves/universe-picker';
import { Histogram } from '../sleeves/charts/histogram';
import { LineChart, LinePoint } from '../sleeves/charts/line-chart';
import { WinLossBar } from '../sleeves/charts/win-loss-bar';
import { useEffect, useMemo, useRef, useState } from 'react';

const TIMEFRAMES: { key: PatternTimeframe; label: string }[] = [
  { key: 'week', label: 'Weekly' },
  { key: 'day', label: 'Daily' },
  { key: '1h', label: '1 hour' },
  { key: '15m', label: '15 min' },
];

const DELTA_CHOICES = [0.3, 0.4, 0.5, 0.6, 0.7];
const HOLD_CHOICES = [1, 2, 3, 5, 10];

// How far back to replay, per timeframe. The last entry is the max the data
// plan allows for that bar size (intraday history is short); the backend
// clamps anything larger. Default is the max.
const LOOKBACK_PRESETS: Record<PatternTimeframe, { label: string; days: number }[]> = {
  week: [{ label: '1y', days: 365 }, { label: '2y', days: 730 }, { label: '3y', days: 1095 }, { label: '5y (max)', days: 1825 }],
  day: [{ label: '3mo', days: 90 }, { label: '6mo', days: 180 }, { label: '1y', days: 365 }, { label: '2y (max)', days: 730 }],
  '1h': [{ label: '2wk', days: 14 }, { label: '1mo', days: 30 }, { label: '2mo', days: 60 }, { label: '3mo (max)', days: 90 }],
  '15m': [{ label: '1wk', days: 7 }, { label: '2wk', days: 14 }, { label: '1mo (max)', days: 30 }],
};

function maxLookback(tf: PatternTimeframe): number {
  const p = LOOKBACK_PRESETS[tf];
  return p[p.length - 1].days;
}

type RunStatus = 'idle' | 'running' | 'done' | 'error';

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

export function PatternBacktestPanel() {
  const { config, watchlists } = useSleevesContext();

  // ─── Universe ──────────────────────────────────────────────────────────
  const [universe, setUniverse] = useState<string>('');
  const [customTickers, setCustomTickers] = useState('');
  useEffect(() => {
    if (universe) return;
    const first = config?.sleeves?.[0]?.name;
    if (first) setUniverse(`sleeve:${first}`);
  }, [config, universe]);

  // ─── Scan scope ────────────────────────────────────────────────────────
  const [timeframe, setTimeframe] = useState<PatternTimeframe>('1h');
  const [lookbackDays, setLookbackDays] = useState<number>(maxLookback('1h'));
  const [allPatterns, setAllPatterns] = useState<string[]>([]);
  const [selPatterns, setSelPatterns] = useState<Set<string>>(new Set());
  const [patternsOpen, setPatternsOpen] = useState(false);

  // Each timeframe has its own valid lookback range — reset to that
  // timeframe's max when it changes so we never send an out-of-range window.
  useEffect(() => {
    setLookbackDays(maxLookback(timeframe));
  }, [timeframe]);

  // ─── Option + exit knobs ───────────────────────────────────────────────
  const [mode, setMode] = useState<'single' | 'optimize'>('single');
  const [direction, setDirection] = useState<'auto' | 'calls' | 'puts'>('auto');
  const [pricing, setPricing] = useState<'real' | 'bsm'>('real');
  const [slippagePct, setSlippagePct] = useState<number | null>(0.05);
  const [minConfidence, setMinConfidence] = useState(0);

  // single
  const [delta, setDelta] = useState(0.4);
  const [dte, setDte] = useState<number | ''>('');
  const [hold, setHold] = useState(1);
  // optimize
  const [deltas, setDeltas] = useState<Set<number>>(new Set([0.3, 0.4, 0.5, 0.6]));
  const [holds, setHolds] = useState<Set<number>>(new Set([1, 2, 3, 5]));
  const [dtesInput, setDtesInput] = useState('');

  // ─── Run state ─────────────────────────────────────────────────────────
  const [status, setStatus] = useState<RunStatus>('idle');
  const [progress, setProgress] = useState('');
  const [summary, setSummary] = useState<PatternBacktestSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    void listPatterns()
      .then((r) => setAllPatterns(r.patterns ?? []))
      .catch(() => setAllPatterns([]));
  }, []);

  const resolveTickers = (): string[] => {
    const custom = customTickers
      .split(/[,\s]+/)
      .map((t) => t.trim().toUpperCase())
      .filter(Boolean);
    if (custom.length) return Array.from(new Set(custom));
    const { source, name } = parseUniverse(universe);
    if (source === 'watchlist') {
      const wl = watchlists.find((w) => w.name === name);
      return wl ? Array.from(new Set(wl.tickers.map((t) => t.ticker.toUpperCase()))) : [];
    }
    const s = config?.sleeves.find((sl) => sl.name === name);
    return s ? Array.from(new Set(s.tickers.map((t) => t.toUpperCase()))) : [];
  };

  const handleRun = async () => {
    if (status === 'running') return;
    const tickers = resolveTickers();
    if (!tickers.length) {
      setError('No tickers — pick a portfolio/watchlist with holdings, or enter custom tickers.');
      setStatus('error');
      return;
    }
    setStatus('running');
    setProgress('Starting…');
    setSummary(null);
    setError(null);

    const dtes = dtesInput
      .split(/[,\s]+/)
      .map((d) => parseInt(d.trim(), 10))
      .filter((d) => Number.isFinite(d) && d > 0);

    const body: Record<string, unknown> = {
      tickers,
      timeframe,
      patterns: selPatterns.size > 0 ? Array.from(selPatterns) : [],
      mode,
      direction,
      pricing,
      slippage_pct: slippagePct,
      min_confidence: minConfidence,
      lookback_days: lookbackDays,
    };
    if (mode === 'single') {
      body.delta = delta;
      body.dte = dte === '' ? null : dte;
      body.hold = hold;
    } else {
      body.deltas = Array.from(deltas).sort((a, b) => a - b);
      body.holds = Array.from(holds).sort((a, b) => a - b);
      body.dtes = dtes;
    }

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    // Watchdog: the server heartbeats every ~5s even while pricing a heavy
    // ticker, so a 90s gap means the connection is effectively dead. Abort and
    // show a clear message instead of hanging forever on the last progress line.
    let lastEvent = Date.now();
    let stalled = false;
    const watchdog = window.setInterval(() => {
      if (Date.now() - lastEvent > 90_000) {
        stalled = true;
        ctrl.abort();
      }
    }, 5_000);
    try {
      await postSse(
        '/patterns/backtest',
        body,
        (event, data) => {
          lastEvent = Date.now();
          if (event === 'progress') {
            const d = data as { status?: string };
            if (d.status) setProgress(d.status);
          } else if (event === 'complete') {
            setSummary((data as { data: PatternBacktestSummary }).data);
            setStatus('done');
            setProgress('Complete');
          } else if (event === 'error') {
            setError((data as { message?: string }).message ?? 'Backtest error');
            setStatus('error');
          }
        },
        ctrl.signal,
      );
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        if (stalled) {
          setError(
            'The backtest stalled (no response for 90s). Very large runs can do this — try fewer tickers, a smaller optimize grid, or BSM pricing.',
          );
          setStatus('error');
        } else {
          setStatus('idle');
          setProgress('Cancelled');
        }
        return;
      }
      setError(err instanceof Error ? err.message : String(err));
      setStatus('error');
    } finally {
      window.clearInterval(watchdog);
      if (abortRef.current === ctrl) abortRef.current = null;
    }
  };

  const handleStop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
  };

  const best = summary?.configs?.[0] ?? null;
  const trades = summary?.trades ?? [];
  const cumulative: LinePoint[] = useMemo(() => {
    let cum = 0;
    return trades.map((t, i) => {
      cum += t.pnl;
      return { x: `#${i + 1}`, y: cum };
    });
  }, [trades]);

  const toggle = <T,>(set: Set<T>, v: T, setter: (s: Set<T>) => void) => {
    const next = new Set(set);
    next.has(v) ? next.delete(v) : next.add(v);
    setter(next);
  };

  return (
    <div className="h-full overflow-y-auto p-6 max-w-5xl mx-auto text-sm">
      <header className="mb-4">
        <h1 className="text-xl font-semibold">Pattern Backtest</h1>
        <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
          Replays the scanner over history: every time a pattern fires, it buys an
          option (your target delta + DTE) and sells it after a set number of
          candles, then reports win rate, return, and expectancy.{' '}
          <strong>Optimize</strong> sweeps delta × DTE × hold to find the best
          option to buy and how long to hold. Pricing uses real historical option
          fills by default.
        </p>
      </header>

      {/* ─── Universe ─── */}
      <Section title="Universe">
        <div className="flex flex-wrap items-center gap-3">
          <UniversePicker value={universe} onChange={setUniverse} />
          <span className="text-xs text-muted-foreground">or custom</span>
          <input
            value={customTickers}
            onChange={(e) => setCustomTickers(e.target.value)}
            placeholder="NVDA, AAPL (overrides)"
            className="bg-background border border-border rounded px-2 py-1 font-mono text-xs flex-1 min-w-[160px]"
          />
        </div>
      </Section>

      {/* ─── Timeframe + patterns ─── */}
      <Section title="What to scan">
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <Label>Timeframe</Label>
          <div className="inline-flex rounded-md border border-border overflow-hidden">
            {TIMEFRAMES.map((t, i) => (
              <button
                key={t.key}
                onClick={() => setTimeframe(t.key)}
                className={cls(
                  'px-2.5 py-1 text-xs font-mono',
                  i > 0 && 'border-l border-border',
                  timeframe === t.key
                    ? 'bg-indigo-500/15 text-indigo-400'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {t.label}
              </button>
            ))}
          </div>
          <Label>Lookback</Label>
          <div className="inline-flex rounded-md border border-border overflow-hidden">
            {LOOKBACK_PRESETS[timeframe].map((p, i) => (
              <button
                key={p.days}
                onClick={() => setLookbackDays(p.days)}
                title={`Replay the last ${p.days} calendar days`}
                className={cls(
                  'px-2.5 py-1 text-xs font-mono',
                  i > 0 && 'border-l border-border',
                  lookbackDays === p.days
                    ? 'bg-indigo-500/15 text-indigo-400'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
        <div>
          <button
            onClick={() => setPatternsOpen((o) => !o)}
            className="text-xs text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
          >
            Patterns: {selPatterns.size === 0 ? 'All' : `${selPatterns.size} selected`} ▾
          </button>
          {patternsOpen && (
            <div className="mt-2 grid grid-cols-2 sm:grid-cols-3 gap-1">
              {allPatterns.map((p) => (
                <label key={p} className="flex items-center gap-1.5 text-[11px] cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selPatterns.has(p)}
                    onChange={() => toggle(selPatterns, p, setSelPatterns)}
                  />
                  {p}
                </label>
              ))}
              {selPatterns.size > 0 && (
                <button
                  onClick={() => setSelPatterns(new Set())}
                  className="text-[11px] text-indigo-400 text-left"
                >
                  clear (use all)
                </button>
              )}
            </div>
          )}
        </div>
      </Section>

      {/* ─── Option + exit ─── */}
      <Section title="Option & exit">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 mb-3">
          <Toggle
            label="Mode"
            value={mode}
            opts={[
              { v: 'single', label: 'Single' },
              { v: 'optimize', label: 'Optimize' },
            ]}
            onChange={(v) => setMode(v as 'single' | 'optimize')}
          />
          <Toggle
            label="Direction"
            value={direction}
            opts={[
              { v: 'auto', label: 'Auto' },
              { v: 'calls', label: 'Calls' },
              { v: 'puts', label: 'Puts' },
            ]}
            onChange={(v) => setDirection(v as 'auto' | 'calls' | 'puts')}
          />
          <Toggle
            label="Pricing"
            value={pricing}
            opts={[
              { v: 'real', label: 'Real fills' },
              { v: 'bsm', label: 'BSM (fast)' },
            ]}
            onChange={(v) => setPricing(v as 'real' | 'bsm')}
          />
        </div>

        {mode === 'single' ? (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Field label="Delta">
              <select
                value={delta}
                onChange={(e) => setDelta(Number(e.target.value))}
                className="bg-background border border-border rounded px-2 py-1 w-full font-mono text-xs"
              >
                {DELTA_CHOICES.map((d) => (
                  <option key={d} value={d}>
                    {d.toFixed(2)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="DTE" hint="Days to expiry. Blank = timeframe default.">
              <input
                type="number"
                min={1}
                value={dte}
                onChange={(e) => setDte(e.target.value === '' ? '' : Number(e.target.value))}
                placeholder="auto"
                className="bg-background border border-border rounded px-2 py-1 w-full font-mono text-xs"
              />
            </Field>
            <Field label="Hold (candles)" hint="1 = sell at the next candle's close.">
              <input
                type="number"
                min={1}
                max={60}
                value={hold}
                onChange={(e) => setHold(Math.max(1, Number(e.target.value) || 1))}
                className="bg-background border border-border rounded px-2 py-1 w-full font-mono text-xs"
              />
            </Field>
          </div>
        ) : (
          <div className="space-y-2">
            <ChipRow
              label="Deltas"
              choices={DELTA_CHOICES}
              selected={deltas}
              onToggle={(v) => toggle(deltas, v, setDeltas)}
              fmt={(v) => v.toFixed(2)}
            />
            <ChipRow
              label="Holds (candles)"
              choices={HOLD_CHOICES}
              selected={holds}
              onToggle={(v) => toggle(holds, v, setHolds)}
              fmt={(v) => String(v)}
            />
            <div className="flex items-center gap-2">
              <Label>DTEs</Label>
              <input
                value={dtesInput}
                onChange={(e) => setDtesInput(e.target.value)}
                placeholder="auto (e.g. 14, 30)"
                className="bg-background border border-border rounded px-2 py-1 font-mono text-xs w-44"
              />
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 mt-3">
          <div className="flex items-center gap-2">
            <Label>Slippage</Label>
            <div className="inline-flex rounded-md border border-border overflow-hidden font-mono text-xs">
              {[
                { label: 'Off', value: null },
                { label: '5%', value: 0.05 },
                { label: '10%', value: 0.1 },
              ].map((o, i) => (
                <button
                  key={o.label}
                  onClick={() => setSlippagePct(o.value)}
                  className={cls(
                    'px-2 py-1',
                    i > 0 && 'border-l border-border',
                    slippagePct === o.value
                      ? 'bg-amber-500/15 text-amber-400'
                      : 'text-muted-foreground hover:text-foreground',
                  )}
                >
                  {o.label}
                </button>
              ))}
            </div>
          </div>
          <Field label="Min confidence">
            <select
              value={minConfidence}
              onChange={(e) => setMinConfidence(Number(e.target.value))}
              className="bg-background border border-border rounded px-2 py-1 font-mono text-xs"
            >
              {[0, 50, 60, 70, 80, 90, 100].map((c) => (
                <option key={c} value={c}>
                  {c === 0 ? 'Any' : c === 100 ? '100' : `${c}+`}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Section>

      <div className="flex items-center gap-3 mb-4">
        <button
          onClick={handleRun}
          disabled={status === 'running'}
          className="px-3 py-1.5 rounded-md bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium disabled:opacity-60"
        >
          {status === 'running' ? 'Running…' : 'Run backtest'}
        </button>
        {status === 'running' && (
          <button
            onClick={handleStop}
            className="px-3 py-1.5 rounded-md border border-border text-xs"
          >
            Stop
          </button>
        )}
        {progress && <span className="text-xs text-muted-foreground font-mono">{progress}</span>}
      </div>

      {error && (
        <div className="text-xs text-rose-500 italic px-2 py-2 rounded border border-rose-500/30 bg-rose-500/5 mb-3">
          {error}
        </div>
      )}

      {summary && <Results summary={summary} best={best} trades={trades} cumulative={cumulative} />}
    </div>
  );
}

// ─── Results ────────────────────────────────────────────────────────────────

function Results({
  summary,
  best,
  trades,
  cumulative,
}: {
  summary: PatternBacktestSummary;
  best: PatternBacktestSummary['configs'][number] | null;
  trades: PatternBacktestTrade[];
  cumulative: LinePoint[];
}) {
  if (!best || best.n_trades === 0) {
    return (
      <div className="text-xs text-muted-foreground italic px-2 py-3 rounded border border-dashed">
        No trades simulated. Try a wider lookback, more tickers, lower min-confidence, or a
        longer hold. ({summary.n_signals} signals fired but none formed a tradeable hold.)
      </div>
    );
  }
  const wins = best.n_wins;
  const losses = best.n_trades - wins;

  return (
    <div className="space-y-5">
      {summary.pricing === 'bsm' && (
        <Note tone="amber">
          <strong>BSM pricing</strong> — premiums are modeled off realized vol, not real
          fills. Empirically ~24% median divergence (worse for OTM / high-IV names). Re-run
          with <strong>Real fills</strong> to confirm a promising combo.
        </Note>
      )}
      {best.n_trades < 20 && (
        <Note tone="sky">
          <strong>Small sample ({best.n_trades} trades).</strong> Intraday windows are short
          (1h ≈ 3 months, 15m ≈ 1 month of history), so a win rate this thin isn't reliable.
          Add tickers or widen the timeframe.
        </Note>
      )}
      {summary.truncated && (
        <Note tone="sky">
          Hit the signal cap — results cover the most recent signals only. Narrow the
          universe or patterns for a complete run.
        </Note>
      )}

      {/* Window replayed */}
      {summary.start_date && summary.end_date && (
        <div className="text-[11px] text-muted-foreground font-mono">
          Backtested <span className="text-foreground">{summary.start_date}</span> →{' '}
          <span className="text-foreground">{summary.end_date}</span>
          {summary.lookback_days ? ` (${summary.lookback_days} days)` : ''} ·{' '}
          {summary.n_signals} signals over {summary.tickers.length}{' '}
          {summary.tickers.length === 1 ? 'ticker' : 'tickers'}
        </div>
      )}

      {/* Headline metrics for the best config */}
      <section>
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
          {summary.mode === 'optimize' ? 'Best combination' : 'Result'} ·{' '}
          {summary.pricing === 'real' ? 'real fills' : 'BSM'} · {summary.direction}
        </div>
        <div className="text-xs font-mono mb-2">
          {best.delta.toFixed(2)}Δ · {best.dte} DTE · hold {best.hold}{' '}
          {best.hold === 1 ? 'candle' : 'candles'}
          {best.n_synthetic > 0 && (
            <span className="text-amber-500 ml-2">({best.n_synthetic} BSM-fallback)</span>
          )}
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 font-mono">
          <Metric label="Trades" value={String(best.n_trades)} />
          <Metric label="Win rate" value={pct(best.win_rate)} good={best.win_rate >= 0.5} />
          <Metric label="Avg return" value={pct(best.avg_return_pct)} good={best.avg_return_pct >= 0} />
          <Metric label="Expectancy" value={pct(best.expectancy)} good={best.expectancy >= 0} />
          <Metric
            label="Σ P&L /sh"
            value={`$${best.total_pnl.toFixed(2)}`}
            good={best.total_pnl >= 0}
          />
        </div>
      </section>

      <section>
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
          Win / loss
        </div>
        <WinLossBar wins={wins} losses={losses} />
      </section>

      <section>
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
          Cumulative P&L over trade sequence (per share)
        </div>
        <LineChart points={cumulative} baseline={0} yPrefix="$" height={180} />
      </section>

      <section>
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
          Per-trade return distribution
        </div>
        <Histogram values={trades.map((t) => t.return_pct * 100)} prefix="" buckets={20} />
      </section>

      {/* Optimizer ranking */}
      {summary.mode === 'optimize' && summary.configs.length > 1 && (
        <section>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
            All combinations (ranked by expectancy)
          </div>
          <div className="max-h-72 overflow-y-auto rounded border border-border">
            <table className="w-full text-[11px] font-mono">
              <thead className="sticky top-0 bg-background border-b border-border text-muted-foreground">
                <tr>
                  <Th>Δ</Th>
                  <Th>DTE</Th>
                  <Th>Hold</Th>
                  <Th right>Trades</Th>
                  <Th right>Win%</Th>
                  <Th right>Avg ret</Th>
                  <Th right>Expectancy</Th>
                  <Th right>Σ P&L</Th>
                </tr>
              </thead>
              <tbody>
                {summary.configs.map((c, i) => (
                  <tr
                    key={`${c.delta}-${c.dte}-${c.hold}`}
                    className={cls('border-b border-border/40 last:border-0', i === 0 && 'bg-emerald-500/10')}
                  >
                    <Td>{c.delta.toFixed(2)}</Td>
                    <Td>{c.dte}</Td>
                    <Td>{c.hold}</Td>
                    <Td right>{c.n_trades}</Td>
                    <Td right>{pct(c.win_rate)}</Td>
                    <Td right cls={c.avg_return_pct >= 0 ? 'text-emerald-500' : 'text-rose-500'}>
                      {pct(c.avg_return_pct)}
                    </Td>
                    <Td right cls={c.expectancy >= 0 ? 'text-emerald-500' : 'text-rose-500'}>
                      {pct(c.expectancy)}
                    </Td>
                    <Td right cls={c.total_pnl >= 0 ? 'text-emerald-500' : 'text-rose-500'}>
                      ${c.total_pnl.toFixed(2)}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* By pattern */}
      {Object.keys(best.by_pattern).length > 0 && (
        <section>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
            By pattern (best config)
          </div>
          <table className="w-full text-[11px] font-mono">
            <thead className="text-muted-foreground border-b border-border">
              <tr>
                <Th>Pattern</Th>
                <Th right>Trades</Th>
                <Th right>Win%</Th>
                <Th right>Avg ret</Th>
                <Th right>Σ P&L</Th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(best.by_pattern)
                .sort((a, b) => b[1].n - a[1].n)
                .map(([name, s]) => (
                  <tr key={name} className="border-b border-border/40 last:border-0">
                    <Td>{name}</Td>
                    <Td right>{s.n}</Td>
                    <Td right>{pct(s.win_rate)}</Td>
                    <Td right cls={s.avg_return_pct >= 0 ? 'text-emerald-500' : 'text-rose-500'}>
                      {pct(s.avg_return_pct)}
                    </Td>
                    <Td right cls={s.pnl >= 0 ? 'text-emerald-500' : 'text-rose-500'}>
                      ${s.pnl.toFixed(2)}
                    </Td>
                  </tr>
                ))}
            </tbody>
          </table>
        </section>
      )}

      {/* Trades */}
      {trades.length > 0 && (
        <section>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
            Trades ({trades.length})
          </div>
          <div className="max-h-80 overflow-y-auto rounded border border-border">
            <table className="w-full text-[10px] font-mono">
              <thead className="sticky top-0 bg-background border-b border-border text-muted-foreground">
                <tr>
                  <Th>Ticker</Th>
                  <Th>Pattern</Th>
                  <Th>Type</Th>
                  <Th right>Strike</Th>
                  <Th>Entered</Th>
                  <Th>Exited</Th>
                  <Th right>Entry $</Th>
                  <Th right>Exit $</Th>
                  <Th right>Return</Th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr key={i} className="border-b border-border/40 last:border-0">
                    <Td>
                      {t.ticker}
                      {t.synthetic && (
                        <span className="ml-1 text-amber-500" title="BSM fallback (no real fill)">
                          ⚠
                        </span>
                      )}
                    </Td>
                    <Td>{t.pattern}</Td>
                    <Td>{t.option_type}</Td>
                    <Td right>{t.strike.toFixed(1)}</Td>
                    <Td>{t.open_date}</Td>
                    <Td>{t.close_date}</Td>
                    <Td right>${t.entry_premium.toFixed(2)}</Td>
                    <Td right>${t.exit_premium.toFixed(2)}</Td>
                    <Td right cls={t.return_pct >= 0 ? 'text-emerald-500' : 'text-rose-500'}>
                      {(t.return_pct * 100).toFixed(0)}%
                    </Td>
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

// ─── Small UI atoms ──────────────────────────────────────────────────────────

function cls(...xs: (string | false | undefined)[]): string {
  return xs.filter(Boolean).join(' ');
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-4 rounded-lg border border-border p-3">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-2">{title}</div>
      {children}
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{children}</span>;
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

function Toggle({
  label,
  value,
  opts,
  onChange,
}: {
  label: string;
  value: string;
  opts: { v: string; label: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <Label>{label}</Label>
      <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
        {opts.map((o, i) => (
          <button
            key={o.v}
            onClick={() => onChange(o.v)}
            className={cls(
              'px-2.5 py-1',
              i > 0 && 'border-l border-border',
              value === o.v ? 'bg-indigo-500/15 text-indigo-400' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function ChipRow({
  label,
  choices,
  selected,
  onToggle,
  fmt,
}: {
  label: string;
  choices: number[];
  selected: Set<number>;
  onToggle: (v: number) => void;
  fmt: (v: number) => string;
}) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <Label>{label}</Label>
      {choices.map((c) => (
        <button
          key={c}
          onClick={() => onToggle(c)}
          className={cls(
            'px-2 py-0.5 rounded border text-[11px] font-mono',
            selected.has(c)
              ? 'border-indigo-500/60 bg-indigo-500/15 text-indigo-400'
              : 'border-border text-muted-foreground hover:text-foreground',
          )}
        >
          {fmt(c)}
        </button>
      ))}
    </div>
  );
}

function Metric({ label, value, good }: { label: string; value: string; good?: boolean }) {
  return (
    <div className="rounded border border-border px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div
        className={cls(
          'font-semibold text-sm',
          good === true && 'text-emerald-500',
          good === false && 'text-rose-500',
        )}
      >
        {value}
      </div>
    </div>
  );
}

function Note({ tone, children }: { tone: 'amber' | 'sky'; children: React.ReactNode }) {
  const map = {
    amber: 'border-amber-500/30 bg-amber-500/5 text-amber-700 dark:text-amber-400',
    sky: 'border-sky-500/30 bg-sky-500/5 text-sky-700 dark:text-sky-400',
  };
  return <div className={cls('text-xs px-2 py-2 rounded border', map[tone])}>{children}</div>;
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return <th className={cls('px-2 py-1', right ? 'text-right' : 'text-left')}>{children}</th>;
}

function Td({
  children,
  right,
  cls: extra,
}: {
  children: React.ReactNode;
  right?: boolean;
  cls?: string;
}) {
  return <td className={cls('px-2 py-1', right ? 'text-right' : 'text-left', extra)}>{children}</td>;
}
