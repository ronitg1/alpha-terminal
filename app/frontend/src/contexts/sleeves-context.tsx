/**
 * SleevesContext — single source of truth for the Sleeves Dashboard.
 *
 * Owns:
 *   • config           — sleeve definitions
 *   • latestScan       — parsed rows from most recent scan
 *   • scanStatus       — 'idle' | 'loading' | 'running' | 'error'
 *   • liveActivity     — ring buffer of SSE progress events during a run
 *   • selectedTicker   — drives the per-ticker detail views
 *   • watchlist(s), portfolio settings, scan history, analyst metadata
 */

import { sleevesApi } from '@/services/sleeves-api';
import {
  AnalystMetadata,
  NamedWatchlist,
  PortfolioSettings,
  ScanListItem,
  ScanSummary,
  SleevesConfig,
  TickerRow,
  WatchlistEntry,
} from '@/types/sleeves';
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
import { API_BASE_URL } from '@/lib/api-base';

type ScanStatus = 'idle' | 'loading' | 'running' | 'error';

// ─── SSE scan-event payloads (discriminated by the SSE `event:` field) ──────

interface ScanProgressEvent {
  agent?: string;
  ticker?: string | null;
  status?: string;
}

interface ScanSleeveCompleteEvent {
  date?: string;
  rows: TickerRow[];
}

interface ScanCompletePayload {
  date?: string;
  csv_path?: string;
  row_count?: number;
  rows?: TickerRow[];
}

/** CompleteEvent serializes its payload under a nested `data` key. */
interface ScanCompleteEvent extends ScanCompletePayload {
  data?: ScanCompletePayload;
}

interface ScanErrorEvent {
  message?: string;
}

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

  // Watchlist (legacy single)
  watchlist: WatchlistEntry[];
  loadWatchlist: () => Promise<void>;
  saveWatchlist: (entries: WatchlistEntry[]) => Promise<void>;

  // Named watchlists
  watchlists: NamedWatchlist[];
  loadWatchlists: () => Promise<void>;
  createNamedWatchlist: (name: string, tickers?: WatchlistEntry[]) => Promise<void>;
  updateNamedWatchlist: (name: string, tickers: WatchlistEntry[]) => Promise<void>;
  renameNamedWatchlist: (oldName: string, newName: string) => Promise<void>;
  deleteNamedWatchlist: (name: string) => Promise<void>;

  // Portfolio settings (per-ticker allocation + agents overlay)
  portfolioSettings: PortfolioSettings;
  loadPortfolioSettings: () => Promise<void>;
  savePortfolioSettings: (settings: PortfolioSettings) => Promise<void>;

  // Sleeve rename (separate from updateSleeve which requires full payload)
  renameSleeve: (oldName: string, newName: string) => Promise<void>;

  // Scan history
  scanHistory: ScanListItem[];
  loadScanHistory: () => Promise<void>;
  loadScanByDate: (date: string) => Promise<void>;

  // Analyst metadata (display_name / description / investing_style) keyed by analyst key.
  analystMeta: Record<string, AnalystMetadata>;
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

export function SleevesProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<SleevesConfig | null>(null);
  const [latestScan, setLatestScan] = useState<ScanSummary | null>(null);
  const [scanStatus, setScanStatus] = useState<ScanStatus>('idle');
  const [scanError, setScanError] = useState<string | null>(null);
  const [liveActivity, setLiveActivity] = useState<ActivityEvent[]>([]);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [watchlist, setWatchlist] = useState<WatchlistEntry[]>([]);
  const [watchlists, setWatchlists] = useState<NamedWatchlist[]>([]);
  const [portfolioSettings, setPortfolioSettings] = useState<PortfolioSettings>({});
  const [scanHistory, setScanHistory] = useState<ScanListItem[]>([]);
  const [analystMeta, setAnalystMeta] = useState<Record<string, AnalystMetadata>>({});

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
      console.warn('SleevesProvider refresh failed:', err);
    }
  }, []);

  const loadWatchlist = useCallback(async () => {
    try {
      const { entries } = await sleevesApi.getWatchlist();
      setWatchlist(entries);
    } catch (err) {
      console.warn('loadWatchlist failed:', err);
    }
  }, []);

  const saveWatchlist = useCallback(async (entries: WatchlistEntry[]) => {
    try {
      const { entries: persisted } = await sleevesApi.putWatchlist(entries);
      setWatchlist(persisted);
      toast.success('Watchlist saved');
    } catch (err) {
      // Surface loudly — silent save failures (e.g. backend down) read as
      // "the app ate my edit", which is worse than an error.
      toast.error(`Could not save the watchlist: ${err instanceof Error ? err.message : err}`);
      throw err;
    }
  }, []);

  const loadWatchlists = useCallback(async () => {
    try {
      const { watchlists: wls } = await sleevesApi.getWatchlists();
      setWatchlists(wls);
    } catch (err) {
      console.warn('loadWatchlists failed:', err);
    }
  }, []);

  const createNamedWatchlist = useCallback(async (name: string, tickers: WatchlistEntry[] = []) => {
    try {
      const wl = await sleevesApi.createWatchlist(name, tickers);
      setWatchlists((prev) => [...prev, wl]);
      toast.success(`Watchlist "${name}" created`);
    } catch (err) {
      toast.error(`Could not create "${name}": ${err instanceof Error ? err.message : err}`);
      throw err;
    }
  }, []);

  const updateNamedWatchlist = useCallback(async (name: string, tickers: WatchlistEntry[]) => {
    try {
      const wl = await sleevesApi.updateWatchlist(name, tickers);
      setWatchlists((prev) => prev.map((w) => w.name === name ? wl : w));
      toast.success(`"${name}" saved (${tickers.length} ticker${tickers.length === 1 ? '' : 's'})`);
    } catch (err) {
      toast.error(`Could not save "${name}": ${err instanceof Error ? err.message : err}`);
      throw err;
    }
  }, []);

  const renameNamedWatchlist = useCallback(async (oldName: string, newName: string) => {
    try {
      await sleevesApi.renameWatchlist(oldName, newName);
      setWatchlists((prev) => prev.map((w) => w.name === oldName ? { ...w, name: newName } : w));
      toast.success(`Renamed to "${newName}"`);
    } catch (err) {
      toast.error(`Rename failed: ${err instanceof Error ? err.message : err}`);
      throw err;
    }
  }, []);

  const deleteNamedWatchlist = useCallback(async (name: string) => {
    try {
      await sleevesApi.deleteWatchlist(name);
      setWatchlists((prev) => prev.filter((w) => w.name !== name));
      toast.success(`Deleted "${name}"`);
    } catch (err) {
      toast.error(`Delete failed: ${err instanceof Error ? err.message : err}`);
      throw err;
    }
  }, []);

  const loadPortfolioSettings = useCallback(async () => {
    try {
      const { settings } = await sleevesApi.getPortfolioSettings();
      setPortfolioSettings(settings);
    } catch (err) {
      console.warn('loadPortfolioSettings failed:', err);
    }
  }, []);

  const savePortfolioSettings = useCallback(async (settings: PortfolioSettings) => {
    const { settings: saved } = await sleevesApi.putPortfolioSettings(settings);
    setPortfolioSettings(saved);
  }, []);

  const renameSleeve = useCallback(async (oldName: string, newName: string) => {
    await sleevesApi.renameSleeve(oldName, newName);
    await refresh();
    // Also migrate portfolio settings key
    setPortfolioSettings((prev) => {
      if (!(oldName in prev)) return prev;
      const next = { ...prev, [newName]: prev[oldName] };
      delete next[oldName];
      return next;
    });
  }, [refresh]);

  const loadScanHistory = useCallback(async () => {
    try {
      const { scans } = await sleevesApi.listScans(30);
      setScanHistory(scans);
    } catch (err) {
      console.warn('loadScanHistory failed:', err);
    }
  }, []);

  const loadScanByDate = useCallback(async (date: string) => {
    try {
      const scan = await sleevesApi.getScanByDate(date);
      setLatestScan(scan);
    } catch (err) {
      // User-initiated (history dropdown) — surface it, don't fail silently.
      toast.error(`Could not load the ${date} scan: ${err instanceof Error ? err.message : err}`);
    }
  }, []);

  const loadAnalystMeta = useCallback(async () => {
    try {
      const { analysts } = await sleevesApi.getAnalysts();
      // Index by key for O(1) lookup in render paths (sleeve card agent badges,
      // drill drawer accordion headers, live activity feed).
      const byKey: Record<string, AnalystMetadata> = {};
      for (const a of analysts) byKey[a.key] = a;
      setAnalystMeta(byKey);
    } catch (err) {
      // Non-fatal — UI degrades to plain badges without tooltips.
      console.warn('loadAnalystMeta failed:', err);
    }
  }, []);

  useEffect(() => {
    void refresh();
    void loadWatchlist();
    void loadWatchlists();
    void loadScanHistory();
    void loadAnalystMeta();
    void loadPortfolioSettings();
  }, [refresh, loadWatchlist, loadWatchlists, loadScanHistory, loadAnalystMeta, loadPortfolioSettings]);

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

      // The `complete`/`error` SSE event marks a finished scan. Tracked in a
      // local rather than reading `scanStatus` after the loop — the state
      // value captured by this closure is the PRE-scan one (stale-closure
      // hazard), so it can never be observed as 'running' here.
      let sawTerminal = false;

      // Inactivity watchdog: a stalled backend or silent network drop would
      // otherwise leave reader.read() pending forever and the UI stuck on
      // "running". Scans emit progress events continuously, so a long gap
      // means the stream is dead.
      const INACTIVITY_MS = 300_000;
      let watchdog: ReturnType<typeof setTimeout> | undefined;
      const armWatchdog = () => {
        clearTimeout(watchdog);
        watchdog = setTimeout(
          () => ctrl.abort(new DOMException('Scan stream stalled — no events for 5 minutes.', 'TimeoutError')),
          INACTIVITY_MS,
        );
      };

      try {
        armWatchdog();
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
          armWatchdog();
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

        if (!sawTerminal) {
          // Stream closed without a complete/error event — the backend died
          // mid-scan. Don't pretend it finished.
          setScanError('Scan stream ended unexpectedly — check that the backend is still running.');
          setScanStatus('error');
        }
      } catch (err) {
        if ((err as Error).name === 'AbortError') {
          // User-initiated stop (or unmount) — quiet, intentional.
          setScanStatus('idle');
          return;
        }
        const msg = err instanceof Error ? err.message : String(err);
        setScanError(msg);
        setScanStatus('error');
        toast.error(`Scan failed: ${msg}`);
      } finally {
        clearTimeout(watchdog);
        if (abortRef.current === ctrl) {
          abortRef.current = null;
        }
      }

      function handleEvent(eventType: string, data: unknown, partialRows: TickerRow[]) {
        switch (eventType) {
          case 'start':
            // No-op — scanStatus already set to 'running'.
            return;
          case 'progress': {
            const ev = data as ScanProgressEvent;
            pushActivity(ev.agent ?? 'system', ev.ticker ?? null, ev.status ?? '');
            return;
          }
          case 'sleeve_complete': {
            const ev = data as ScanSleeveCompleteEvent;
            // Merge in-place; replace any rows already in partialRows for the
            // same ticker (defensive — sleeves shouldn't overlap).
            for (const row of ev.rows ?? []) {
              const idx = partialRows.findIndex((r) => r.ticker === row.ticker);
              if (idx >= 0) partialRows[idx] = row;
              else partialRows.push(row);
            }
            // Merge into the existing scan rather than replacing it — a
            // filtered run (one sleeve) must not blank the rest of the book
            // mid-scan. Rescanned tickers take the fresh row.
            setLatestScan((prev) => {
              const fresh = new Set(partialRows.map((r) => r.ticker));
              const kept = (prev?.rows ?? []).filter((r) => !fresh.has(r.ticker));
              const rows = [...kept, ...partialRows];
              return {
                date: ev.date ?? prev?.date ?? '',
                path: prev?.path ?? '',
                row_count: rows.length,
                rows,
              };
            });
            return;
          }
          case 'complete': {
            const ev = data as ScanCompleteEvent;
            const payload = ev.data ?? ev; // event payload nested under 'data'
            sawTerminal = true;
            setLatestScan({
              date: payload.date ?? '',
              path: payload.csv_path ?? '',
              row_count: payload.row_count ?? 0,
              rows: payload.rows ?? [],
            });
            setScanStatus('idle');
            // Refresh the scan list so a brand-new scan immediately appears
            // in the history dropdown. Fire-and-forget — the user will see
            // the new option after a brief tick.
            void loadScanHistory();
            return;
          }
          case 'error': {
            const ev = data as ScanErrorEvent;
            sawTerminal = true;
            setScanError(ev.message ?? 'Scan error');
            setScanStatus('error');
            return;
          }
          default:
            // Unknown event type; ignore silently.
            return;
        }
      }
    },
    [clearActivity, loadScanHistory, pushActivity]
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
    watchlists,
    loadWatchlists,
    createNamedWatchlist,
    updateNamedWatchlist,
    renameNamedWatchlist,
    deleteNamedWatchlist,
    portfolioSettings,
    loadPortfolioSettings,
    savePortfolioSettings,
    renameSleeve,
    scanHistory,
    loadScanHistory,
    loadScanByDate,
    analystMeta,
  };

  return <SleevesContext.Provider value={value}>{children}</SleevesContext.Provider>;
}

// ─── helpers ─────────────────────────────────────────────────────────────────

/** Parse one SSE frame ("event: foo\ndata: {...}") into { event, data }. */
function parseSseFrame(frame: string): { event: string; data: unknown } | null {
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
