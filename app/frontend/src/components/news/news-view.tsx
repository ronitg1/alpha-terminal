/**
 * NewsView — Market News tab. Three columns:
 *   1. Book headlines — news fanned across your portfolio (sleeve) tickers
 *   2. Ticker search — ad-hoc per-ticker feed
 *   3. Macro — general-market news auto-categorized, with filter pills
 *
 * Book tickers are the configured portfolios' holdings only (watchlists are
 * exploratory, not owned, so they're excluded). News is Finnhub-primary
 * with a Polygon fallback.
 */

import { ArticleCard } from '@/components/news/article-card';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { newsApi } from '@/services/news-api';
import { NewsArticle, NewsFeed } from '@/types/sleeves';
import { cn } from '@/lib/utils';
import { Newspaper, RefreshCw, Search } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

const MACRO_LABELS: Record<string, string> = {
  monetary: 'Monetary · CPI · Fed',
  geopolitics: 'Geopolitics · War · China',
  government: 'Government · Tariffs',
  economy: 'Economy · Jobs · GDP',
  energy: 'Energy · OPEC · Oil',
  markets: 'Markets',
};
const MACRO_ORDER = ['monetary', 'geopolitics', 'government', 'economy', 'energy', 'markets'];

export function NewsView() {
  const { config } = useSleevesContext();

  // Book headlines = your portfolio names only (the configured sleeves).
  // Watchlists are exploratory, not holdings, so they're intentionally
  // excluded — the feed stays focused on what you actually own.
  const tickers = useMemo(
    () => [...new Set(config?.sleeves.flatMap((s) => s.tickers) ?? [])],
    [config],
  );

  const [feed, setFeed] = useState<NewsFeed | null>(null);
  const [loading, setLoading] = useState(false);
  const [macroCat, setMacroCat] = useState<string | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadFeed = useCallback(async () => {
    if (tickers.length === 0) return;
    setLoading(true);
    try {
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
    <div className="h-full overflow-hidden flex flex-col">
      <div className="flex items-center gap-2 px-6 py-3 border-b border-border flex-shrink-0">
        <Newspaper className="h-4 w-4 text-primary" />
        <h1 className="text-lg font-semibold">Market News</h1>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => void loadFeed()}
          disabled={loading}
          className="text-muted-foreground hover:text-foreground transition-colors"
          title="Refresh"
        >
          <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
        </button>
      </div>

      <div className="flex-1 overflow-hidden grid grid-cols-1 lg:grid-cols-3 gap-px bg-border">
        {/* Column 1 — book headlines */}
        <NewsColumn title="Your book" subtitle={`${tickers.length} names across your portfolios`}>
          {feed && feed.book_headlines.length === 0 && !loading && (
            <Empty>No recent headlines for your book.</Empty>
          )}
          {(feed?.book_headlines ?? []).map((a) => (
            <ArticleCard key={a.id} article={a} />
          ))}
          {loading && !feed && <LoadingCards />}
        </NewsColumn>

        {/* Column 2 — ticker search */}
        <TickerSearchColumn />

        {/* Column 3 — macro */}
        <NewsColumn
          title="Macro"
          subtitle={feed?.configured === false ? 'Finnhub key needed for macro feed' : 'General market, auto-categorized'}
        >
          {feed && feed.configured !== false && (
            <div className="flex flex-wrap gap-1 mb-2">
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
          {macroFiltered.map((a) => (
            <ArticleCard key={a.id} article={a} />
          ))}
          {feed && feed.macro.length === 0 && !loading && (
            <Empty>
              {feed.configured === false
                ? 'Add FINNHUB_API_KEY to enable the macro feed.'
                : 'No macro headlines right now.'}
            </Empty>
          )}
          {loading && !feed && <LoadingCards />}
        </NewsColumn>
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
    <NewsColumn title="Ticker search" subtitle="News for any symbol">
      <form
        onSubmit={(e) => { e.preventDefault(); void search(input); }}
        className="flex items-center gap-1.5 mb-2"
      >
        <div className="relative flex-1">
          <Search className="h-3 w-3 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="e.g. TSLA"
            className="w-full bg-background border border-border rounded pl-6 pr-2 py-1 text-xs font-mono uppercase outline-none focus:border-primary"
          />
        </div>
        <button
          type="submit"
          className="text-xs px-2.5 py-1 rounded bg-primary text-primary-foreground hover:bg-primary/80 transition-colors"
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
    </NewsColumn>
  );
}

function NewsColumn({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-background overflow-y-auto">
      <div className="sticky top-0 bg-background/95 backdrop-blur px-4 py-2.5 border-b border-border/60 z-10">
        <h2 className="text-xs font-semibold uppercase tracking-wider">{title}</h2>
        {subtitle && <p className="text-[10px] text-muted-foreground mt-0.5">{subtitle}</p>}
      </div>
      <div className="p-3 space-y-2">{children}</div>
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
