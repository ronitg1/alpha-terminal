/**
 * MarketView — main content area for the Market section.
 *
 * No ticker selected → management panel (watchlists + sleeves).
 * Ticker selected → full detail: OHLCV chart, company overview, fundamentals, news.
 */

import { useDashboard } from '@/contexts/dashboard-context';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { useTickerData } from '@/components/sleeves/hooks/use-ticker-data';
import { CompanyOverviewCard } from '@/components/sleeves/company-overview-card';
import { FinnhubFinancials } from '@/components/dashboard/finnhub-financials';
import { RecentNewsList } from '@/components/sleeves/recent-news-list';
import { LineVolumeChart } from '@/components/sleeves/line-volume-chart';
import { MiniSpark } from '@/components/sleeves/mini-spark';
import { MarketCards } from '@/components/dashboard/portfolio/market-cards';
import { CatalystCalendar } from '@/components/dashboard/market/catalyst-calendar';
import { sleevesApi } from '@/services/sleeves-api';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, ChevronRight, LineChart, Pencil, Plus, Settings2, Trash2, TrendingDown, TrendingUp, X } from 'lucide-react';
import { Quote, WatchlistEntry } from '@/types/sleeves';

type Timeframe = '1W' | '1M' | '3M' | '6M' | 'YTD' | '1Y' | '2Y';

const TF_DAYS: Record<Timeframe, number> = {
  '1W': 7, '1M': 30, '3M': 90, '6M': 180, 'YTD': 0, '1Y': 365, '2Y': 730,
};

function slicePrices(
  bars: import('@/types/sleeves').PriceBar[],
  tf: Timeframe,
): import('@/types/sleeves').PriceBar[] {
  if (!bars.length) return bars;
  if (tf === 'YTD') {
    const year = new Date().getFullYear();
    return bars.filter((b) => b.time.startsWith(String(year)));
  }
  const days = TF_DAYS[tf];
  return days > 0 ? bars.slice(-days) : bars;
}

function pctChange(bars: import('@/types/sleeves').PriceBar[]): number | null {
  if (bars.length < 2) return null;
  const first = bars[0].close;
  const last = bars[bars.length - 1].close;
  return ((last - first) / first) * 100;
}

// ─── Ticker detail ───────────────────────────────────────────────────────────

function TickerDetail({ ticker }: { ticker: string }) {
  const { data, loading, error } = useTickerData(ticker);
  const [tf, setTf] = useState<Timeframe>('3M');

  const bars = slicePrices(data?.price_history ?? [], tf);
  const pct = pctChange(bars);
  const last = bars.length ? bars[bars.length - 1].close : null;

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-6 space-y-6">
        {/* ── Header ── */}
        <div className="flex items-start gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl font-bold font-mono">{ticker}</h1>
            {data?.details?.name && (
              <p className="text-sm text-muted-foreground mt-0.5">{data.details.name}</p>
            )}
          </div>
          <div className="flex-1" />
          {last != null && (
            <div className="text-right">
              <div className="text-2xl font-mono font-semibold">${last.toFixed(2)}</div>
              {pct != null && (
                <div className={cn('text-sm font-mono', pct >= 0 ? 'text-emerald-500' : 'text-rose-500')}>
                  {pct >= 0 ? '+' : ''}{pct.toFixed(2)}% ({tf})
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Chart + timeframe selector ── */}
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex gap-1 mb-3">
            {(['1W', '1M', '3M', '6M', 'YTD', '1Y', '2Y'] as Timeframe[]).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTf(t)}
                className={cn(
                  'px-2.5 py-1 text-xs rounded font-medium transition-colors',
                  tf === t
                    ? 'bg-foreground text-background'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted/60',
                )}
              >
                {t}
              </button>
            ))}
          </div>
          {loading && !data && (
            <div className="h-40 flex items-center justify-center text-xs text-muted-foreground">
              Loading chart…
            </div>
          )}
          {error && (
            <div className="h-40 flex items-center justify-center text-xs text-rose-500">
              {error}
            </div>
          )}
          {bars.length >= 2 && (
            <LineVolumeChart bars={bars} priceHeight={160} volHeight={50} />
          )}
          {!loading && !error && bars.length < 2 && (
            <div className="h-40 flex items-center justify-center text-xs text-muted-foreground italic">
              Not enough price history for {tf}.
            </div>
          )}
        </div>

        {/* ── Company overview + key financials ── */}
        <CompanyOverviewCard data={data} loading={loading} ticker={ticker} />

        {/* ── Finnhub enrichment (growth/turnover, beat-miss, consensus, peers) ── */}
        <FinnhubFinancials ticker={ticker} />

        {/* ── Recent News ── */}
        <div>
          <h2 className="text-sm font-semibold mb-3">Recent News</h2>
          <RecentNewsList items={data?.recent_news ?? []} loading={loading} />
        </div>
      </div>
    </div>
  );
}

// ─── Inline ticker editor (reused for watchlist and sleeve) ─────────────────

const TICKER_RE = /^[A-Z0-9]{1,10}([.\-][A-Z0-9]{1,6})?$/;

function TickerEditor({
  entries,
  onSave,
  onCancel,
  saving,
}: {
  entries: WatchlistEntry[];
  onSave: (next: WatchlistEntry[]) => void;
  onCancel: () => void;
  saving?: boolean;
}) {
  const [draft, setDraft] = useState<WatchlistEntry[]>(entries);
  const [addInput, setAddInput] = useState('');
  const [addError, setAddError] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const remove = (ticker: string) => setDraft((prev) => prev.filter((e) => e.ticker !== ticker));

  const add = () => {
    const parts = addInput.trim().toUpperCase().split(/[\s,]+/).filter(Boolean);
    const errors: string[] = [];
    const toAdd: WatchlistEntry[] = [];
    for (const t of parts) {
      if (!TICKER_RE.test(t)) { errors.push(t); continue; }
      if (draft.some((e) => e.ticker === t)) continue;
      toAdd.push({ ticker: t, comment: '' });
    }
    if (errors.length) { setAddError(`Invalid: ${errors.join(', ')}`); return; }
    if (toAdd.length) setDraft((prev) => [...prev, ...toAdd]);
    setAddInput('');
    setAddError('');
  };

  /** Draft plus anything still typed in the add box — clicking Save without
   *  pressing Enter/Add first must not silently drop the typed tickers. */
  const draftWithPending = (): WatchlistEntry[] | null => {
    const parts = addInput.trim().toUpperCase().split(/[\s,]+/).filter(Boolean);
    if (parts.length === 0) return draft;
    const bad = parts.filter((t) => !TICKER_RE.test(t));
    if (bad.length) { setAddError(`Invalid: ${bad.join(', ')}`); return null; }
    const extra = parts
      .filter((t) => !draft.some((e) => e.ticker === t))
      .map((t) => ({ ticker: t, comment: '' }));
    const next = [...draft, ...extra];
    setDraft(next);
    setAddInput('');
    return next;
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {draft.map((e) => (
          <span
            key={e.ticker}
            className="inline-flex items-center gap-1 bg-muted/60 border border-border rounded-full px-2 py-0.5 text-xs font-mono"
          >
            {e.ticker}
            <button type="button" onClick={() => remove(e.ticker)} className="text-muted-foreground hover:text-rose-500">
              <X className="h-2.5 w-2.5" />
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          ref={inputRef}
          value={addInput}
          onChange={(e) => { setAddInput(e.target.value); setAddError(''); }}
          onKeyDown={(e) => { if (e.key === 'Enter') add(); }}
          placeholder="AAPL, MSFT… (comma or space separated)"
          className="flex-1 bg-background border border-border rounded px-3 py-1.5 text-xs font-mono uppercase outline-none focus:border-primary"
        />
        <button type="button" onClick={add} className="px-3 py-1.5 text-xs bg-muted rounded hover:bg-muted/80 transition-colors">
          Add
        </button>
      </div>
      {addError && <p className="text-xs text-rose-500">{addError}</p>}
      <div className="flex gap-2 pt-1">
        <button
          type="button"
          onClick={() => { const next = draftWithPending(); if (next) onSave(next); }}
          disabled={saving}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded hover:bg-primary/80 disabled:opacity-50 transition-colors"
        >
          <Check className="h-3 w-3" /> {saving ? 'Saving…' : 'Save'}
        </button>
        <button type="button" onClick={onCancel} className="px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
          Cancel
        </button>
      </div>
    </div>
  );
}

// ─── Watchlists management panel ─────────────────────────────────────────────

function WatchlistsPanel() {
  const {
    watchlists,
    createNamedWatchlist,
    updateNamedWatchlist,
    renameNamedWatchlist,
    deleteNamedWatchlist,
  } = useSleevesContext();

  const [creatingName, setCreatingName] = useState('');
  const [creating, setCreating] = useState(false);
  const [expandedWl, setExpandedWl] = useState<Set<string>>(new Set());
  const [editingTickers, setEditingTickers] = useState<string | null>(null);
  const [renamingWl, setRenamingWl] = useState<string | null>(null);
  const [renameVal, setRenameVal] = useState('');
  const [saving, setSaving] = useState(false);

  const toggleExpand = (name: string) =>
    setExpandedWl((prev) => { const next = new Set(prev); next.has(name) ? next.delete(name) : next.add(name); return next; });

  const handleCreate = async () => {
    const name = creatingName.trim();
    if (!name) return;
    setSaving(true);
    try { await createNamedWatchlist(name); setCreatingName(''); setCreating(false); }
    catch { /* toast shown by context; keep the form open */ }
    finally { setSaving(false); }
  };

  const handleRename = async (oldName: string) => {
    const newName = renameVal.trim();
    if (!newName || newName === oldName) { setRenamingWl(null); return; }
    setSaving(true);
    try { await renameNamedWatchlist(oldName, newName); setRenamingWl(null); }
    catch { /* toast shown by context */ }
    finally { setSaving(false); }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Watchlists</h3>
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="flex items-center gap-1 text-xs text-primary hover:text-primary/80 transition-colors"
        >
          <Plus className="h-3.5 w-3.5" /> New watchlist
        </button>
      </div>

      {creating && (
        <div className="flex items-center gap-2 p-3 bg-muted/30 rounded-lg border border-border">
          <input
            autoFocus
            value={creatingName}
            onChange={(e) => setCreatingName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void handleCreate(); if (e.key === 'Escape') { setCreating(false); setCreatingName(''); } }}
            placeholder="Watchlist name…"
            className="flex-1 bg-background border border-border rounded px-3 py-1.5 text-sm outline-none focus:border-primary"
          />
          <button type="button" onClick={() => void handleCreate()} disabled={saving || !creatingName.trim()}
            className="flex items-center gap-1 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded hover:bg-primary/80 disabled:opacity-50 transition-colors">
            <Check className="h-3 w-3" /> Create
          </button>
          <button type="button" onClick={() => { setCreating(false); setCreatingName(''); }}
            className="text-muted-foreground hover:text-foreground transition-colors">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {watchlists.length === 0 && !creating && (
        <p className="text-sm text-muted-foreground italic py-2">No watchlists yet. Create one above.</p>
      )}

      {watchlists.map((wl) => {
        const isExpanded = expandedWl.has(wl.name);
        const isEditingTickers = editingTickers === wl.name;
        const isRenaming = renamingWl === wl.name;

        return (
          <div key={wl.name} className="border border-border rounded-lg overflow-hidden">
            {/* Header row */}
            <div className="flex items-center gap-2 px-3 py-2.5 bg-muted/20">
              <button type="button" onClick={() => toggleExpand(wl.name)} className="text-muted-foreground hover:text-foreground">
                {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
              </button>
              {isRenaming ? (
                <input
                  autoFocus
                  value={renameVal}
                  onChange={(e) => setRenameVal(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') void handleRename(wl.name); if (e.key === 'Escape') setRenamingWl(null); }}
                  onBlur={() => void handleRename(wl.name)}
                  className="flex-1 bg-background border border-border rounded px-2 py-0.5 text-sm outline-none focus:border-primary"
                />
              ) : (
                <span className="flex-1 font-medium text-sm">{wl.name}</span>
              )}
              <span className="text-xs text-muted-foreground">{wl.tickers.length} tickers</span>
              <button type="button" onClick={() => { setEditingTickers(isEditingTickers ? null : wl.name); setExpandedWl((p) => new Set([...p, wl.name])); }}
                className="text-muted-foreground hover:text-foreground transition-colors" title="Edit tickers">
                <Pencil className="h-3.5 w-3.5" />
              </button>
              {!isRenaming && (
                <button type="button" onClick={() => { setRenamingWl(wl.name); setRenameVal(wl.name); }}
                  className="text-[10px] text-muted-foreground hover:text-foreground transition-colors px-1" title="Rename watchlist">
                  Rename
                </button>
              )}
              <button type="button"
                onClick={() => {
                  toast(`Delete "${wl.name}"?`, {
                    action: { label: 'Delete', onClick: () => void deleteNamedWatchlist(wl.name) },
                    cancel: { label: 'Cancel', onClick: () => {} },
                  });
                }}
                className="text-muted-foreground hover:text-rose-500 transition-colors" title="Delete watchlist">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>

            {/* Rename shortcut: double-click name */}
            {isExpanded && (
              <div className="px-4 py-3 border-t border-border/50">
                {isEditingTickers ? (
                  <TickerEditor
                    entries={wl.tickers}
                    saving={saving}
                    onSave={async (next) => {
                      setSaving(true);
                      try { await updateNamedWatchlist(wl.name, next); setEditingTickers(null); }
                      catch { /* toast shown by context; keep editor open */ }
                      finally { setSaving(false); }
                    }}
                    onCancel={() => setEditingTickers(null)}
                  />
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {wl.tickers.length === 0 && (
                      <span className="text-xs text-muted-foreground italic">No tickers — click the pencil to add.</span>
                    )}
                    {wl.tickers.map((e) => (
                      <span key={e.ticker} className="inline-flex items-center bg-muted/60 border border-border rounded-full px-2 py-0.5 text-xs font-mono">
                        {e.ticker}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── Sleeves management panel ─────────────────────────────────────────────────

function AgentsDropdown({
  selected,
  allAgents,
  onChange,
}: {
  selected: string[];
  allAgents: import('@/types/sleeves').AnalystMetadata[];
  onChange: (keys: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  const toggle = (key: string) =>
    onChange(selected.includes(key) ? selected.filter((k) => k !== key) : [...selected, key]);

  const label = selected.length === 0
    ? 'No agents'
    : selected.length === allAgents.length
    ? 'All agents'
    : `${selected.length} agents`;

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 px-2 py-1 text-[11px] bg-muted/60 border border-border rounded hover:bg-muted transition-colors"
      >
        <span>{label}</span>
        <ChevronDown className="h-3 w-3 text-muted-foreground" />
      </button>
      {open && (
        <div className="absolute z-30 top-full mt-1 left-0 w-52 bg-background border border-border rounded-lg shadow-xl py-1 max-h-52 overflow-y-auto">
          {allAgents.map((a) => {
            const checked = selected.includes(a.key);
            return (
              <button
                key={a.key}
                type="button"
                onClick={() => toggle(a.key)}
                className={cn(
                  'w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 hover:bg-muted/60 transition-colors',
                  checked ? 'text-foreground' : 'text-muted-foreground',
                )}
              >
                <span className={cn('w-3.5 h-3.5 rounded border flex items-center justify-center text-[9px] flex-shrink-0', checked ? 'bg-primary border-primary text-primary-foreground' : 'border-muted-foreground')}>
                  {checked && <Check className="h-2.5 w-2.5" />}
                </span>
                {a.display_name}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function SleevesPanel() {
  const {
    config,
    analystMeta,
    portfolioSettings,
    savePortfolioSettings,
    renameSleeve,
    refresh,
  } = useSleevesContext();

  const [expandedSl, setExpandedSl] = useState<Set<string>>(new Set());
  const [renamingSl, setRenamingSl] = useState<string | null>(null);
  const [renameVal, setRenameVal] = useState('');
  const [editingTickers, setEditingTickers] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [creatingName, setCreatingName] = useState('');
  const [settingsDraft, setSettingsDraft] = useState<import('@/types/sleeves').PortfolioSettings>({});
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => { setSettingsDraft(portfolioSettings); }, [portfolioSettings]);

  const allAgents = Object.values(analystMeta).sort((a, b) => a.order - b.order);

  const toggle = (name: string) =>
    setExpandedSl((prev) => { const next = new Set(prev); next.has(name) ? next.delete(name) : next.add(name); return next; });

  const handleCreate = async () => {
    const name = creatingName.trim().replace(/\s+/g, '_').toLowerCase();
    if (!name) return;
    setSaving('new');
    try {
      const defaultAgent = allAgents[0]?.key ?? 'alpha_seeker';
      await import('@/services/sleeves-api').then(({ sleevesApi }) =>
        sleevesApi.createSleeve(name, {
          allocation_pct: 0,
          agents: [defaultAgent],
          agent_weights: { [defaultAgent]: 1.0 },
          tickers: [],
        })
      );
      await refresh();
      setCreatingName('');
      setCreating(false);
    } catch (err) {
      toast.error(`Failed to create portfolio: ${(err as Error).message}`);
    } finally { setSaving(null); }
  };

  const doDeleteSleeve = async (name: string) => {
    setSaving(name);
    try {
      await import('@/services/sleeves-api').then(({ sleevesApi }) => sleevesApi.deleteSleeve(name));
      await refresh();
    } catch (err) {
      toast.error(`Failed to delete: ${(err as Error).message}`);
    } finally { setSaving(null); }
  };

  const handleDelete = (name: string) => {
    toast(`Delete portfolio "${name}"? This cannot be undone.`, {
      action: { label: 'Delete', onClick: () => void doDeleteSleeve(name) },
      cancel: { label: 'Cancel', onClick: () => {} },
    });
  };

  const handleRename = async (oldName: string) => {
    const newName = renameVal.trim().replace(/\s+/g, '_').toLowerCase();
    if (!newName || newName === oldName) { setRenamingSl(null); return; }
    setSaving(oldName);
    try {
      await renameSleeve(oldName, newName);
      setRenamingSl(null);
    } catch (err) {
      toast.error(`Rename failed: ${(err as Error).message}`);
    } finally { setSaving(null); }
  };

  const handleSaveTickerList = async (sleeveName: string, entries: WatchlistEntry[], sleeve: import('@/types/sleeves').SleeveConfig) => {
    setSaving(sleeveName);
    try {
      await import('@/services/sleeves-api').then(({ sleevesApi }) =>
        sleevesApi.updateSleeve(sleeveName, {
          allocation_pct: sleeve.allocation_pct,
          agents: sleeve.agents,
          agent_weights: sleeve.agent_weights,
          tickers: entries.map((e) => e.ticker),
        })
      );
      await refresh();
      setEditingTickers(null);
    } finally { setSaving(null); }
  };

  const handleSaveSettings = async (sleeveName: string) => {
    setSaving(sleeveName + '_settings');
    const next = { ...portfolioSettings, [sleeveName]: settingsDraft[sleeveName] ?? {} };
    try { await savePortfolioSettings(next); }
    finally { setSaving(null); }
  };

  const setTickerSetting = (sleeve: string, ticker: string, field: 'allocation_pct' | 'agents', value: number | string[] | null) => {
    setSettingsDraft((prev) => {
      const existing = prev[sleeve]?.[ticker] ?? { allocation_pct: 0, agents: null };
      return {
        ...prev,
        [sleeve]: {
          ...(prev[sleeve] ?? {}),
          [ticker]: { ...existing, [field]: value },
        },
      };
    });
  };

  if (!config) return <p className="text-sm text-muted-foreground italic">Loading portfolios…</p>;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold">Portfolios</h3>
          <p className="text-xs text-muted-foreground mt-0.5">Each portfolio holds a set of positions. Set per-ticker allocation and which analysts run on each position.</p>
        </div>
        <button type="button" onClick={() => setCreating(true)}
          className="flex items-center gap-1 text-xs text-primary hover:text-primary/80 transition-colors">
          <Plus className="h-3.5 w-3.5" /> New portfolio
        </button>
      </div>

      {creating && (
        <div className="flex items-center gap-2 p-3 bg-muted/30 rounded-lg border border-border">
          <input
            autoFocus
            value={creatingName}
            onChange={(e) => setCreatingName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void handleCreate(); if (e.key === 'Escape') { setCreating(false); setCreatingName(''); } }}
            placeholder="portfolio_name (lowercase, underscores)"
            className="flex-1 bg-background border border-border rounded px-3 py-1.5 text-sm outline-none focus:border-primary font-mono"
          />
          <button type="button" onClick={() => void handleCreate()} disabled={saving === 'new' || !creatingName.trim()}
            className="flex items-center gap-1 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded hover:bg-primary/80 disabled:opacity-50 transition-colors">
            <Check className="h-3 w-3" /> Create
          </button>
          <button type="button" onClick={() => { setCreating(false); setCreatingName(''); }}
            className="text-muted-foreground hover:text-foreground transition-colors">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {config.sleeves.length === 0 && !creating && (
        <p className="text-sm text-muted-foreground italic py-2">No portfolios configured. Create one above.</p>
      )}

      {config.sleeves.map((sleeve) => {
        const isExpanded = expandedSl.has(sleeve.name);
        const isRenaming = renamingSl === sleeve.name;
        const isEditingTickers = editingTickers === sleeve.name;
        const isSaving = saving === sleeve.name || saving === sleeve.name + '_settings';
        const sleeveSettings = settingsDraft[sleeve.name] ?? {};

        return (
          <div key={sleeve.name} className="border border-border rounded-lg overflow-hidden">
            <div className="flex items-center gap-2 px-3 py-2.5 bg-muted/20">
              <button type="button" onClick={() => toggle(sleeve.name)} className="text-muted-foreground hover:text-foreground">
                {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
              </button>
              {isRenaming ? (
                <input
                  autoFocus
                  value={renameVal}
                  onChange={(e) => setRenameVal(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') void handleRename(sleeve.name); if (e.key === 'Escape') setRenamingSl(null); }}
                  onBlur={() => void handleRename(sleeve.name)}
                  className="flex-1 bg-background border border-border rounded px-2 py-0.5 text-sm outline-none focus:border-primary font-mono"
                />
              ) : (
                <span className="flex-1 font-medium text-sm">{sleeve.name.replace(/_/g, ' ')}</span>
              )}
              <span className="text-xs text-muted-foreground">{sleeve.tickers.length} tickers</span>
              {!isRenaming && (
                <button type="button" onClick={() => { setRenamingSl(sleeve.name); setRenameVal(sleeve.name); }}
                  className="text-[10px] text-muted-foreground hover:text-foreground px-1 transition-colors">
                  Rename
                </button>
              )}
              <button type="button" onClick={() => void handleDelete(sleeve.name)} disabled={!!saving}
                className="text-muted-foreground hover:text-rose-500 transition-colors" title="Delete portfolio">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>

            {isExpanded && (
              <div className="border-t border-border/50">
                {/* Tickers section */}
                <div className="px-4 pt-3 pb-2">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Tickers</span>
                    <button type="button"
                      onClick={() => setEditingTickers(isEditingTickers ? null : sleeve.name)}
                      className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors">
                      <Pencil className="h-3 w-3" /> Edit
                    </button>
                  </div>
                  {isEditingTickers ? (
                    <TickerEditor
                      entries={sleeve.tickers.map((t) => ({ ticker: t, comment: '' }))}
                      saving={isSaving}
                      onSave={(next) => void handleSaveTickerList(sleeve.name, next, sleeve)}
                      onCancel={() => setEditingTickers(null)}
                    />
                  ) : (
                    <div className="flex flex-wrap gap-1.5">
                      {sleeve.tickers.length === 0 && <span className="text-xs text-muted-foreground italic">No tickers.</span>}
                      {sleeve.tickers.map((t) => (
                        <span key={t} className="inline-flex items-center bg-muted/60 border border-border rounded-full px-2 py-0.5 text-xs font-mono">{t}</span>
                      ))}
                    </div>
                  )}
                </div>

                {/* Per-ticker portfolio settings */}
                {!isEditingTickers && sleeve.tickers.length > 0 && (
                  <div className="px-4 pb-3 border-t border-border/30 pt-3 space-y-2">
                    <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Portfolio Settings</span>
                    {sleeve.tickers.map((t) => {
                      const ts = sleeveSettings[t] ?? { allocation_pct: 0, agents: null };
                      const selectedAgents = ts.agents ?? sleeve.agents;
                      return (
                        <div key={t} className="flex items-center gap-3 py-0.5">
                          <span className="font-mono font-semibold text-xs w-16 flex-shrink-0">{t}</span>
                          <div className="flex items-center gap-1">
                            <input
                              type="number" min="0" max="100" step="0.1"
                              value={ts.allocation_pct ?? 0}
                              onChange={(e) => setTickerSetting(sleeve.name, t, 'allocation_pct', parseFloat(e.target.value) || 0)}
                              className="w-16 bg-background border border-border rounded px-2 py-0.5 text-xs font-mono outline-none focus:border-primary text-right"
                            />
                            <span className="text-xs text-muted-foreground">%</span>
                          </div>
                          <AgentsDropdown
                            selected={selectedAgents}
                            allAgents={allAgents}
                            onChange={(keys) => setTickerSetting(sleeve.name, t, 'agents', keys)}
                          />
                        </div>
                      );
                    })}
                    <div className="pt-1">
                      <button type="button"
                        onClick={() => void handleSaveSettings(sleeve.name)}
                        disabled={saving === sleeve.name + '_settings'}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded hover:bg-primary/80 disabled:opacity-50 transition-colors">
                        <Check className="h-3 w-3" />
                        {saving === sleeve.name + '_settings' ? 'Saving…' : 'Save Settings'}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── Watchlist dashboard (shown when no ticker is selected) ───────────────────

type PerfPeriod = 'today' | 'week' | 'month';

/** Percent change over the chosen window, derived from the quote (today's %) or
 *  its 30-close sparkline (≈5 trading days = week, ≈21 = month). */
function perfForPeriod(q: Quote | undefined, period: PerfPeriod): number | null {
  if (!q) return null;
  if (period === 'today') return q.pct_change ?? null;
  const s = q.spark ?? [];
  const back = period === 'week' ? 5 : 21;
  if (s.length <= back) return null;
  const prev = s[s.length - 1 - back];
  const last = s[s.length - 1];
  if (!prev) return null;
  return ((last - prev) / prev) * 100;
}

function PerfRow({ ticker, quote, perf, onClick }: { ticker: string; quote?: Quote; perf: number | null; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left hover:bg-muted/50">
      <div className="min-w-0 flex-1">
        <div className="font-mono text-xs font-semibold">{ticker}</div>
        {quote?.name && <div className="truncate text-[10px] text-muted-foreground">{quote.name}</div>}
      </div>
      {quote?.spark && quote.spark.length >= 2 && (
        <MiniSpark closes={quote.spark} width={56} height={22} className="flex-shrink-0 opacity-80" />
      )}
      <div className="w-20 flex-shrink-0 text-right">
        <div className="font-mono text-xs">{quote?.last != null ? `$${quote.last.toFixed(2)}` : '—'}</div>
        <div className={cn('font-mono text-[10px]', perf == null ? 'text-muted-foreground' : perf >= 0 ? 'text-emerald-500' : 'text-rose-500')}>
          {perf == null ? '—' : `${perf >= 0 ? '+' : ''}${perf.toFixed(2)}%`}
        </div>
      </div>
    </button>
  );
}

function MarketDashboard() {
  const { watchlists } = useSleevesContext();
  const { setSelectedTicker } = useDashboard();
  const [selected, setSelected] = useState<string>('');
  const [period, setPeriod] = useState<PerfPeriod>('today');
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [manageOpen, setManageOpen] = useState(false);
  const [manageTab, setManageTab] = useState<'watchlists' | 'sleeves'>('watchlists');

  // Default to a market-cap-leaders watchlist when present, else the first one.
  useEffect(() => {
    if (selected || watchlists.length === 0) return;
    const leaders = watchlists.find((w) => /market ?cap|leaders|mega/i.test(w.name));
    setSelected(leaders?.name ?? watchlists[0].name);
  }, [watchlists, selected]);

  const current = watchlists.find((w) => w.name === selected);
  const tickers = useMemo(() => current?.tickers.map((t) => t.ticker) ?? [], [current]);

  useEffect(() => {
    if (tickers.length === 0) { setQuotes({}); return; }
    let alive = true;
    void sleevesApi.getQuotes(tickers).then((d) => { if (alive) setQuotes(d.quotes); }).catch(() => {});
    return () => { alive = false; };
  }, [tickers.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  const ranked = useMemo(
    () =>
      tickers
        .map((t) => ({ t, q: quotes[t], perf: perfForPeriod(quotes[t], period) }))
        .filter((x) => x.perf != null)
        .sort((a, b) => (b.perf as number) - (a.perf as number)),
    [tickers, quotes, period],
  );
  const top = ranked.slice(0, 8);
  const bottom = ranked.slice(-8).reverse();

  return (
    <div className="app-vh overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-5 px-4 py-4 sm:px-6 sm:py-6">
        {/* Header + watchlist selector */}
        <div className="flex flex-wrap items-center gap-2">
          <LineChart className="h-5 w-5 text-primary" />
          <h1 className="text-base font-semibold sm:text-lg">Market Monitor</h1>
          <div className="ml-auto flex items-center gap-2">
            {watchlists.length > 0 && (
              <select
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
                className="rounded border border-border bg-background px-2 py-1 text-xs outline-none focus:border-primary"
              >
                {watchlists.map((w) => (
                  <option key={w.name} value={w.name}>{w.name} ({w.tickers.length})</option>
                ))}
              </select>
            )}
          </div>
        </div>

        {/* Macro panel + market movers */}
        <MarketCards />

        {/* Watchlist performers */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              {current?.name ?? 'Watchlist'} movers
            </span>
            <div className="ml-auto flex rounded-md bg-muted p-0.5 text-[11px]">
              {(['today', 'week', 'month'] as const).map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPeriod(p)}
                  className={cn('rounded px-2 py-0.5 font-medium capitalize', period === p ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground')}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>
          {tickers.length === 0 ? (
            <p className="py-2 text-xs italic text-muted-foreground">This watchlist has no tickers.</p>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="rounded-lg border border-border/60 bg-card p-3">
                <div className="mb-1 flex items-center gap-1 text-[10px] font-medium uppercase text-emerald-500">
                  <TrendingUp className="h-3 w-3" /> Top performers
                </div>
                {top.length === 0 ? <p className="px-2 py-1 text-xs text-muted-foreground">Loading…</p> : top.map((x) => (
                  <PerfRow key={x.t} ticker={x.t} quote={x.q} perf={x.perf} onClick={() => setSelectedTicker(x.t)} />
                ))}
              </div>
              <div className="rounded-lg border border-border/60 bg-card p-3">
                <div className="mb-1 flex items-center gap-1 text-[10px] font-medium uppercase text-rose-500">
                  <TrendingDown className="h-3 w-3" /> Laggards
                </div>
                {bottom.length === 0 ? <p className="px-2 py-1 text-xs text-muted-foreground">Loading…</p> : bottom.map((x) => (
                  <PerfRow key={x.t} ticker={x.t} quote={x.q} perf={x.perf} onClick={() => setSelectedTicker(x.t)} />
                ))}
              </div>
            </div>
          )}
          <p className="text-[10px] text-muted-foreground">Tap any ticker for full research — chart, fundamentals, and news.</p>
        </div>

        {/* Catalyst calendar — earnings + macro/policy events for these names */}
        <CatalystCalendar tickers={tickers} onTicker={setSelectedTicker} />

        {/* Manage watchlists & portfolios (collapsible — the old landing content) */}
        <div className="rounded-lg border border-border/60">
          <button
            type="button"
            onClick={() => setManageOpen((o) => !o)}
            className="flex w-full items-center gap-2 px-3 py-2.5 text-sm font-medium"
          >
            <Settings2 className="h-4 w-4 text-muted-foreground" />
            Manage watchlists &amp; portfolios
            {manageOpen ? <ChevronDown className="ml-auto h-4 w-4" /> : <ChevronRight className="ml-auto h-4 w-4" />}
          </button>
          {manageOpen && (
            <div className="space-y-4 border-t border-border/60 p-4">
              <div className="flex gap-1 border-b border-border">
                {(['watchlists', 'sleeves'] as const).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setManageTab(t)}
                    className={cn(
                      '-mb-px border-b-2 px-4 py-2 text-sm font-medium capitalize transition-colors',
                      manageTab === t ? 'border-foreground text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {t === 'sleeves' ? 'Portfolios' : 'Watchlists'}
                  </button>
                ))}
              </div>
              {manageTab === 'watchlists' && <WatchlistsPanel />}
              {manageTab === 'sleeves' && <SleevesPanel />}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Export ──────────────────────────────────────────────────────────────────

export function MarketView() {
  const { selectedTicker } = useDashboard();
  return selectedTicker ? <TickerDetail ticker={selectedTicker} /> : <MarketDashboard />;
}
