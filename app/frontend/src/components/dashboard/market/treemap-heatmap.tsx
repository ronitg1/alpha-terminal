/**
 * Finviz-style market heatmap: a squarified treemap grouped by sector, tile size =
 * market cap, tile colour = performance (red→green). Defaults to the whole S&P 500;
 * a dropdown switches to a detailed view of the current watchlist. Tapping a tile
 * opens that ticker's research. Responsive (convention #8) — measures its own width
 * and lays out taller on phones so tiles stay tappable.
 */
import { marketApi, type HeatmapTile } from '@/services/market-api';
import { cn } from '@/lib/utils';
import { LayoutGrid } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

type Period = 'today' | 'week' | 'month';
type Source = 'sp500' | 'watchlist';

function perfFor(t: HeatmapTile, period: Period): number | null {
  if (period === 'today') return t.pct_change ?? null;
  const s = t.spark ?? [];
  const back = period === 'week' ? 5 : 21;
  if (s.length <= back) return t.pct_change ?? null;
  const prev = s[s.length - 1 - back];
  const last = s[s.length - 1];
  return prev ? ((last - prev) / prev) * 100 : null;
}

// Continuous-ish red→green scale, capped at ±3% like finviz.
function perfColor(p: number | null): string {
  if (p == null) return '#3f3f46';
  const c = Math.max(-3, Math.min(3, p)) / 3; // -1..1
  if (c >= 0) {
    // muted → bright green
    const g = Math.round(90 + c * 90);
    return `rgb(${Math.round(40 - c * 20)}, ${g}, ${Math.round(55 - c * 20)})`;
  }
  const r = Math.round(90 + -c * 100);
  return `rgb(${r}, ${Math.round(40 + c * 15)}, ${Math.round(50 + c * 15)})`;
}

// ─── Squarified treemap ───────────────────────────────────────────────────────
interface Item<T> { v: number; data: T }
interface Rect<T> { x: number; y: number; w: number; h: number; data: T }

function worstAspect(areas: number[], side: number): number {
  const sum = areas.reduce((a, b) => a + b, 0);
  if (sum <= 0) return Infinity;
  const max = Math.max(...areas), min = Math.min(...areas);
  const s2 = sum * sum;
  return Math.max((side * side * max) / s2, s2 / (side * side * min));
}

function squarify<T>(items: Item<T>[], x: number, y: number, w: number, h: number): Rect<T>[] {
  const total = items.reduce((s, i) => s + i.v, 0);
  if (total <= 0 || w <= 0 || h <= 0) return [];
  const scale = (w * h) / total;
  const scaled = items.map((i) => ({ area: i.v * scale, data: i.data }));
  const out: Rect<T>[] = [];
  layout(scaled, x, y, w, h, out);
  return out;
}

function layout<T>(items: { area: number; data: T }[], x: number, y: number, w: number, h: number, out: Rect<T>[]): void {
  if (!items.length || w <= 0 || h <= 0) return;
  if (items.length === 1) { out.push({ x, y, w, h, data: items[0].data }); return; }
  const side = Math.min(w, h);
  const areas = items.map((i) => i.area);
  let best = 1;
  let bestWorst = Infinity;
  let sum = 0;
  for (let k = 1; k <= items.length; k++) {
    sum += areas[k - 1];
    const wst = worstAspect(areas.slice(0, k), side);
    if (wst <= bestWorst) { bestWorst = wst; best = k; } else break;
  }
  const row = items.slice(0, best);
  const rowArea = row.reduce((s, i) => s + i.area, 0);
  if (w >= h) {
    const stripW = rowArea / h;
    let yy = y;
    for (const it of row) { const ih = it.area / stripW; out.push({ x, y: yy, w: stripW, h: ih, data: it.data }); yy += ih; }
    layout(items.slice(best), x + stripW, y, w - stripW, h, out);
  } else {
    const stripH = rowArea / w;
    let xx = x;
    for (const it of row) { const iw = it.area / stripH; out.push({ x: xx, y, w: iw, h: stripH, data: it.data }); xx += iw; }
    layout(items.slice(best), x, y + stripH, w, h - stripH, out);
  }
}

// ─── Component ────────────────────────────────────────────────────────────────
export function TreemapHeatmap({ tickers, onTicker }: { tickers: string[]; onTicker: (t: string) => void }) {
  const [source, setSource] = useState<Source>('sp500');
  const [period, setPeriod] = useState<Period>('today');
  const [tiles, setTiles] = useState<HeatmapTile[]>([]);
  const [loading, setLoading] = useState(true);
  const [width, setWidth] = useState(0);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const measure = () => { if (ref.current) setWidth(ref.current.clientWidth); };
    measure(); // immediate — the observer's first callback can fire pre-layout at 0
    const ro = new ResizeObserver(measure);
    if (ref.current) ro.observe(ref.current);
    window.addEventListener('resize', measure);
    return () => { ro.disconnect(); window.removeEventListener('resize', measure); };
  }, []);

  // Clear on a deliberate source switch so the loading state shows instead of the
  // previous source's stale tiles (the fetch guard below only protects tickers flips).
  useEffect(() => { setTiles([]); }, [source]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    const p = source === 'sp500' ? marketApi.getSp500Heatmap() : marketApi.getHeatmap(tickers);
    const h = setTimeout(() => {
      p.then((r) => { if (alive) setTiles((prev) => (r.tiles.length ? r.tiles : prev)); })
        .catch(() => {})
        .finally(() => { if (alive) setLoading(false); });
    }, 300);
    return () => { alive = false; clearTimeout(h); };
  }, [source, tickers.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  const height = width > 0 ? Math.max(340, width * (width < 640 ? 1.15 : 0.5)) : 400;

  // Two-level squarified: sectors, then tickers within each sector.
  const { cells, sectorLabels } = useMemo(() => {
    const cells: Rect<HeatmapTile>[] = [];
    const sectorLabels: { x: number; y: number; w: number; name: string }[] = [];
    if (width <= 0) return { cells, sectorLabels };
    const valid = tiles.filter((t) => (t.market_cap ?? 0) > 0);
    const bySector = new Map<string, HeatmapTile[]>();
    for (const t of valid) (bySector.get(t.sector) ?? bySector.set(t.sector, []).get(t.sector)!).push(t);
    const sectors = [...bySector.entries()].map(([name, ts]) => ({
      name, ts, total: ts.reduce((s, t) => s + (t.market_cap ?? 0), 0),
    })).sort((a, b) => b.total - a.total);
    const sectorRects = squarify(sectors.map((s) => ({ v: s.total, data: s })), 0, 0, width, height);
    for (const sr of sectorRects) {
      const pad = 1;
      const hdr = sr.h > 34 && sr.w > 60 ? 13 : 0; // room for the sector label
      if (hdr) sectorLabels.push({ x: sr.x, y: sr.y, w: sr.w, name: sr.data.name });
      const inner = squarify(
        sr.data.ts.sort((a, b) => (b.market_cap ?? 0) - (a.market_cap ?? 0)).map((t) => ({ v: t.market_cap ?? 0, data: t })),
        sr.x + pad, sr.y + hdr + pad, Math.max(0, sr.w - pad * 2), Math.max(0, sr.h - hdr - pad * 2),
      );
      cells.push(...inner);
    }
    return { cells, sectorLabels };
  }, [tiles, width, height]);

  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <LayoutGrid className="h-4 w-4 text-muted-foreground" />
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Market heatmap</span>
        <select
          value={source}
          onChange={(e) => setSource(e.target.value as Source)}
          className="rounded border border-border bg-background px-2 py-1 text-xs outline-none focus:border-primary"
        >
          <option value="sp500">S&amp;P 500</option>
          <option value="watchlist">Watchlist</option>
        </select>
        {source === 'watchlist' && (
          <div className="ml-auto flex rounded-md bg-muted p-0.5 text-[11px]">
            {(['today', 'week', 'month'] as const).map((p) => (
              <button key={p} type="button" onClick={() => setPeriod(p)}
                className={cn('rounded px-2 py-0.5 font-medium capitalize', period === p ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground')}>
                {p}
              </button>
            ))}
          </div>
        )}
      </div>

      <div ref={ref} className="relative w-full overflow-hidden rounded" style={{ height }}>
        {loading && tiles.length === 0 ? (
          <div className="flex h-full items-center justify-center text-xs text-muted-foreground">Building heatmap…</div>
        ) : (
          <>
            {sectorLabels.map((l, i) => (
              <div key={`s${i}`} className="pointer-events-none absolute truncate px-1 text-[9px] font-semibold uppercase tracking-wide text-white/70"
                style={{ left: l.x, top: l.y, width: l.w }}>
                {l.name}
              </div>
            ))}
            {cells.map((rect, i) => {
              const t = rect.data;
              const perf = source === 'watchlist' ? perfFor(t, period) : t.pct_change ?? null;
              const showTicker = rect.w > 26 && rect.h > 16;
              const showPct = rect.w > 40 && rect.h > 30;
              return (
                <button
                  key={`${t.ticker}-${i}`}
                  type="button"
                  onClick={() => onTicker(t.ticker)}
                  title={`${t.ticker} · ${t.industry || t.sector} · ${perf == null ? '' : `${perf >= 0 ? '+' : ''}${perf.toFixed(2)}%`}`}
                  className="absolute flex flex-col items-center justify-center overflow-hidden border border-black/30 text-white transition-[filter] hover:brightness-110"
                  style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h, backgroundColor: perfColor(perf) }}
                >
                  {showTicker && <span className="font-mono text-[10px] font-bold leading-none">{t.ticker}</span>}
                  {showPct && perf != null && (
                    <span className="mt-0.5 text-[9px] leading-none tabular-nums">{perf >= 0 ? '+' : ''}{perf.toFixed(2)}%</span>
                  )}
                </button>
              );
            })}
          </>
        )}
      </div>
      <p className="mt-2 text-[10px] text-muted-foreground">
        Tile size = market cap · colour = {source === 'watchlist' ? period : "today's"} performance · tap to research.
      </p>
    </div>
  );
}
