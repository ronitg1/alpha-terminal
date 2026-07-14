/**
 * BacktestValidationCard — shows the vibe-engine-grade statistical validation of
 * a backtest's realized trades: risk-adjusted metrics plus a Monte-Carlo
 * permutation p-value, bootstrap Sharpe CI, and walk-forward consistency, with a
 * one-line plain-English verdict. Mobile-first (stacks / wraps at phone width).
 */
import type { BacktestValidation } from '@/types/patterns';

function fmt(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  return n.toFixed(digits);
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'good' | 'bad' }) {
  return (
    <div className="rounded-md bg-muted/50 px-2 py-1.5">
      <div
        className={
          'text-sm font-semibold font-mono ' +
          (tone === 'good' ? 'text-emerald-500' : tone === 'bad' ? 'text-rose-500' : 'text-foreground')
        }
      >
        {value}
      </div>
      <div className="text-[10px] text-muted-foreground">{label}</div>
    </div>
  );
}

export function BacktestValidationCard({ v }: { v: BacktestValidation | undefined | null }) {
  if (!v) return null;
  if (!v.available) {
    return (
      <div className="rounded-lg border border-border/60 bg-card p-3">
        <p className="text-xs font-semibold text-muted-foreground">Statistical validation</p>
        <p className="mt-1 text-xs text-muted-foreground">{v.reason || 'Not enough trades to validate.'}</p>
      </div>
    );
  }

  const m = v.metrics;
  const mc = v.validation?.monte_carlo;
  const bs = v.validation?.bootstrap;
  const wf = v.validation?.walk_forward;
  const p = mc?.p_value_sharpe;

  // Plain-English read of the Monte-Carlo permutation p-value.
  let verdict = 'Not enough signal to judge.';
  let verdictTone: 'good' | 'bad' | undefined;
  if (typeof p === 'number') {
    if (p <= 0.05) {
      verdict = `p = ${fmt(p, 3)} — the edge is unlikely to be luck (better than ${Math.round((1 - p) * 100)}% of random orderings).`;
      verdictTone = 'good';
    } else if (p <= 0.2) {
      verdict = `p = ${fmt(p, 3)} — some edge, but not conclusive.`;
    } else {
      verdict = `p = ${fmt(p, 3)} — results are consistent with luck (likely noise).`;
      verdictTone = 'bad';
    }
  }

  return (
    <div className="rounded-lg border border-border/60 bg-card p-3 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold">Statistical validation</p>
        {m && <span className="text-[10px] text-muted-foreground">{m.n_trades} trades</span>}
      </div>

      {m && (
        <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-6">
          <Stat label="Sharpe" value={fmt(m.sharpe)} tone={m.sharpe > 0 ? 'good' : 'bad'} />
          <Stat label="Sortino" value={fmt(m.sortino)} />
          <Stat label="Calmar" value={fmt(m.calmar)} />
          <Stat label="Max DD" value={`${fmt(m.max_drawdown * 100, 1)}%`} tone="bad" />
          <Stat label="Win rate" value={`${fmt(m.win_rate * 100, 0)}%`} />
          <Stat label="Profit factor" value={fmt(m.profit_factor)} />
        </div>
      )}

      <div className={'rounded-md px-2 py-1.5 text-xs ' + (verdictTone === 'good' ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' : verdictTone === 'bad' ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400' : 'bg-muted/50 text-muted-foreground')}>
        {verdict}
      </div>

      <div className="grid grid-cols-1 gap-1.5 text-[11px] text-muted-foreground sm:grid-cols-3">
        {bs && !bs.error && (
          <div>
            <span className="font-medium text-foreground">Bootstrap Sharpe</span>: 95% CI [{fmt(bs.ci_lower)}, {fmt(bs.ci_upper)}]
            {typeof bs.prob_positive === 'number' && <> · {Math.round(bs.prob_positive * 100)}% positive</>}
          </div>
        )}
        {wf && !wf.error && typeof wf.consistency_rate === 'number' && (
          <div>
            <span className="font-medium text-foreground">Walk-forward</span>: profitable in {wf.profitable_windows}/{wf.n_windows} windows ({Math.round(wf.consistency_rate * 100)}%)
          </div>
        )}
        {mc && !mc.error && (
          <div>
            <span className="font-medium text-foreground">Monte-Carlo</span>: {mc.n_trades} trades, {mc.n_trades ? '' : ''}p={fmt(p, 3)}
          </div>
        )}
      </div>
    </div>
  );
}
