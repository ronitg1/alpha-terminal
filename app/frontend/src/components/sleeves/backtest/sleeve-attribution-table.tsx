/**
 * SleeveAttributionTable — renders the per-sleeve + per-agent attribution
 * blocks emitted on the D1 backtest 'complete' event.
 *
 * Plain compact tables, no styling drama — this is a reference report,
 * not a hero element.
 */

import { Badge } from '@/components/ui/badge';
import { BacktestAttribution } from '@/types/sleeves';

interface SleeveAttributionTableProps {
  attribution: BacktestAttribution;
}

export function SleeveAttributionTable({ attribution }: SleeveAttributionTableProps) {
  const sleeves = Object.entries(attribution.sleeves);
  const agents = Object.entries(attribution.agents);

  if (attribution.n_trades === 0) {
    return (
      <div className="text-xs text-muted-foreground italic px-2 py-3 rounded border border-dashed">
        No closed trades to attribute. (Backtest may have been too short for any
        buy → sell cycle.)
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <section>
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
          Per-sleeve performance
        </div>
        <table className="w-full text-[11px] font-mono">
          <thead>
            <tr className="text-muted-foreground border-b border-border">
              <th className="text-left px-2 py-1">Sleeve</th>
              <th className="text-right px-2 py-1">Trades</th>
              <th className="text-right px-2 py-1">Win%</th>
              <th className="text-right px-2 py-1">Hold</th>
              <th className="text-right px-2 py-1">Sharpe</th>
              <th className="text-right px-2 py-1">MaxDD</th>
              <th className="text-right px-2 py-1">Total P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {sleeves
              .sort(([, a], [, b]) => b.total_pnl - a.total_pnl)
              .map(([sleeve, m]) => (
                <tr key={sleeve} className="border-b border-border/40 last:border-0">
                  <td className="px-2 py-1">{sleeve.replace(/_/g, ' ')}</td>
                  <td className="px-2 py-1 text-right">{m.n_trades}</td>
                  <td className="px-2 py-1 text-right">{(m.win_rate * 100).toFixed(1)}%</td>
                  <td className="px-2 py-1 text-right">{m.avg_hold_days.toFixed(1)}d</td>
                  <td className="px-2 py-1 text-right">
                    {m.sharpe === null ? '—' : m.sharpe.toFixed(2)}
                  </td>
                  <td className="px-2 py-1 text-right">${m.max_drawdown.toFixed(0)}</td>
                  <td
                    className={`px-2 py-1 text-right ${
                      m.total_pnl >= 0 ? 'text-emerald-500' : 'text-rose-500'
                    }`}
                  >
                    ${m.total_pnl.toFixed(0)}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </section>

      <section>
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
          Agent attribution
        </div>
        <table className="w-full text-[11px] font-mono">
          <thead>
            <tr className="text-muted-foreground border-b border-border">
              <th className="text-left px-2 py-1">Agent</th>
              <th className="text-right px-2 py-1">Trades</th>
              <th className="text-right px-2 py-1">Win%</th>
              <th className="text-right px-2 py-1">Avg Ret</th>
              <th className="text-right px-2 py-1">P&amp;L attributed</th>
            </tr>
          </thead>
          <tbody>
            {agents
              .sort(([, a], [, b]) => b.total_pnl_attributed - a.total_pnl_attributed)
              .map(([agent, a]) => (
                <tr key={agent} className="border-b border-border/40 last:border-0">
                  <td className="px-2 py-1">{agent}</td>
                  <td className="px-2 py-1 text-right">{a.n_trades}</td>
                  <td className="px-2 py-1 text-right">{(a.win_rate * 100).toFixed(1)}%</td>
                  <td className="px-2 py-1 text-right">
                    {(a.avg_return_pct * 100).toFixed(1)}%
                  </td>
                  <td
                    className={`px-2 py-1 text-right ${
                      a.total_pnl_attributed >= 0 ? 'text-emerald-500' : 'text-rose-500'
                    }`}
                  >
                    ${a.total_pnl_attributed.toFixed(0)}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </section>

      {attribution.warnings.length > 0 && (
        <section>
          <div className="text-[10px] uppercase tracking-wide text-amber-700 dark:text-amber-400 mb-1">
            Underperforming agents
          </div>
          <ul className="space-y-1">
            {attribution.warnings.map((w, i) => (
              <li key={i} className="text-xs">
                <Badge
                  variant="outline"
                  className="border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400 mr-2"
                >
                  warn
                </Badge>
                {w}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
