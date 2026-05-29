/**
 * WinLossBar — single horizontal bar split into win / loss / breakeven
 * segments, sized by trade count. Centered text shows the win rate.
 *
 * Useful as a one-glance summary above a P&L distribution.
 */

import { cn } from '@/lib/utils';

interface WinLossBarProps {
  wins: number;
  losses: number;
  breakevens?: number;
  className?: string;
}

export function WinLossBar({ wins, losses, breakevens = 0, className }: WinLossBarProps) {
  const total = wins + losses + breakevens;
  if (!total) {
    return (
      <div
        className={cn(
          'flex items-center justify-center text-xs text-muted-foreground italic h-7 border border-dashed rounded',
          className,
        )}
      >
        No trades.
      </div>
    );
  }
  const winPct = (wins / total) * 100;
  const losePct = (losses / total) * 100;
  const bePct = (breakevens / total) * 100;
  const winRate = wins / total;

  return (
    <div className={cn('w-full', className)}>
      <div className="flex h-7 w-full overflow-hidden rounded border border-border bg-muted/30">
        {wins > 0 && (
          <div
            className="bg-emerald-500/80 text-white text-[10px] font-mono flex items-center justify-center"
            style={{ width: `${winPct}%` }}
            title={`${wins} winning trade${wins === 1 ? '' : 's'}`}
          >
            {winPct >= 12 && `${wins} W`}
          </div>
        )}
        {breakevens > 0 && (
          <div
            className="bg-muted-foreground/50 text-white text-[10px] font-mono flex items-center justify-center"
            style={{ width: `${bePct}%` }}
            title={`${breakevens} break-even trade${breakevens === 1 ? '' : 's'}`}
          >
            {bePct >= 12 && `${breakevens} BE`}
          </div>
        )}
        {losses > 0 && (
          <div
            className="bg-rose-500/80 text-white text-[10px] font-mono flex items-center justify-center"
            style={{ width: `${losePct}%` }}
            title={`${losses} losing trade${losses === 1 ? '' : 's'}`}
          >
            {losePct >= 12 && `${losses} L`}
          </div>
        )}
      </div>
      <div className="mt-1 text-[10px] text-muted-foreground tabular-nums">
        Win rate {(winRate * 100).toFixed(1)}% · {wins} W / {losses} L
        {breakevens > 0 && ` / ${breakevens} BE`} · {total} total trades
      </div>
    </div>
  );
}
