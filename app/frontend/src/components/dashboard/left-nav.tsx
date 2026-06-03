/**
 * LeftNav — Google Finance-style persistent left sidebar.
 *
 * Sections (all collapsible):
 *   • Watchlist  — from /sleeves/watchlist
 *   • My Sleeves — from /sleeves/config; click sleeve → expand tickers
 *   • Equity Sectors — hardcoded sector ETFs
 *
 * Clicking any ticker calls setSelectedTicker (navigates to Market section).
 */

import { MiniSpark } from '@/components/sleeves/mini-spark';
import { useDashboard } from '@/contexts/dashboard-context';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { sleevesApi } from '@/services/sleeves-api';
import { DashboardSection, Quote, WatchlistEntry } from '@/types/sleeves';
import { ChevronDown, ChevronRight, LineChart, LayoutGrid, Activity, RefreshCw, MessageSquare, Pencil, Check, X, Plus, Settings, Newspaper, FileText } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';

// Hardcoded sector ETFs (SPDR sector suite)
const SECTOR_ETFS = [
  { ticker: 'XLK', label: 'Technology' },
  { ticker: 'XLF', label: 'Financials' },
  { ticker: 'XLE', label: 'Energy' },
  { ticker: 'XLV', label: 'Health Care' },
  { ticker: 'XLY', label: 'Consumer Disc.' },
  { ticker: 'XLI', label: 'Industrials' },
  { ticker: 'XLB', label: 'Materials' },
  { ticker: 'XLRE', label: 'Real Estate' },
  { ticker: 'XLU', label: 'Utilities' },
  { ticker: 'XLC', label: 'Comm. Services' },
];

// ─── helpers ────────────────────────────────────────────────────────────────

function pctClass(pct: number | null | undefined): string {
  if (pct == null) return 'text-muted-foreground';
  return pct >= 0 ? 'text-emerald-500' : 'text-rose-500';
}

function fmtPct(pct: number | null | undefined): string {
  if (pct == null) return '—';
  return (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
}

function fmtPrice(p: number | null | undefined): string {
  if (p == null) return '—';
  return '$' + p.toFixed(2);
}

// ─── Collapsible section wrapper ────────────────────────────────────────────

function SectionHeader({
  label,
  open,
  onToggle,
  count,
}: {
  label: string;
  open: boolean;
  onToggle: () => void;
  count?: number;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="w-full flex items-center gap-1 px-3 py-1.5 text-xs font-semibold text-muted-foreground hover:text-foreground uppercase tracking-wider group"
    >
      {open ? (
        <ChevronDown className="h-3 w-3 flex-shrink-0" />
      ) : (
        <ChevronRight className="h-3 w-3 flex-shrink-0" />
      )}
      <span className="flex-1 text-left">{label}</span>
      {count != null && (
        <span className="text-[10px] opacity-60 font-normal normal-case">{count}</span>
      )}
    </button>
  );
}

// ─── Ticker row ──────────────────────────────────────────────────────────────

function TickerRow({
  ticker,
  label,
  quote,
  selected,
  onClick,
  indent = false,
}: {
  ticker: string;
  label?: string;
  quote?: Quote;
  selected?: boolean;
  onClick: () => void;
  indent?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors group',
        indent && 'pl-6',
        selected
          ? 'bg-accent text-accent-foreground'
          : 'text-foreground hover:bg-muted/60',
      )}
    >
      {/* Ticker + name/label */}
      <div className="flex flex-col items-start min-w-0 flex-1">
        <span className="font-mono font-semibold text-[11px] leading-tight">{ticker}</span>
        {(label || quote?.name) && (
          <span className="text-[10px] text-muted-foreground truncate max-w-[110px] leading-tight">
            {label || quote?.name}
          </span>
        )}
      </div>

      {/* Sparkline */}
      {quote?.spark && quote.spark.length >= 2 && (
        <MiniSpark
          closes={quote.spark}
          width={48}
          height={20}
          className="flex-shrink-0 opacity-80"
        />
      )}

      {/* Price + pct */}
      <div className="flex flex-col items-end flex-shrink-0">
        <span className="font-mono text-[11px] leading-tight">
          {fmtPrice(quote?.last)}
        </span>
        <span className={cn('text-[10px] leading-tight font-mono', pctClass(quote?.pct_change))}>
          {fmtPct(quote?.pct_change)}
        </span>
      </div>
    </button>
  );
}

// ─── Watchlist editor (inline, triggered from section header) ────────────────

const TICKER_RE = /^[A-Z0-9]{1,10}([.\-][A-Z0-9]{1,6})?$/;

function WatchlistEditor({
  entries,
  onSave,
  onCancel,
}: {
  entries: WatchlistEntry[];
  onSave: (next: WatchlistEntry[]) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<WatchlistEntry[]>(entries);
  const [addInput, setAddInput] = useState('');
  const [addError, setAddError] = useState('');
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const remove = (ticker: string) =>
    setDraft((prev) => prev.filter((e) => e.ticker !== ticker));

  const add = () => {
    const t = addInput.trim().toUpperCase();
    if (!t) return;
    if (!TICKER_RE.test(t)) { setAddError('Invalid ticker'); return; }
    if (draft.some((e) => e.ticker === t)) { setAddError('Already in list'); return; }
    setDraft((prev) => [...prev, { ticker: t, comment: '' }]);
    setAddInput('');
    setAddError('');
  };

  const save = async () => {
    setSaving(true);
    try { await onSave(draft); }
    finally { setSaving(false); }
  };

  return (
    <div className="px-2 pb-2 space-y-1">
      {draft.length === 0 && (
        <p className="text-[11px] text-muted-foreground italic px-1 py-1">No tickers — add one below.</p>
      )}
      {draft.map((e) => (
        <div key={e.ticker} className="flex items-center gap-1 px-2 py-1 rounded bg-muted/30">
          <span className="font-mono text-[11px] font-semibold flex-1">{e.ticker}</span>
          <button
            type="button"
            onClick={() => remove(e.ticker)}
            className="text-muted-foreground hover:text-rose-500 transition-colors"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      ))}
      {/* Add row */}
      <div className="flex items-center gap-1 mt-1">
        <input
          ref={inputRef}
          value={addInput}
          onChange={(e) => { setAddInput(e.target.value); setAddError(''); }}
          onKeyDown={(e) => { if (e.key === 'Enter') add(); }}
          placeholder="Add ticker…"
          className="flex-1 bg-background border border-border rounded px-2 py-1 text-[11px] font-mono uppercase outline-none focus:border-primary"
        />
        <button
          type="button"
          onClick={add}
          className="text-muted-foreground hover:text-primary transition-colors"
          title="Add"
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>
      {addError && <p className="text-[10px] text-rose-500 px-1">{addError}</p>}
      {/* Save / cancel */}
      <div className="flex gap-1.5 pt-1">
        <button
          type="button"
          onClick={() => void save()}
          disabled={saving}
          className="flex items-center gap-1 text-[11px] px-2.5 py-1 rounded bg-primary text-primary-foreground hover:bg-primary/80 transition-colors disabled:opacity-50"
        >
          <Check className="h-3 w-3" />
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="text-[11px] px-2 py-1 rounded text-muted-foreground hover:text-foreground transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

// ─── Main component ──────────────────────────────────────────────────────────

export function LeftNav() {
  const { section, setSection, selectedTicker, setSelectedTicker, chatOpen, toggleChat } = useDashboard();
  const { config, watchlists, updateNamedWatchlist, createNamedWatchlist } = useSleevesContext();

  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [watchlistsOpen, setWatchlistsOpen] = useState(true);
  const [expandedWatchlists, setExpandedWatchlists] = useState<Set<string>>(new Set());
  const [editingWatchlist, setEditingWatchlist] = useState<string | null>(null);
  const [creatingWatchlist, setCreatingWatchlist] = useState(false);
  const [newWatchlistName, setNewWatchlistName] = useState('');
  const [sleevesOpen, setSleevesOpen] = useState(true);
  const [sectorsOpen, setSectorsOpen] = useState(false);
  const [expandedSleeves, setExpandedSleeves] = useState<Set<string>>(new Set());
  const [quotesLoading, setQuotesLoading] = useState(false);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // All tickers we want prices for
  const allTickers = [
    ...watchlists.flatMap((wl) => wl.tickers.map((e) => e.ticker)),
    ...(config?.sleeves.flatMap((s) => s.tickers) ?? []),
    ...SECTOR_ETFS.map((s) => s.ticker),
  ];
  const uniqueTickers = [...new Set(allTickers)];

  const fetchQuotes = useCallback(async () => {
    if (uniqueTickers.length === 0) return;
    setQuotesLoading(true);
    try {
      const data = await sleevesApi.getQuotes(uniqueTickers);
      setQuotes(data.quotes);
    } catch { /* non-fatal */ }
    finally { setQuotesLoading(false); }
  }, [uniqueTickers.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  // Initial fetch + 60s refresh
  useEffect(() => {
    void fetchQuotes();
    refreshTimer.current = setInterval(() => {
      if (document.visibilityState === 'visible') void fetchQuotes();
    }, 60_000);
    return () => { if (refreshTimer.current) clearInterval(refreshTimer.current); };
  }, [fetchQuotes]);

  const toggleSleeve = (name: string) =>
    setExpandedSleeves((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  const navItems: { id: DashboardSection; label: string; icon: React.ComponentType<{className?: string}> }[] = [
    { id: 'market', label: 'Market', icon: LayoutGrid },
    { id: 'screening', label: 'Screening', icon: Activity },
    { id: 'portfolio', label: 'Portfolio', icon: LineChart },
    { id: 'news', label: 'News', icon: Newspaper },
    { id: 'transcripts', label: 'Calls', icon: FileText },
  ];

  return (
    <div className="flex flex-col h-full bg-background border-r border-border select-none">
      {/* App header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <div className="w-6 h-6 rounded bg-primary/20 flex items-center justify-center flex-shrink-0">
          <LineChart className="h-3.5 w-3.5 text-primary" />
        </div>
        <span className="text-sm font-bold tracking-tight">Alpha Engine</span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => void fetchQuotes()}
          disabled={quotesLoading}
          className="text-muted-foreground hover:text-foreground transition-colors"
          title="Refresh prices"
        >
          <RefreshCw className={cn('h-3.5 w-3.5', quotesLoading && 'animate-spin')} />
        </button>
        <button
          type="button"
          onClick={toggleChat}
          className={cn(
            'transition-colors',
            chatOpen ? 'text-primary' : 'text-muted-foreground hover:text-foreground',
          )}
          title="Toggle AI assistant"
        >
          <MessageSquare className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Section navigation */}
      <div className="flex border-b border-border">
        {navItems.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setSection(id)}
            className={cn(
              'flex-1 flex flex-col items-center gap-0.5 py-2 text-[10px] font-medium transition-colors',
              section === id
                ? 'text-foreground border-b-2 border-foreground -mb-px'
                : 'text-muted-foreground hover:text-foreground border-b-2 border-transparent -mb-px',
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        ))}
      </div>

      {/* Scrollable list area */}
      <div className="flex-1 overflow-y-auto">
        {/* ── Watchlists ── */}
        <div className="pt-2">
          <div className="flex items-center">
            <div className="flex-1">
              <SectionHeader
                label="Watchlists"
                open={watchlistsOpen}
                onToggle={() => setWatchlistsOpen((o) => !o)}
                count={watchlists.reduce((n, wl) => n + wl.tickers.length, 0)}
              />
            </div>
            <button
              type="button"
              onClick={() => { setCreatingWatchlist(true); setWatchlistsOpen(true); }}
              className="mr-2 text-muted-foreground hover:text-foreground transition-colors"
              title="New watchlist"
            >
              <Plus className="h-3 w-3" />
            </button>
            <button
              type="button"
              onClick={() => { setSection('market'); setSelectedTicker(null); }}
              className="mr-2 text-muted-foreground hover:text-foreground transition-colors"
              title="Manage watchlists"
            >
              <Settings className="h-3 w-3" />
            </button>
          </div>

          {watchlistsOpen && (
            <div>
              {/* New watchlist inline form */}
              {creatingWatchlist && (
                <div className="px-2 pb-2 pt-1 flex items-center gap-1">
                  <input
                    autoFocus
                    value={newWatchlistName}
                    onChange={(e) => setNewWatchlistName(e.target.value)}
                    onKeyDown={async (e) => {
                      if (e.key === 'Enter' && newWatchlistName.trim()) {
                        await createNamedWatchlist(newWatchlistName.trim());
                        setNewWatchlistName('');
                        setCreatingWatchlist(false);
                      } else if (e.key === 'Escape') {
                        setCreatingWatchlist(false);
                        setNewWatchlistName('');
                      }
                    }}
                    placeholder="Watchlist name…"
                    className="flex-1 bg-background border border-border rounded px-2 py-1 text-[11px] font-mono outline-none focus:border-primary"
                  />
                  <button
                    type="button"
                    onClick={async () => {
                      if (newWatchlistName.trim()) {
                        await createNamedWatchlist(newWatchlistName.trim());
                        setNewWatchlistName('');
                        setCreatingWatchlist(false);
                      }
                    }}
                    className="text-muted-foreground hover:text-primary transition-colors"
                  >
                    <Check className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => { setCreatingWatchlist(false); setNewWatchlistName(''); }}
                    className="text-muted-foreground hover:text-rose-500 transition-colors"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              )}

              {watchlists.length === 0 && !creatingWatchlist && (
                <button
                  type="button"
                  onClick={() => setCreatingWatchlist(true)}
                  className="flex items-center gap-1 px-4 py-2 text-[11px] text-muted-foreground hover:text-foreground italic transition-colors"
                >
                  <Plus className="h-3 w-3" /> Create a watchlist
                </button>
              )}

              {watchlists.map((wl) => {
                const isExpanded = expandedWatchlists.has(wl.name);
                const isEditing = editingWatchlist === wl.name;
                return (
                  <div key={wl.name}>
                    {/* Sub-group header */}
                    <div className="flex items-center group/wl">
                      <button
                        type="button"
                        onClick={() => setExpandedWatchlists((prev) => {
                          const next = new Set(prev);
                          if (next.has(wl.name)) next.delete(wl.name);
                          else next.add(wl.name);
                          return next;
                        })}
                        className="flex-1 flex items-center gap-1.5 px-4 py-1.5 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                      >
                        {isExpanded ? <ChevronDown className="h-2.5 w-2.5 flex-shrink-0" /> : <ChevronRight className="h-2.5 w-2.5 flex-shrink-0" />}
                        <span className="flex-1 text-left font-medium truncate">{wl.name}</span>
                        <span className="text-[10px] opacity-60">{wl.tickers.length}</span>
                      </button>
                      {!isEditing && (
                        <button
                          type="button"
                          onClick={() => { setEditingWatchlist(wl.name); setExpandedWatchlists((prev) => new Set([...prev, wl.name])); }}
                          className="mr-2 opacity-0 group-hover/wl:opacity-100 text-muted-foreground hover:text-foreground transition-all"
                          title="Edit tickers"
                        >
                          <Pencil className="h-2.5 w-2.5" />
                        </button>
                      )}
                    </div>

                    {/* Editor */}
                    {isExpanded && isEditing && (
                      <WatchlistEditor
                        entries={wl.tickers}
                        onSave={async (next) => {
                          await updateNamedWatchlist(wl.name, next);
                          setEditingWatchlist(null);
                        }}
                        onCancel={() => setEditingWatchlist(null)}
                      />
                    )}

                    {/* Tickers */}
                    {isExpanded && !isEditing && wl.tickers.map((entry) => (
                      <TickerRow
                        key={entry.ticker}
                        ticker={entry.ticker}
                        label={entry.comment || undefined}
                        quote={quotes[entry.ticker]}
                        selected={selectedTicker === entry.ticker}
                        onClick={() => { setSelectedTicker(entry.ticker); setSection('market'); }}
                        indent
                      />
                    ))}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* ── My Sleeves ── */}
        <div className="pt-2 border-t border-border/50 mt-2">
          <SectionHeader
            label="My Sleeves"
            open={sleevesOpen}
            onToggle={() => setSleevesOpen((o) => !o)}
            count={config?.sleeves.length}
          />
          {sleevesOpen && (
            <div>
              {(!config || config.sleeves.length === 0) && (
                <p className="px-4 py-2 text-[11px] text-muted-foreground italic">No sleeves configured.</p>
              )}
              {config?.sleeves.map((sleeve) => {
                const expanded = expandedSleeves.has(sleeve.name);
                return (
                  <div key={sleeve.name}>
                    {/* Sleeve header row */}
                    <button
                      type="button"
                      onClick={() => toggleSleeve(sleeve.name)}
                      className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/40 transition-colors"
                    >
                      {expanded ? (
                        <ChevronDown className="h-3 w-3 flex-shrink-0" />
                      ) : (
                        <ChevronRight className="h-3 w-3 flex-shrink-0" />
                      )}
                      <span className="flex-1 text-left font-medium">
                        {sleeve.name.replace(/_/g, ' ')}
                      </span>
                      <span className="text-[10px] opacity-60">{sleeve.tickers.length}</span>
                    </button>
                    {/* Sleeve tickers */}
                    {expanded && sleeve.tickers.map((t) => (
                      <TickerRow
                        key={t}
                        ticker={t}
                        quote={quotes[t]}
                        selected={selectedTicker === t}
                        onClick={() => { setSelectedTicker(t); setSection('market'); }}
                        indent
                      />
                    ))}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* ── Equity Sectors ── */}
        <div className="pt-2 border-t border-border/50 mt-2 mb-4">
          <SectionHeader
            label="Equity Sectors"
            open={sectorsOpen}
            onToggle={() => setSectorsOpen((o) => !o)}
          />
          {sectorsOpen && (
            <div>
              {SECTOR_ETFS.map((s) => (
                <TickerRow
                  key={s.ticker}
                  ticker={s.ticker}
                  label={s.label}
                  quote={quotes[s.ticker]}
                  selected={selectedTicker === s.ticker}
                  onClick={() => setSelectedTicker(s.ticker)}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
