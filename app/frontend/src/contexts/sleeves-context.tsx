/**
 * SleevesContext — single source of truth for the Sleeves Dashboard.
 *
 * Phase 1 owns:
 *   • latestScan       — parsed rows from the most recent scan
 *   • config           — sleeve definitions (allocations, agent panels, tickers)
 *   • scanStatus       — 'idle' | 'loading' | 'error'   (Phase 2 adds 'running' / 'complete')
 *   • selectedTicker   — ticker shown in the drill drawer (drawer ships Phase 3)
 *
 * Phase 2 will extend this with `liveActivity`, `agentProgress`, `runScan()`.
 * Phase 3 will add `watchlist` and `saveWatchlist()`.
 */

import { sleevesApi } from '@/services/sleeves-api';
import { ScanSummary, SleevesConfig } from '@/types/sleeves';
import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react';

type ScanStatus = 'idle' | 'loading' | 'error';

interface SleevesContextType {
  config: SleevesConfig | null;
  latestScan: ScanSummary | null;
  scanStatus: ScanStatus;
  scanError: string | null;
  selectedTicker: string | null;
  selectTicker: (ticker: string | null) => void;
  refresh: () => Promise<void>;
}

const SleevesContext = createContext<SleevesContextType | null>(null);

export function useSleevesContext(): SleevesContextType {
  const ctx = useContext(SleevesContext);
  if (!ctx) {
    throw new Error('useSleevesContext must be used within a SleevesProvider');
  }
  return ctx;
}

export function SleevesProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<SleevesConfig | null>(null);
  const [latestScan, setLatestScan] = useState<ScanSummary | null>(null);
  const [scanStatus, setScanStatus] = useState<ScanStatus>('idle');
  const [scanError, setScanError] = useState<string | null>(null);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setScanStatus('loading');
    setScanError(null);
    try {
      // Config + latest scan in parallel. Config rarely changes but is cheap to refetch.
      const [cfg, scan] = await Promise.all([
        sleevesApi.getConfig(),
        sleevesApi
          .getLatestScan()
          .catch((err: Error) => {
            // 404 here just means "no scans yet" — surface as null, not an error.
            if (/404/.test(err.message)) {
              return null;
            }
            throw err;
          }),
      ]);
      setConfig(cfg);
      setLatestScan(scan);
      setScanStatus('idle');
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setScanError(msg);
      setScanStatus('error');
      // Keep whatever data we had — partial state beats blank page.
      console.error('SleevesProvider refresh failed:', err);
    }
  }, []);

  // Initial load on mount. Re-runs of this provider remount the dashboard.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value: SleevesContextType = {
    config,
    latestScan,
    scanStatus,
    scanError,
    selectedTicker,
    selectTicker: setSelectedTicker,
    refresh,
  };

  return <SleevesContext.Provider value={value}>{children}</SleevesContext.Provider>;
}
