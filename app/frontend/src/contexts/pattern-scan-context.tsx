/**
 * PatternScanContext — owns the Pattern Scanner's scan lifecycle so a running
 * scan (and its results) survive navigating away from the Screening page.
 *
 * The scan is a plain `fetch` that the browser does NOT abort on component
 * unmount — but historically the results landed in `PatternsTab`'s local state,
 * which is torn down the moment you switch section/sub-tab, so the response was
 * discarded and the UI reset. Mounting this provider ABOVE MainContent (in
 * DashboardLayout) keeps the scan state alive across navigation: you can kick
 * off a scan, go look at another tab, and come back to the finished results —
 * or get a toast when it completes while you're elsewhere.
 *
 * Scope: this survives in-app navigation while the browser tab stays open. A
 * full page reload / tab close still loses an in-flight scan (there is no
 * server-side job for on-demand scans — only the scheduled pre-scans persist).
 */
import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';
import { toast } from 'sonner';

import { useDashboard } from '@/contexts/dashboard-context';
import { getSignalAnalysis, scanTickers } from '@/services/patterns-api';
import { scheduledApi } from '@/services/scheduled-api';
import type { HistoricalStats, PatternTimeframe, ScanResult } from '@/types/patterns';

export interface RunScanArgs {
  tickers: string[];
  patterns: string[];
  lookback: number;
  timeframe: PatternTimeframe;
}

interface PatternScanContextType {
  results: ScanResult[];
  timeframe: PatternTimeframe;
  isScanning: boolean;
  /** How many names the currently-running scan covers (0 when idle). */
  scanningCount: number;
  winRates: Map<string, HistoricalStats>;
  prescanAt: string | null;
  /** Start a scan. Fire-and-forget — it keeps running if you navigate away. */
  runScan: (args: RunScanArgs) => void;
}

const PatternScanContext = createContext<PatternScanContextType | null>(null);

export function PatternScanProvider({ children }: { children: ReactNode }) {
  const { section, screeningSubTab, setSection, setScreeningSubTab } = useDashboard();

  const [results, setResults] = useState<ScanResult[]>([]);
  const [timeframe, setTimeframe] = useState<PatternTimeframe>('day');
  const [isScanning, setIsScanning] = useState(false);
  const [scanningCount, setScanningCount] = useState(0);
  const [winRates, setWinRates] = useState<Map<string, HistoricalStats>>(new Map());
  const [prescanAt, setPrescanAt] = useState<string | null>(null);

  // Monotonic id so only the most-recent scan's response is allowed to land
  // (an older, slower scan resolving after a newer one must not clobber it).
  const scanSeq = useRef(0);
  // Once a manual scan has been kicked off we never auto-adopt a pre-scan.
  const manualStarted = useRef(false);

  // Track whether the user is currently looking at the Pattern Scanner, so a
  // background completion can decide whether to grab their attention.
  const viewing = section === 'screening' && screeningSubTab === 'patterns';
  const viewingRef = useRef(viewing);
  viewingRef.current = viewing;

  const runScan = useCallback(
    ({ tickers, patterns, lookback, timeframe: tf }: RunScanArgs) => {
      const myId = ++scanSeq.current;
      manualStarted.current = true;
      setIsScanning(true);
      setScanningCount(tickers.length);
      // A manual scan supersedes any shown pre-scan straight away: drop the
      // "pre-scan from yesterday" banner and clear the old rows now, so a slow
      // scan (still running when you navigate back) or a failed one never leaves
      // yesterday's pre-scan results on screen looking like this scan's output.
      setPrescanAt(null);
      setResults([]);
      setWinRates(new Map());
      if (tickers.length > 120) {
        toast.info(
          `Scanning ${tickers.length} names — large scans can take a minute or two. ` +
            'You can keep using the app; the scan runs in the background.',
        );
      }
      // Deliberately NOT awaited by any component: the promise is owned by this
      // long-lived provider, so unmounting the scanner never drops the result.
      void (async () => {
        try {
          const rows = await scanTickers(tickers, patterns, lookback, tf);
          if (myId !== scanSeq.current) return; // superseded by a newer scan
          setTimeframe(tf);
          setResults(rows);
          setPrescanAt(null); // a manual scan supersedes the background pre-scan
          if (!viewingRef.current) {
            toast.success(
              `Pattern scan complete — ${rows.length} signal${rows.length === 1 ? '' : 's'}.`,
              {
                action: {
                  label: 'View',
                  onClick: () => {
                    setSection('screening');
                    setScreeningSubTab('patterns');
                  },
                },
              },
            );
          }
        } catch (err) {
          if (myId !== scanSeq.current) return;
          console.warn('Scan failed:', err);
          const isTimeout = err instanceof DOMException && err.name === 'TimeoutError';
          toast.error(
            isTimeout
              ? `Scan timed out on ${tickers.length} names — try fewer tickers or a single watchlist.`
              : 'Scan failed — check that the backend is running.',
          );
        } finally {
          if (myId === scanSeq.current) {
            setIsScanning(false);
            setScanningCount(0);
          }
        }
      })();
    },
    [setSection, setScreeningSubTab],
  );

  // On first app load, adopt the user's latest background pre-scan so results
  // are ready instantly — unless they've already started a manual scan. Runs
  // once for the app's lifetime (this provider never unmounts), so revisiting
  // the tab no longer refetches the pre-scan.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const pre = await scheduledApi.getPrescan();
        if (cancelled || manualStarted.current || !pre || !pre.results?.length) return;
        setResults((cur) => (cur.length ? cur : pre.results));
        setTimeframe((pre.timeframe as PatternTimeframe) ?? 'day');
        setPrescanAt(pre.computed_at);
      } catch {
        // no pre-scan, or not signed in — ignore
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Background-fetch win rates for every unique ticker+pattern pair after each
  // scan. Lives here (not in the tab) so results already have their win rates
  // when you return to the Pattern Scanner instead of refetching on remount.
  useEffect(() => {
    if (results.length === 0) {
      setWinRates(new Map());
      return;
    }

    const seen = new Set<string>();
    const pairs: { ticker: string; pattern: string; key: string }[] = [];
    for (const r of results) {
      const key = `${r.ticker}:${r.pattern}`;
      if (!seen.has(key)) {
        seen.add(key);
        pairs.push({ ticker: r.ticker, pattern: r.pattern, key });
      }
    }

    let cancelled = false;
    setWinRates(new Map());

    const fetchAll = async () => {
      const BATCH = 5;
      for (let i = 0; i < pairs.length && !cancelled; i += BATCH) {
        await Promise.all(
          pairs.slice(i, i + BATCH).map(async ({ ticker, pattern, key }) => {
            try {
              const data = await getSignalAnalysis(ticker, pattern, timeframe);
              if (!cancelled) setWinRates((prev) => new Map(prev).set(key, data.historical));
            } catch {
              // skip on error — non-critical
            }
          }),
        );
      }
    };

    void fetchAll();
    return () => {
      cancelled = true;
    };
    // timeframe is set atomically with results, so results is the only trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [results]);

  return (
    <PatternScanContext.Provider
      value={{ results, timeframe, isScanning, scanningCount, winRates, prescanAt, runScan }}
    >
      {children}
    </PatternScanContext.Provider>
  );
}

export function usePatternScan(): PatternScanContextType {
  const ctx = useContext(PatternScanContext);
  if (!ctx) throw new Error('usePatternScan must be used inside a PatternScanProvider');
  return ctx;
}
