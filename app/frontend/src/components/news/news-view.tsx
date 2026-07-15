/**
 * NewsView — News tab with three switchable sub-tabs (same on mobile and desktop,
 * so nothing is a 3-wide squeeze or an endless triple-stack on iOS):
 *   1. Market    — general-market news auto-categorized, with filter pills
 *   2. Watchlist — news fanned across your watchlist + portfolio holdings
 *   3. Ticker    — ad-hoc per-ticker search
 *
 * One feed fetch drives Market (macro) and Watchlist (book_headlines); the tickers
 * fed to it are your watchlist entries plus your configured portfolios' holdings.
 * News is Finnhub-primary with a Polygon fallback.
 */

import { ArticleCard } from '@/components/news/article-card';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { newsApi } from '@/services/news-api';
import { NewsArticle, NewsFeed } from '@/types/sleeves';
import { cn } from '@/lib/utils';
import { Eye, Globe, Newspaper, RefreshCw, Search } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

type NewsTab = 'market' | 'watchlist' | 'ticker';

const MACRO_LABELS: Record<string, string> = {
  monetary: 'Monetary · CPI · Fed',
  geopolitics: 'Geopolitics · War · China',
  government: 'Government · Tariffs',
  economy: 'Economy · Jobs · GDP',
  energy: 'Energy · OPEC · Oil',
  markets: 'Markets',
};
const MACRO_ORDER = ['monetary', 'geopolitics', 'government', 'economy', 'energy', 'markets'];

const TABS: { id: NewsTab; label: string; icon: typeof Globe }[] = [
  { id: 'market', label: 'Market', icon: Globe },
  { id: 'watchlist', label: 'Watchlist', icon: Eye },
  { id: 'ticker', label: 'Ticker', icon: Search },
];

export function NewsView() {
  const { config, watchlist } = useSleevesContext();

  // The Watchlist tab covers your watchlist entries plus your portfolios' holdings
  // (both are things you're tracking). The same tickers seed the feed's
  // book_headlines; macro is independent of them.
  const tickers = useMemo(() => {
    const book = config?.sleeves.flatMap((s) => s.tickers) ?? [];
    const wl = watchlist?.map((w) => w.ticker) ?? [];
    return [...new Set([...wl, ...book])];
  }, [config, watchlist]);

  const [tab, setTab] = useState<NewsTab>('market');
  const [feed, setFeed] = useState<NewsFeed | null>(null);
  const [loading, setLoading] = useState(false);
  const [macroCat, setMacroCat] = useState<string | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadFeed = useCallback(async () => {
    setLoading(true);
    try {
      // Always load (even with no tickers) so the Market/macro feed works.
      setFeed(await newsApi.getFeed(tickers));
    } catch {
      /* non-fatal */
    } finally {
      setLoading(false);
    }
  }, [tickers]);

  useEffect(() => {
    void loadFeed();
    refreshTimer.current = setInterval(() => {
      if (document.visibilityState === 'visible') void loadFeed();
    }, 300_000); // 5 min
    return () => { if (refreshTimer.current) clearInterval(refreshTimer.current); };
  }, [loadFeed]);

  const macroFiltered = useMemo(() => {
    const m = feed?.macro ?? [];
    return macroCat ? m.filter((a) => a.category === macroCat) : m;
  }, [feed, macroCat]);

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex flex-shrink-0 items-center gap-2 border-b border-border px-4 py-3 sm:px-6">
        <Newspaper className="h-4 w-4 text-primary" />
        <h1 className="text-base font-semibold sm:text-lg">News</h1>
        <div className="flex-1" />
        {tab !== 'ticker' && (
          <button
            type="button"
            onClick={() => void loadFeed()}
            disabled={loading}
            className="text-muted-foreground transition-colors hover:text-foreground"
            title="Refresh"
          >
            <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
          </button>
        )}
      </div>

      {/* Sub-tab bar (same on mobile + desktop) */}
      <div className="flex flex-shrink-0 gap-1 border-b border-border px-2 sm:px-4">
        {TABS.map((t) => {
          const Icon = t.icon;
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={cn(
                'flex items-center gap-1.5 border-b-2 px-3 py-2 text-xs font-medium transition-colors sm:text-sm',
                active
                  ? 'border-primary text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground',
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* Active tab content — single readable column, centered on desktop */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl p-3 sm:p-4">
          {tab === 'market' && (
            <>
              {feed && feed.configured !== false && (
                <div className="mb-3 flex flex-wrap gap-1">
                  <CatPill label="All" active={macroCat === null} onClick={() => setMacroCat(null)} count={feed.macro.length} />
                  {MACRO_ORDER.filter((c) => (feed.macro_category_counts[c] ?? 0) > 0).map((c) => (
                    <CatPill
                      key={c}
                      label={MACRO_LABELS[c]}
                      active={macroCat === c}
                      onClick={() => setMacroCat(c)}
                      count={feed.macro_category_counts[c] ?? 0}
                    />
                  ))}
                </div>
              )}
              <div className="space-y-2">
                {macroFiltered.map((a) => (
                  <ArticleCard key={a.id} article={a} />
                ))}
                {feed && feed.macro.length === 0 && !loading && (
                  <Empty>
                    {feed.configured === false
                      ? 'Add FINNHUB_API_KEY to enable the market news feed.'
                      : 'No market news right now.'}
                  </Empty>
                )}
                {loading && !feed && <LoadingCards />}
              </div>
            </>
          )}

          {tab === 'watchlist' && (
            <div className="space-y-2">
              <p className="mb-1 text-[11px] text-muted-foreground">
                {tickers.length} names across your watchlist + portfolios
              </p>
              {feed && feed.book_headlines.length === 0 && !loading && (
                <Empty>
                  {tickers.length === 0
                    ? 'Add tickers to a watchlist or portfolio to see their news here.'
                    : 'No recent headlines for your watchlist.'}
                </Empty>
              )}
              {(feed?.book_headlines ?? []).map((a) => (
                <ArticleCard key={a.id} article={a} />
              ))}
              {loading && !feed && <LoadingCards />}
            </div>
          )}

          {tab === 'ticker' && <TickerSearchColumn />}
        </div>
      </div>
    </div>
  );
}

function TickerSearchColumn() {
  const [input, setInput] = useState('');
  const [ticker, setTicker] = useState<string | null>(null);
  const [articles, setArticles] = useState<NewsArticle[]>([]);
  const [loading, setLoading] = useState(false);

  const search = async (sym: string) => {
    const s = sym.trim().toUpperCase();
    if (!s) return;
    setTicker(s);
    setLoading(true);
    try {
      const r = await newsApi.getTickerNews(s);
      setArticles(r.articles);
    } catch {
      setArticles([]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-2">
      <form
        onSubmit={(e) => { e.preventDefault(); void search(input); }}
        className="mb-2 flex items-center gap-1.5"
      >
        <div className="relative flex-1">
          <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Search any symbol — e.g. TSLA"
            className="w-full rounded border border-border bg-background py-1.5 pl-7 pr-2 text-sm font-mono uppercase outline-none focus:border-primary"
          />
        </div>
        <button
          type="submit"
          className="rounded bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/80"
        >
          Go
        </button>
      </form>
      {!ticker && <Empty>Search a ticker to see its latest news.</Empty>}
      {ticker && loading && <LoadingCards />}
      {ticker && !loading && articles.length === 0 && <Empty>No recent news for {ticker}.</Empty>}
      {articles.map((a) => (
        <ArticleCard key={a.id} article={a} />
      ))}
    </div>
  );
}

function CatPill({
  label,
  active,
  onClick,
  count,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  count: number;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border transition-colors',
        active
          ? 'border-foreground/60 bg-foreground/5 text-foreground'
          : 'border-border text-muted-foreground hover:text-foreground hover:border-foreground/30',
      )}
    >
      {label}
      <span className="opacity-60">{count}</span>
    </button>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-muted-foreground italic px-1 py-2">{children}</p>;
}

function LoadingCards() {
  return (
    <>
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="h-16 rounded-md bg-muted-foreground/5 animate-pulse" />
      ))}
    </>
  );
}
