/**
 * Portfolio tab — a two-view (Summary / Positions) portfolio across every
 * connected brokerage. An account switcher chooses one account or "All accounts"
 * (combined). When nothing is connected it shows a connect prompt. Fully
 * responsive for iOS (convention #8): the header wraps, tabs stay tappable, and
 * the positions grid reflows to cards on narrow screens.
 */
import { SnapTradeConnect } from '@/components/dashboard/snaptrade-connect';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { portfolioApi } from '@/services/portfolio-api';
import type { PortfolioAccount, PortfolioOverview } from '@/types/portfolio';
import { RefreshCw, Wallet } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { PortfolioSummary } from './portfolio-summary';
import { PositionsTable } from './positions-table';

const COMBINED_ID = '__combined__';

function EmptyState() {
  return (
    <div className="mx-auto max-w-xl space-y-4 py-8">
      <div className="text-center">
        <Wallet className="mx-auto h-8 w-8 text-muted-foreground" />
        <h2 className="mt-2 text-lg font-semibold">Connect a brokerage</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Connect SnapTrade (Fidelity and more) or Robinhood to see your holdings, gains, and
          allocation here. Everything is read-only.
        </p>
      </div>
      <SnapTradeConnect />
      <p className="text-center text-xs text-muted-foreground">
        To include Robinhood, add a Robinhood MCP token in Settings → API keys.
      </p>
    </div>
  );
}

export function PortfolioTab() {
  const [overview, setOverview] = useState<PortfolioOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string>(COMBINED_ID);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setOverview(await portfolioApi.getOverview());
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
      setOverview(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Choose a sensible default account once data arrives: combined if it exists,
  // else the first account. Only reset when the current selection is invalid.
  const accounts = overview?.accounts ?? [];
  const combined = overview?.combined ?? null;

  useEffect(() => {
    const validIds = new Set<string>([...(combined ? [COMBINED_ID] : []), ...accounts.map((a) => a.id)]);
    if (!validIds.has(selectedId)) {
      setSelectedId(combined ? COMBINED_ID : accounts[0]?.id ?? COMBINED_ID);
    }
  }, [accounts, combined, selectedId]);

  const selected: PortfolioAccount | null = useMemo(() => {
    if (selectedId === COMBINED_ID) return combined ?? accounts[0] ?? null;
    return accounts.find((a) => a.id === selectedId) ?? combined ?? accounts[0] ?? null;
  }, [selectedId, accounts, combined]);

  if (loading && !overview) {
    return <div className="p-6 text-sm text-muted-foreground">Loading portfolio…</div>;
  }

  if (!overview?.connected || !selected) {
    return (
      <div className="h-full overflow-y-auto p-4">
        <EmptyState />
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-3 sm:p-4">
      <div className="mx-auto max-w-6xl space-y-3">
        {/* Header: title + account switcher + refresh — wraps on iOS */}
        <div className="flex flex-wrap items-center gap-2">
          <Wallet className="h-4 w-4 text-muted-foreground" />
          <h1 className="text-base font-semibold">Portfolio</h1>
          <select
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
            className="h-8 rounded-md border border-input bg-background px-2 text-xs"
          >
            {combined && <option value={COMBINED_ID}>All accounts</option>}
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => void load()}
            className="ml-auto inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-muted"
          >
            <RefreshCw className={loading ? 'h-3 w-3 animate-spin' : 'h-3 w-3'} /> Refresh
          </button>
        </div>

        <Tabs defaultValue="summary">
          <TabsList>
            <TabsTrigger value="summary">Summary</TabsTrigger>
            <TabsTrigger value="positions">Positions</TabsTrigger>
          </TabsList>
          <TabsContent value="summary">
            <PortfolioSummary account={selected} />
          </TabsContent>
          <TabsContent value="positions">
            <div className="rounded-lg border border-border/60 bg-card p-2 sm:p-3">
              <PositionsTable positions={selected.positions} />
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
