/**
 * TrafficLight — atom for clean / amber / red status fields.
 *
 * Used in the drill drawer for FEOC risk and any future field with the
 * same semantics. Wraps Badge with cva-style variants so callers stay terse.
 *
 * When status === 'unknown' the badge is wrapped in a Tooltip explaining
 * that the underlying data source doesn't expose enough to classify the
 * field — removes the impression that the system is broken when really
 * it just doesn't have supplier-chain disclosures (e.g. FEOC).
 */

import { Badge } from '@/components/ui/badge';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

type Status = 'clean' | 'amber' | 'red' | 'unknown' | string;

interface TrafficLightProps {
  status: Status;
  label?: string;
  /** Field name for the unknown-state explainer (defaults to "this field"). */
  field?: string;
  /** Override the default unknown explainer copy entirely. */
  unknownHint?: string;
  className?: string;
}

const STYLES: Record<string, string> = {
  clean: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-600/30',
  amber: 'bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-600/30',
  red: 'bg-rose-500/15 text-rose-700 dark:text-rose-400 border-rose-600/30',
  unknown: 'bg-muted text-muted-foreground border-border',
};

const DEFAULT_UNKNOWN_HINTS: Record<string, string> = {
  'feoc risk':
    'FEOC scoring requires supplier-chain data not exposed by Massive or financialdatasets.ai. ' +
    'Agents flag concerns from news flow when present, but won\'t auto-classify "clean/amber/red" without primary disclosures.',
};

export function TrafficLight({ status, label, field, unknownHint, className }: TrafficLightProps) {
  const style = STYLES[status] ?? STYLES.unknown;
  const badge = (
    <Badge
      variant="outline"
      className={cn(
        'gap-1.5 font-mono text-xs',
        style,
        status === 'unknown' && 'cursor-help',
        className
      )}
    >
      <span className="inline-block h-2 w-2 rounded-full bg-current" />
      <span>{label ?? status.toUpperCase()}</span>
    </Badge>
  );

  if (status !== 'unknown') return badge;

  const hint =
    unknownHint ??
    (field ? DEFAULT_UNKNOWN_HINTS[field.toLowerCase()] : undefined) ??
    `Data source doesn't expose enough to classify ${field ?? 'this field'} ` +
      `as clean / amber / red. Treat the absence as "no signal," not "broken."`;

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>{badge}</TooltipTrigger>
        <TooltipContent side="top" className="max-w-sm">
          <div className="text-xs leading-relaxed">{hint}</div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
