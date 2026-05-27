/**
 * SleevesContext — single source of truth for the Sleeves Dashboard.
 *
 * Owns:
 *   • config           — sleeve definitions
 *   • latestScan       — parsed rows from most recent scan
 *   • scanStatus       — 'idle' | 'loading' | 'running' | 'error'
 *   • liveActivity     — ring buffer of SSE progress events during a run
 *   • selectedTicker   — drives the drill drawer (Phase 3)
 *
 * Phase 3 will add watchlist + saveWatchlist.
 */

import { sleevesApi } from '@/services/sleeves-api';
import { ScanSummary, SleevesConfig, TickerRow, WatchlistEntry } from '@/types/sleeves';
import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';

type ScanStatus = 'idle' | 'loading' | 'running' | 'error';

export interface ActivityEvent {
  /** Monotonic counter so React keys are stable. */
  seq: number;
  agent: string;
  ticker: string | null;
  status: string;
  /** Browser-local timestamp captured at receive time. Server ts is rarely set. */
  ts: number;
}

interface RunScanOptions {
  tickers?: string[];
  sleeves?: string[];
  includeWatchlist?: boolean;
  endDate?: string;
}

interface SleevesContextType {
  config: SleevesConfig | null;
  latestScan: ScanSummary | null;
  scanStatus: ScanStatus;
  scanError: string | null;
  liveActivity: ActivityEvent[];
  selectedTicker: string | null;
  selectTicker: (ticker: string | null) => void;
  refresh: () => Promise<void>;
  runScan: (opts?: RunScanOptions) => Promise<void>;
  stopScan: () => void;
  clearActivity: () => void;

  // Watchlist
  watchlist: WatchlistEntry[];
  loadWatchlist: () => Promise<void>;
  saveWatchlist: (entries: WatchlistEntry[]) => Promise<void>;
}

const SleevesContext = createContext<SleevesContextType | null>(null);

export function useSleevesContext(): SleevesContextType {
  const ctx = useContext(SleevesContext);
  if (!ctx) {
    throw new Error('useSleevesContext must be used within a SleevesProvider');
  }
  return ctx;
}

// Cap the activity buffer so a long-running scan with hundreds of progress
// events doesn't bloat memory or murder the rendering pass.
const MAX_ACTIVITY_EVENTS = 500;
const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export function SleevesProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<SleevesConfig | null>(null);
  const [latestScan, setLatestScan] = useState<ScanSummary | null>(null);
  const [scanStatus, setScanStatus] = useState<ScanStatus>('idle');
  const [scanError, setScanError] = useState<string | null>(null);
  const [liveActivity, setLiveActivity] = useState<ActivityEvent[]>([]);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [watchlist, setWatchlist] = useState<WatchlistEntry[]>([]);

  // Used to abort an in-flight SSE stream when the user clicks Stop or
  // unmounts. Stored in a ref so the abort doesn't trigger re-renders.
  const abortRef = useRef<AbortController | null>(null);
  const seqRef = useRef(0);

  const refresh = useCallback(async () => {
    setScanStatus('loading');
    setScanError(null);
    try {
      const [cfg, scan] = await Promise.all([
        sleevesApi.getConfig(),
        sleevesApi.getLatestScan().catch((err: Error) => {
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
      console.error('SleevesProvider refresh failed:', err);
    }
  }, []);

  const loadWatchlist = useCallback(async () => {
    try {
      const { entries } = await sleevesApi.getWatchlist();
      setWatchlist(entries);
    } catch (err) {
      console.error('loadWatchlist failed:', err);
    }
  }, []);

  const saveWatchlist = useCallback(async (entries: WatchlistEntry[]) => {
    const { entries: persisted } = await sleevesApi.putWatchlist(entries);
    setWatchlist(persisted);
  }, []);

  useEffect(() => {
    void refresh();
    void loadWatchlist();
  }, [refresh, loadWatchlist]);

  const clearActivity = useCallback(() => {
    setLiveActivity([]);
    seqRef.current = 0;
  }, []);

  const pushActivity = useCallback(
    (agent: string, ticker: string | null, status: string) => {
      const seq = ++seqRef.current;
      setLiveActivity((prev) => {
        const next = [...prev, { seq, agent, ticker, status, ts: Date.now() }];
        // Drop from the front when over cap.
        if (next.length > MAX_ACTIVITY_EVENTS) {
          return next.slice(next.length - MAX_ACTIVITY_EVENTS);
        }
        return next;
      });
    },
    []
  );

  const stopScan = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const runScan = useCallback(
    async (opts: RunScanOptions = {}) => {
      // Don't allow overlapping scans.
      if (abortRef.current) {
        abortRef.current.abort();
      }
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      setScanStatus('running');
      setScanError(null);
      clearActivity();

      try {
        const response = await fetch(`${API_BASE_URL}/sleeves/scan/run`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            tickers: opts.tickers ?? null,
            sleeves: opts.sleeves ?? null,
            include_watchlist: opts.includeWatchlist ?? false,
            end_date: opts.endDate ?? null,
          }),
          signal: ctrl.signal,
        });

        if (!response.ok || !response.body) {
          const body = await response.text().catch(() => '');
          throw new Error(`Scan request failed: ${response.status} ${response.statusText} ${body}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buf = '';

        // Track per-sleeve completion so the UI fills in as scans finish.
        const partialRows: TickerRow[] = [];

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });

          // SSE frames are separated by blank lines. Split, keep last partial
          // frame in the buffer.
          const frames = buf.split('\n\n');
          buf = frames.pop() ?? '';
          for (const frame of frames) {
            const parsed = parseSseFrame(frame);
            if (!parsed) continue;
            handleEvent(parsed.event, parsed.data, partialRows);
          }
        }

        // No more bytes — scanStatus should already be 'idle' from a
        // complete event. If we never received one, mark error.
        if (scanStatus === 'running') {
          setScanStatus('idle');
        }
      } catch (err) {
        if ((err as Error).name === 'AbortError') {
          setScanStatus('idle');
          return;
        }
        const msg = err instanceof Error ? err.message : String(err);
        setScanError(msg);
        setScanStatus('error');
        console.error('runScan failed:', err);
      } finally {
        if (abortRef.current === ctrl) {
          abortRef.current = null;
        }
      }

      function handleEvent(eventType: string, data: any, partialRows: TickerRow[]) {
        switch (eventType) {
          case 'start':
            // No-op — scanStatus already set to 'running'.
            return;
          case 'progress':
            pushActivity(data.agent ?? 'system', data.ticker ?? null, data.status ?? '');
            return;
          case 'sleeve_complete':
            // Merge in-place; replace any rows already in partialRows for the
            // same ticker (defensive — sleeves shouldn't overlap).
            for (const row of data.rows as TickerRow[]) {
              const idx = partialRows.findIndex((r) => r.ticker === row.ticker);
              if (idx >= 0) partialRows[idx] = row;
              else partialRows.push(row);
            }
            setLatestScan((prev) => ({
              date: data.date ?? prev?.date ?? '',
              path: prev?.path ?? '',
              row_count: partialRows.length,
              rows: [...partialRows],
            }));
            return;
          case 'complete': {
            const payload = data?.data ?? data; // event_payload nested under 'data'
            setLatestScan({
              date: payload.date ?? '',
              path: payload.csv_path ?? '',
              row_count: payload.row_count ?? 0,
              rows: payload.rows ?? [],
            });
            setScanStatus('idle');
            return;
          }
          case 'error':
            setScanError(data.message ?? 'Scan error');
            setScanStatus('error');
            return;
          default:
            // Unknown event type; ignore silently.
            return;
        }
      }
    },
    [clearActivity, pushActivity, scanStatus]
  );

  // Abort any in-flight scan when the provider unmounts.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const value: SleevesContextType = {
    config,
    latestScan,
    scanStatus,
    scanError,
    liveActivity,
    selectedTicker,
    selectTicker: setSelectedTicker,
    refresh,
    runScan,
    stopScan,
    clearActivity,
    watchlist,
    loadWatchlist,
    saveWatchlist,
  };

  return <SleevesContext.Provider value={value}>{children}</SleevesContext.Provider>;
}

// ─── helpers ─────────────────────────────────────────────────────────────────

/** Parse one SSE frame ("event: foo\ndata: {...}") into { event, data }. */
function parseSseFrame(frame: string): { event: string; data: any } | null {
  let event = 'message';
  const dataLines: string[] = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim());
    }
    // ignore id:, retry:, comments
  }
  if (dataLines.length === 0) return null;
  const dataStr = dataLines.join('\n');
  try {
    return { event, data: JSON.parse(dataStr) };
  } catch {
    return { event, data: dataStr };
  }
}
