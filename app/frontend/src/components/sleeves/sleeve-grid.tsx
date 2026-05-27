/**
 * SleeveGrid — 2×2 grid wrapper rendering one SleeveCard per configured sleeve.
 * On viewports below 1280px we collapse to a single column (the project's
 * anti-scope says we don't optimize for mobile, but graceful single-column
 * fallback is free with Tailwind).
 */

import { useSleevesContext } from '@/contexts/sleeves-context';
import { SleeveCard } from './sleeve-card';

export function SleeveGrid() {
  const { config } = useSleevesContext();

  if (!config) {
    return (
      <div className="p-6 text-sm text-muted-foreground">Loading sleeve config…</div>
    );
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 p-6 auto-rows-fr">
      {config.sleeves.map((s) => (
        <SleeveCard key={s.name} sleeve={s} />
      ))}
    </div>
  );
}
