/**
 * AnalystChip — agent badge wrapped in a tooltip that surfaces the
 * analyst's display_name, description, and investing_style.
 *
 * Use everywhere we render an agent name: sleeve card panel headers,
 * drill drawer accordion headers, live activity feed rows. Replaces the
 * bare <Badge> agent-name rendering that used to live inline.
 *
 * Falls back to a plain badge with no tooltip when analyst metadata
 * hasn't been loaded yet (or 404s) — the UI doesn't break.
 */

import { Badge } from '@/components/ui/badge';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { cn } from '@/lib/utils';

interface AnalystChipProps {
  /** Canonical analyst key (matches ANALYST_CONFIG keys, e.g. 'aswath_damodaran'). */
  agentKey: string;
  /** Optional weight (0-1) — rendered as "33%" suffix when present. */
  weight?: number;
  /** Visual variant — 'badge' (default) is the outlined Shadcn badge; 'inline' is
   *  unboxed text for spots where another container already provides chrome. */
  variant?: 'badge' | 'inline';
  className?: string;
}

function shortLabel(agentKey: string, displayName: string | undefined): string {
  if (displayName) return displayName;
  // Fall back to the snake_case key cleaned up. Strip the redundant "_analyst"
  // suffix (e.g. fundamentals_analyst → fundamentals) since the chip already
  // visually communicates that this IS an analyst.
  return agentKey.replace(/_analyst$/, '').replace(/_/g, ' ');
}

export function AnalystChip({ agentKey, weight, variant = 'badge', className }: AnalystChipProps) {
  const { analystMeta } = useSleevesContext();
  const meta = analystMeta[agentKey];
  const label = shortLabel(agentKey, meta?.display_name);
  const hasTooltip = !!meta;

  const inner = (
    <span className={cn('inline-flex items-center gap-1', className)}>
      <span>{label}</span>
      {weight !== undefined && (
        <span className="opacity-60">{Math.round(weight * 100)}%</span>
      )}
    </span>
  );

  const trigger =
    variant === 'badge' ? (
      <Badge
        variant="outline"
        className={cn(
          'text-[10px] font-mono px-1.5 py-0',
          hasTooltip && 'cursor-help',
          className
        )}
      >
        {inner}
      </Badge>
    ) : (
      <span className={cn('font-mono', hasTooltip && 'cursor-help underline-offset-2 hover:underline')}>
        {inner}
      </span>
    );

  if (!hasTooltip) {
    return trigger;
  }

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>{trigger}</TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs">
          <div className="space-y-1">
            <div className="font-semibold text-sm">{meta.display_name}</div>
            <div className="text-xs text-muted-foreground italic">{meta.description}</div>
            <div className="text-xs leading-relaxed">{meta.investing_style}</div>
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
