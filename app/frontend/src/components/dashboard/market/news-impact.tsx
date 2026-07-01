/**
 * News + thesis-impact panel: recent headlines filtered to the watchlist tickers,
 * each with a Claude one-liner on what changed and whether it supports / threatens
 * / is neutral to the thesis — instead of a raw feed. One batched LLM call on the
 * backend, cached per day. Responsive (convention #8).
 */
import { newsApi, type ThesisImpactItem } from '@/services/news-api';
import { cn } from '@/lib/utils';
import { Newspaper, RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';

const IMPACT: Record<string, { label: string; cls: string; dot: string }> = {
  supports: { label: 'Supports', cls: 'bg-emerald-500/15 text-emerald-500', dot: 'bg-emerald-500' },
  threatens: { label: 'Threatens', cls: 'bg-rose-500/15 text-rose-500', dot: 'bg-rose-500' },
  neutral: { label: 'Neutral', cls: 'bg-muted text-muted-foreground', dot: 'bg-muted-foreground' },
};

function timeAgo(unix: number | null): string {
  if (!unix) return '';
  const s = Math.max(0, Date.now() / 1000 - unix);
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

export function NewsImpact({ tickers }: { tickers: string[] }) {
  const [items, setItems] = useState<ThesisImpactItem[]>([]);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    let alive = true;
    newsApi
      .getThesisImpact(tickers)
      .then((r) => { if (alive) setItems((prev) => (r.items.length ? r.items : prev)); })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  };

  useEffect(() => {
    const cancel = load();
    return cancel;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tickers.join(',')]);

  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="mb-3 flex items-center gap-2">
        <Newspaper className="h-4 w-4 text-muted-foreground" />
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">News &amp; thesis impact</span>
        <button type="button" onClick={() => load()} disabled={loading} className="ml-auto text-muted-foreground hover:text-foreground disabled:opacity-50" title="Refresh">
          <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
        </button>
      </div>

      {loading && items.length === 0 ? (
        <p className="text-xs text-muted-foreground">Reading the tape…</p>
      ) : items.length === 0 ? (
        <p className="text-xs italic text-muted-foreground">No recent headlines for these names.</p>
      ) : (
        <div className="max-h-96 space-y-1 overflow-y-auto">
          {items.map((it, i) => {
            const meta = IMPACT[it.impact] ?? IMPACT.neutral;
            return (
              <a
                key={i}
                href={it.url}
                target="_blank"
                rel="noopener noreferrer"
                className="block rounded-md p-2 hover:bg-muted/40"
              >
                <div className="flex items-center gap-2">
                  <span className={cn('shrink-0 rounded px-1.5 py-0.5 text-[9px] font-medium', meta.cls)}>{meta.label}</span>
                  {it.ticker && <span className="font-mono text-[11px] font-semibold">{it.ticker}</span>}
                  <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
                    {it.source}{it.datetime ? ` · ${timeAgo(it.datetime)}` : ''}
                  </span>
                </div>
                <div className="mt-1 line-clamp-2 text-xs font-medium leading-snug">{it.headline}</div>
                {it.line && (
                  <div className="mt-0.5 flex items-start gap-1.5">
                    <span className={cn('mt-1 h-1.5 w-1.5 shrink-0 rounded-full', meta.dot)} />
                    <span className="text-[11px] italic leading-snug text-muted-foreground">{it.line}</span>
                  </div>
                )}
              </a>
            );
          })}
        </div>
      )}
    </div>
  );
}
