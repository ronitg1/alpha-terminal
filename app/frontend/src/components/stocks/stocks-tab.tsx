/**
 * StocksTab — My Stocks dashboard.
 *
 * Editable list of tickers, persisted in localStorage. Each ticker
 * renders as a StockCard with its own timeframe selector. Add ticker via
 * the input at the top; remove + reorder per-card.
 *
 * Wrapped in SleevesProvider so cards can echo signals from latestScan
 * and use the same run-scan + select-ticker plumbing as the Sleeves tab.
 */
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { SleevesProvider } from '@/contexts/sleeves-context';
import { LayoutGrid, Plus, X } from 'lucide-react';
import { useState } from 'react';
import { StockCard } from './stock-card';
import { useMyStocks } from './use-my-stocks';

export function StocksTab() {
  return (
    <SleevesProvider>
      <StocksTabContent />
    </SleevesProvider>
  );
}

function StocksTabContent() {
  const { tickers, add, remove, move } = useMyStocks();
  const [input, setInput] = useState('');
  const [error, setError] = useState<string | null>(null);

  const handleAdd = () => {
    const trimmed = input.trim().toUpperCase();
    if (!trimmed) return;
    const ok = add(trimmed);
    if (!ok) {
      setError(
        `"${trimmed}" doesn't look like a valid ticker (1-10 uppercase letters/digits, optional . or -)`,
      );
      return;
    }
    setInput('');
    setError(null);
  };

  return (
    <div className="h-full w-full overflow-y-auto bg-background">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-background border-b border-border px-6 py-3">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <LayoutGrid className="h-4 w-4 text-muted-foreground" />
            <h1 className="text-base font-semibold tracking-tight">My Stocks</h1>
            <span className="text-xs text-muted-foreground font-mono">
              {tickers.length} ticker{tickers.length === 1 ? '' : 's'}
            </span>
          </div>

          <div className="flex items-center gap-2">
            <Input
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                setError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleAdd();
              }}
              placeholder="Add ticker (e.g. PLTR)"
              className="h-8 w-44 text-sm font-mono uppercase"
              autoFocus={tickers.length === 0}
            />
            <Button size="sm" onClick={handleAdd}>
              <Plus className="h-3.5 w-3.5 mr-1" />
              Add
            </Button>
          </div>
        </div>
        {error && (
          <div className="mt-1.5 text-xs px-2 py-1 rounded border border-rose-500/30 bg-rose-500/5 text-rose-700 dark:text-rose-400 flex items-start gap-1.5">
            <X className="h-3 w-3 mt-0.5 flex-shrink-0" />
            {error}
          </div>
        )}
        <p className="mt-1.5 text-[11px] text-muted-foreground">
          Cards are persisted in your browser. Each card's timeframe is
          independent — pick 1W, 1M, 3M, 6M, YTD, 1Y, or 2Y per ticker. The
          big % chip on each card reflects the selected window; the smaller
          one next to price is 1-day.
        </p>
      </div>

      {/* Grid */}
      {tickers.length === 0 ? (
        <div className="flex items-center justify-center h-[60vh] text-center text-muted-foreground">
          <div className="space-y-2">
            <LayoutGrid className="h-10 w-10 mx-auto opacity-50" />
            <p className="text-sm">
              Add a ticker above to start building your stocks dashboard.
            </p>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 p-4">
          {tickers.map((t, i) => (
            <StockCard
              key={t}
              ticker={t}
              onRemove={() => remove(t)}
              onMoveUp={() => move(t, 'up')}
              onMoveDown={() => move(t, 'down')}
              canMoveUp={i > 0}
              canMoveDown={i < tickers.length - 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}
