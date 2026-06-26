/**
 * UniversePicker — a single dropdown that lets the user pick the ticker
 * universe for the Options Screener and the Options-Strategy backtest from
 * either a portfolio (sleeve) or a watchlist.
 *
 * The value is a string encoding both the kind and the name as
 * ``"<source>:<name>"`` (e.g. ``"sleeve:mega_tech"``, ``"watchlist:My List"``)
 * so a single <select> can hold both groups. Use {@link parseUniverse} to
 * split it back into ``{ source, name }`` before calling the API.
 *
 * Portfolios and watchlists both come from the shared SleevesContext, which
 * already loads them on mount — no extra fetching here.
 */

import { useSleevesContext } from '@/contexts/sleeves-context';

export type UniverseSource = 'sleeve' | 'watchlist';

export interface ParsedUniverse {
  source: UniverseSource;
  name: string;
}

/** Split a ``"<source>:<name>"`` value. Bare values (no colon) are treated
 *  as a sleeve name for backward-compatibility. */
export function parseUniverse(value: string): ParsedUniverse {
  const idx = value.indexOf(':');
  if (idx === -1) return { source: 'sleeve', name: value };
  const source = value.slice(0, idx);
  return {
    source: source === 'watchlist' ? 'watchlist' : 'sleeve',
    name: value.slice(idx + 1),
  };
}

/** Build the encoded value for a (source, name) pair. */
export function makeUniverse(source: UniverseSource, name: string): string {
  return `${source}:${name}`;
}

interface UniversePickerProps {
  value: string;
  onChange: (value: string) => void;
  className?: string;
  disabled?: boolean;
}

export function UniversePicker({
  value,
  onChange,
  className,
  disabled,
}: UniversePickerProps) {
  const { config, watchlists } = useSleevesContext();
  const sleeves = config?.sleeves ?? [];

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      className={
        className ??
        'bg-background border border-border rounded px-2 py-1 text-sm font-mono'
      }
    >
      <optgroup label="My Portfolios">
        {sleeves.map((s) => (
          <option key={`sleeve:${s.name}`} value={`sleeve:${s.name}`}>
            {s.name.replace(/_/g, ' ')}
          </option>
        ))}
      </optgroup>
      {watchlists.length > 0 && (
        <optgroup label="Watchlists">
          {watchlists.map((w) => (
            <option key={`watchlist:${w.name}`} value={`watchlist:${w.name}`}>
              {w.name} ({w.tickers.length})
            </option>
          ))}
        </optgroup>
      )}
    </select>
  );
}
