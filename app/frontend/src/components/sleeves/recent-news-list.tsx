/**
 * RecentNewsList — top 5 headlines from Massive's company-news endpoint,
 * rendered as a compact list with publisher chip + relative date +
 * external link.
 *
 * Empty list and loading states render their own small placeholder rather
 * than letting the section vanish — keeps the drawer layout stable while
 * the user waits or when news is just unavailable.
 */

import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { NewsItem } from '@/types/sleeves';
import { ExternalLink } from 'lucide-react';

interface RecentNewsListProps {
  items: NewsItem[];
  loading?: boolean;
  className?: string;
  /** Cap. Backend returns 5 by default; trim further if a caller wants less. */
  max?: number;
}

export function RecentNewsList({ items, loading, className, max = 5 }: RecentNewsListProps) {
  if (loading) {
    return (
      <div className={cn('space-y-2', className)}>
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="space-y-1">
            <div className="h-2 w-16 rounded bg-muted-foreground/20 animate-pulse" />
            <div className="h-3 w-full rounded bg-muted-foreground/10 animate-pulse" />
          </div>
        ))}
      </div>
    );
  }

  if (!items || items.length === 0) {
    return (
      <div
        className={cn(
          'text-xs text-muted-foreground italic px-2 py-3 rounded border border-dashed',
          className
        )}
      >
        No recent news.
      </div>
    );
  }

  return (
    <ul className={cn('space-y-2', className)}>
      {items.slice(0, max).map((item, i) => (
        <li key={`${item.url || 'no-url'}-${i}`} className="text-xs leading-snug">
          <div className="flex items-center gap-2 mb-0.5">
            <Badge variant="outline" className="text-[10px] py-0 px-1.5 truncate max-w-[160px]">
              {item.source || 'unknown'}
            </Badge>
            <span className="text-[10px] text-muted-foreground tabular-nums">
              {formatRelative(item.date)}
            </span>
          </div>
          {item.url ? (
            <a
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-foreground/90 hover:text-foreground hover:underline inline-flex items-start gap-1"
            >
              <span>{item.title || '(no title)'}</span>
              <ExternalLink className="h-3 w-3 mt-0.5 flex-shrink-0 opacity-60" />
            </a>
          ) : (
            <span className="text-foreground/90">{item.title || '(no title)'}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Render an ISO 8601 timestamp as e.g. "2d ago", "5h ago", "just now".
 *  Returns the raw string when it can't be parsed — defensive against
 *  weird publisher payloads. */
function formatRelative(iso: string): string {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso.slice(0, 10);

  const diffMs = Date.now() - t;
  const sec = Math.max(0, Math.floor(diffMs / 1000));
  if (sec < 60) return 'just now';
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 14) return `${day}d ago`;
  const wk = Math.floor(day / 7);
  if (wk < 8) return `${wk}w ago`;
  // Older than ~2 months — just show the date.
  return iso.slice(0, 10);
}
