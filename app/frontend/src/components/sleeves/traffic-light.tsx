/**
 * TrafficLight — atom for clean / amber / red status fields.
 *
 * Used in the drill drawer for FEOC risk and any future field with the
 * same semantics. Wraps Badge with cva-style variants so callers stay terse.
 */

import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

type Status = 'clean' | 'amber' | 'red' | 'unknown' | string;

interface TrafficLightProps {
  status: Status;
  label?: string;
  className?: string;
}

const STYLES: Record<string, string> = {
  clean: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-600/30',
  amber: 'bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-600/30',
  red: 'bg-rose-500/15 text-rose-700 dark:text-rose-400 border-rose-600/30',
  unknown: 'bg-muted text-muted-foreground border-border',
};

export function TrafficLight({ status, label, className }: TrafficLightProps) {
  const style = STYLES[status] ?? STYLES.unknown;
  return (
    <Badge variant="outline" className={cn('gap-1.5 font-mono text-xs', style, className)}>
      <span className="inline-block h-2 w-2 rounded-full bg-current" />
      <span>{label ?? status.toUpperCase()}</span>
    </Badge>
  );
}
