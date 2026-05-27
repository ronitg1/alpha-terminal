/**
 * SignalPill — small badge rendering a bullish/bearish/neutral signal
 * with optional confidence. The colors map to the project's highlight
 * semantics: green=bullish, red=bearish, muted=neutral.
 */

import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { Signal } from '@/types/sleeves';
import { ArrowDown, ArrowUp, Minus } from 'lucide-react';

interface SignalPillProps {
  signal: Signal;
  confidence?: number;
  /** When true, render compact (no confidence text). */
  compact?: boolean;
  className?: string;
}

const SIGNAL_STYLES: Record<Signal, { className: string; label: string }> = {
  bullish: {
    className: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-600/30 hover:bg-emerald-500/20',
    label: 'BULL',
  },
  bearish: {
    className: 'bg-rose-500/15 text-rose-700 dark:text-rose-400 border-rose-600/30 hover:bg-rose-500/20',
    label: 'BEAR',
  },
  neutral: {
    className: 'bg-muted text-muted-foreground border-border hover:bg-muted/80',
    label: 'NEUT',
  },
};

const SIGNAL_ICON: Record<Signal, React.ComponentType<{ className?: string }>> = {
  bullish: ArrowUp,
  bearish: ArrowDown,
  neutral: Minus,
};

export function SignalPill({ signal, confidence, compact, className }: SignalPillProps) {
  const style = SIGNAL_STYLES[signal];
  const Icon = SIGNAL_ICON[signal];
  return (
    <Badge variant="outline" className={cn('gap-1 font-mono text-xs', style.className, className)}>
      <Icon className="h-3 w-3" />
      <span>{style.label}</span>
      {!compact && confidence !== undefined && (
        <span className="opacity-70">{Math.round(confidence)}</span>
      )}
    </Badge>
  );
}
