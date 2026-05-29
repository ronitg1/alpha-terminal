/**
 * PositionsSection — wraps the vertical list of SleeveSection cards.
 *
 * Defaults the highest-allocation sleeve to open (most useful for the user
 * on landing). All other sleeves render collapsed; user click on the
 * caret expands inline. Keyed by sleeve.name so React re-renders happen
 * cleanly when sleeves are added/removed via Manage sleeves.
 */
import { useSleevesContext } from '@/contexts/sleeves-context';
import { useMemo } from 'react';
import { SleeveSection } from './sleeve-section';

export function PositionsSection() {
  const { config } = useSleevesContext();
  const sleeves = config?.sleeves ?? [];

  // Highest-allocation sleeve opens by default. Stable per-config so the
  // initial-open decision survives re-renders of unrelated state.
  const defaultOpenName = useMemo(() => {
    if (sleeves.length === 0) return null;
    return [...sleeves].sort(
      (a, b) => b.allocation_pct - a.allocation_pct,
    )[0].name;
  }, [sleeves]);

  if (sleeves.length === 0) return null;

  return (
    <div className="px-6 pb-8 space-y-3">
      <div className="flex items-baseline justify-between gap-3 mt-2">
        <h2 className="text-[10px] uppercase tracking-wide text-muted-foreground">
          Positions
        </h2>
        <span className="text-[10px] text-muted-foreground">
          Click a row → detail · Run/Stop per row scoped to that ticker
        </span>
      </div>
      {sleeves.map((s) => (
        <SleeveSection
          key={s.name}
          sleeve={s}
          defaultOpen={s.name === defaultOpenName}
        />
      ))}
    </div>
  );
}
