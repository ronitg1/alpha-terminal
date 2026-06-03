/**
 * ArticleCard — one news item with an inline "AI summarize" action.
 *
 * Summarize calls /news/summarize, which returns 3 bullets + a relevance read
 * grounded in which sleeve (if any) holds the related ticker.
 */

import { newsApi } from '@/services/news-api';
import { ArticleSummary, NewsArticle } from '@/types/sleeves';
import { cn } from '@/lib/utils';
import { ExternalLink, Sparkles } from 'lucide-react';
import { useState } from 'react';

function timeAgo(unixSeconds: number): string {
  if (!unixSeconds) return '';
  const secs = Math.max(0, Date.now() / 1000 - unixSeconds);
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}

const RELEVANCE_CLS: Record<string, string> = {
  high: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
  medium: 'border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400',
  low: 'border-border bg-muted/40 text-muted-foreground',
};

export function ArticleCard({ article }: { article: NewsArticle }) {
  const [summary, setSummary] = useState<ArticleSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const summarize = async () => {
    if (summary) { setSummary(null); return; }
    setLoading(true);
    setErr(null);
    try {
      const s = await newsApi.summarize({
        title: article.headline,
        description: article.summary,
        related: article.related,
      });
      setSummary(s);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="rounded-md border border-border bg-card p-3 hover:border-foreground/20 transition-colors">
      <a href={article.url} target="_blank" rel="noopener noreferrer" className="group block">
        <div className="flex items-start gap-1.5">
          <h3 className="text-xs font-medium leading-snug text-foreground group-hover:text-primary transition-colors flex-1">
            {article.headline}
          </h3>
          <ExternalLink className="h-3 w-3 text-muted-foreground flex-shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity" />
        </div>
      </a>
      <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
        <span className="text-[10px] text-muted-foreground">{article.source}</span>
        {article.datetime > 0 && (
          <>
            <span className="text-[10px] text-muted-foreground/50">·</span>
            <span className="text-[10px] text-muted-foreground">{timeAgo(article.datetime)}</span>
          </>
        )}
        {article.related && (
          <span className="text-[9px] font-mono border border-border rounded px-1 py-0 text-muted-foreground">
            {article.related}
          </span>
        )}
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => void summarize()}
          disabled={loading}
          className={cn(
            'inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded transition-colors disabled:opacity-50',
            summary ? 'text-sky-500' : 'text-muted-foreground hover:text-sky-500',
          )}
          title="Summarize in 3 bullets + why it matters to your book"
        >
          <Sparkles className="h-2.5 w-2.5" />
          {loading ? 'Thinking…' : summary ? 'Hide' : 'Summarize'}
        </button>
      </div>

      {err && <p className="text-[10px] text-rose-500 italic mt-2">{err}</p>}

      {summary && (
        <div className="mt-2.5 pt-2.5 border-t border-border/50 space-y-2">
          <ul className="space-y-1">
            {summary.summary.map((b, i) => (
              <li key={i} className="text-[11px] leading-relaxed text-foreground/90 flex gap-1.5">
                <span className="text-sky-500 flex-shrink-0">•</span>
                <span>{b}</span>
              </li>
            ))}
          </ul>
          {summary.relevanceReason && (
            <div className="flex items-start gap-1.5">
              <span
                className={cn(
                  'text-[9px] font-semibold uppercase px-1 py-0.5 rounded border flex-shrink-0',
                  RELEVANCE_CLS[summary.relevance] ?? RELEVANCE_CLS.low,
                )}
              >
                {summary.relevance}
              </span>
              <p className="text-[10px] text-muted-foreground leading-relaxed">
                {summary.relevanceReason}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
