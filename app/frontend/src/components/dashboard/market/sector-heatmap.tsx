/**
 * Sector heatmap for the Market summary: watchlist tiled by sector, tile size ≈
 * market cap, tile colour = performance (red→green). At-a-glance sector rotation
 * and where the day's action is. Today / Week / Month toggle (week/month derived
 * from each name's sparkline). Tapping a tile opens that ticker's research.
 * Responsive (convention #8) — tiles wrap with a min size for tap targets.
 */
import { marketApi, type HeatmapTile } from '@/services/market-api';
import { cn } from '@/lib/utils';
import { LayoutGrid } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

type Period = 'today' | 'week' | 'month';

function perfFor(t: HeatmapTile, period: Period): number | null {
  if (period === 'today') return t.pct_change ?? null;
  const s = t.spark ?? [];
  const back = period === 'week' ? 5 : 21;
  if (s.length <= back) return null;
  const prev = s[s.length - 1 - back];
  const last = s[s.length - 1];
  return prev ? ((last - prev) / prev) * 100 : null;
}

// Bucketed red→green scale (readable text on the stronger buckets).
function perfColor(p: number | null): string {
  if (p == null) return 'bg-muted text-muted-foreground';
  if (p >= 3) return 'bg-emerald-600 text-white';
  if (p >= 1) return 'bg-emerald-500/80 text-white';
  if (p > 0) return 'bg-emerald-500/30 text-foreground';
  if (p === 0) return 'bg-muted text-muted-foreground';
  if (p > -1) return 'bg-rose-500/30 text-foreground';
  if (p > -3) return 'bg-rose-500/80 text-white';
  return 'bg-rose-600 text-white';
}

function fmtCap(mc: number | null): string {
  if (!mc) return '';
  if (mc >= 1_000_000) return `$${(mc / 1_000_000).toFixed(1)}T`;
  if (mc >= 1_000) return `$${(mc / 1_000).toFixed(0)}B`;
  return `$${mc.toFixed(0)}M`;
}

export function SectorHeatmap({ tickers, onTicker }: { tickers: string[]; onTicker: (t: string) => void }) {
  const [tiles, setTiles] = useState<HeatmapTile[]>([]);
  const [loading, setLoading] = useState(true);
  const [period, setPeriod] = useState<Period>('today');

  useEffect(() => {
    let alive = true;
    setLoading(true);
    const h = setTimeout(() => {
      marketApi
        .getHeatmap(tickers)
        // Don't let a transient empty-ticker response wipe loaded tiles.
        .then((r) => { if (alive) setTiles((prev) => (r.tiles.length ? r.tiles : prev)); })
        .catch(() => {})
        .finally(() => { if (alive) setLoading(false); });
    }, 400);
    return () => { alive = false; clearTimeout(h); };
  }, [tickers.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  const sectors = useMemo(() => {
    const withCap = tiles.filter((t) => (t.market_cap ?? 0) > 0);
    const map = new Map<string, HeatmapTile[]>();
    for (const t of withCap) {
      const list = map.get(t.sector) ?? map.set(t.sector, []).get(t.sector)!;
      list.push(t);
    }
    return [...map.entries()]
      .map(([name, ts]) => ({
        name,
        total: ts.reduce((s, t) => s + (t.market_cap ?? 0), 0),
        tiles: ts.slice().sort((a, b) => (b.market_cap ?? 0) - (a.market_cap ?? 0)),
      }))
      .sort((a, b) => b.total - a.total);
  }, [tiles]);

  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <LayoutGrid className="h-4 w-4 text-muted-foreground" />
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Sector heatmap</span>
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

      {loading && tiles.length === 0 ? (
        <p className="text-xs text-muted-foreground">Building heatmap…</p>
      ) : sectors.length === 0 ? (
        <p className="text-xs italic text-muted-foreground">No market-cap data for this watchlist yet.</p>
      ) : (
        <div className="space-y-2">
          {sectors.map((sec) => (
            <div key={sec.name}>
              <div className="mb-1 flex items-baseline gap-2">
                <span className="text-[11px] font-medium">{sec.name}</span>
                <span className="text-[10px] text-muted-foreground">{fmtCap(sec.total)} · {sec.tiles.length}</span>
              </div>
              <div className="flex flex-wrap gap-1">
                {sec.tiles.map((t) => {
                  const perf = perfFor(t, period);
                  return (
                    <button
                      key={t.ticker}
                      type="button"
                      onClick={() => onTicker(t.ticker)}
                      title={`${t.ticker} · ${t.name} · ${fmtCap(t.market_cap)}`}
                      style={{ flexGrow: Math.max(1, Math.round((t.market_cap ?? 1) / 1000)), flexBasis: '64px' }}
                      className={cn('flex h-14 min-w-[64px] flex-col items-center justify-center rounded px-1 transition-opacity hover:opacity-90', perfColor(perf))}
                    >
                      <span className="font-mono text-[11px] font-bold leading-none">{t.ticker}</span>
                      <span className="mt-0.5 text-[10px] leading-none tabular-nums">
                        {perf == null ? '—' : `${perf >= 0 ? '+' : ''}${perf.toFixed(1)}%`}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
