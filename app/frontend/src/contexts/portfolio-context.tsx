/**
 * PortfolioProvider — one shared fetch of GET /portfolio/overview for the whole
 * dashboard. Both the left nav ("My Portfolios") and the Portfolio tab need it;
 * fetching it in each meant the expensive overview endpoint ran twice per session
 * and the two copies could drift. This owns it once and exposes `refresh()` so the
 * tab's Refresh button / Add-account flow update the nav too.
 *
 * Errors are swallowed (best-effort): an unconnected user returns
 * `{connected: false}` — a normal response, not an error — so the shell never
 * flashes an error on a cold load.
 */
import { portfolioApi } from '@/services/portfolio-api';
import type { PortfolioOverview } from '@/types/portfolio';
import { createContext, useCallback, useContext, useEffect, useState } from 'react';

interface PortfolioContextValue {
  overview: PortfolioOverview | null;
  loading: boolean;
  refresh: () => Promise<void>;
}

const PortfolioContext = createContext<PortfolioContextValue | null>(null);

export function PortfolioProvider({ children }: { children: React.ReactNode }) {
  const [overview, setOverview] = useState<PortfolioOverview | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setOverview(await portfolioApi.getOverview());
    } catch {
      // best-effort; keep any prior data and let consumers show their empty state
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <PortfolioContext.Provider value={{ overview, loading, refresh }}>
      {children}
    </PortfolioContext.Provider>
  );
}

export function usePortfolio(): PortfolioContextValue {
  const ctx = useContext(PortfolioContext);
  if (!ctx) throw new Error('usePortfolio must be used within a PortfolioProvider');
  return ctx;
}
