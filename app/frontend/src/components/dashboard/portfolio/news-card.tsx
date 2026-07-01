/**
 * News card for the Portfolio Summary tab. One fetch returns both this account's
 * holdings news (book_headlines) and general market news (macro); a top toggle
 * switches between "My holdings" and "Top market". Reuses the existing news feed.
 * Best-effort — hides itself if nothing loads. Responsive (convention #8).
 */
import { newsApi } from '@/services/news-api';
import { cn } from '@/lib/utils';
import type { NewsArticle } from '@/types/sleeves';
import type { PortfolioAccount } from '@/types/portfolio';
import { useEffect, useMemo, useState } from 'react';

function timeAgo(unixSeconds: number): string {
  const s = Math.max(0, Date.now() / 1000 - unixSeconds);
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

function ArticleRow({ a }: { a: NewsArticle }) {
  return (
    <a
      href={a.url}
      target="_blank"
      rel="noopener noreferrer"
      className="block rounded px-1 py-1.5 hover:bg-muted/40"
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="line-clamp-2 text-xs font-medium leading-snug">{a.headline}</div>
          <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
            <span className="truncate">{a.source}</span>
            {a.related && <span className="font-mono">· {a.related}</span>}
            <span className="ml-auto shrink-0">{timeAgo(a.datetime)}</span>
          </div>
        </div>
      </div>
    </a>
  );
}

export function NewsCard({ account }: { account: PortfolioAccount }) {
  const [holdings, setHoldings] = useState<NewsArticle[]>([]);
  const [market, setMarket] = useState<NewsArticle[]>([]);
  const [tab, setTab] = useState<'holdings' | 'market'>('holdings');
  const [loading, setLoading] = useState(true);

  const tickers = useMemo(
    () => Array.from(new Set(account.positions.map((p) => p.underlying).filter(Boolean))).slice(0, 20) as string[],
    [account],
  );

  useEffect(() => {
    let alive = true;
    setLoading(true);
    newsApi
      .getFeed(tickers)
      .then((feed) => {
        if (!alive) return;
        setHoldings(feed.book_headlines ?? []);
        setMarket(feed.macro ?? []);
      })
      .catch(() => {})
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [tickers]);

  // Default to whichever tab has content (holdings news is often sparser).
  useEffect(() => {
    if (!loading && holdings.length === 0 && market.length > 0) setTab('market');
  }, [loading, holdings.length, market.length]);

  if (!loading && holdings.length === 0 && market.length === 0) return null;
  const rows = tab === 'holdings' ? holdings : market;

  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">News</span>
        <div className="ml-auto flex rounded-md bg-muted p-0.5 text-[11px]">
          {(['holdings', 'market'] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className={cn(
                'rounded px-2 py-0.5 font-medium',
                tab === t ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground',
              )}
            >
              {t === 'holdings' ? 'My holdings' : 'Top market'}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-2 max-h-80 space-y-0.5 overflow-y-auto">
        {loading ? (
          <p className="px-1 py-2 text-xs text-muted-foreground">Loading news…</p>
        ) : rows.length === 0 ? (
          <p className="px-1 py-2 text-xs italic text-muted-foreground">
            {tab === 'holdings' ? 'No recent news for your holdings.' : 'No market news right now.'}
          </p>
        ) : (
          rows.slice(0, 30).map((a) => <ArticleRow key={a.id || a.url} a={a} />)
        )}
      </div>
    </div>
  );
}
