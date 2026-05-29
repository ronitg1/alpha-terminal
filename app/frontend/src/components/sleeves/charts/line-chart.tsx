/**
 * LineChart — reusable inline-SVG line chart with x/y axes, gridlines, and
 * optional vertical trade-marker overlays.
 *
 * Built per the dashboard's anti-scope rule (no chart library). For the
 * heavy-weight needs of the backtest tab — equity curves, P&L cumulatives
 * — the bare sparkline didn't carry enough context. This component adds:
 *   - dated x-axis with first/last labels and a midpoint
 *   - y-axis with min/max + a midline label
 *   - optional baseline (e.g. initial capital line)
 *   - optional vertical markers at specific x-positions (trade entries/exits)
 *   - color follows positive/negative direction
 *
 * Hover handling is intentionally minimal — for richer interactivity a
 * proper chart lib would be the right call.
 */

import { cn } from '@/lib/utils';

export interface LinePoint {
  /** ISO date or arbitrary x-axis label rendered at boundaries. */
  x: string;
  /** Y value. */
  y: number;
}

export interface LineMarker {
  /** The ``x`` value to align to — matches a point's x in ``points``. */
  x: string;
  /** Marker type — controls colour. */
  kind: 'entry' | 'exit' | 'neutral';
  /** Optional hover label. */
  label?: string;
}

interface LineChartProps {
  points: LinePoint[];
  /** Render a horizontal reference line (e.g. initial-capital baseline). */
  baseline?: number;
  /** Vertical markers — trade entries/exits typically. Aligned by ``x``. */
  markers?: LineMarker[];
  width?: number;
  height?: number;
  /** Prefix prepended to y-axis values (e.g. ``"$"``). */
  yPrefix?: string;
  className?: string;
}

export function LineChart({
  points,
  baseline,
  markers = [],
  width = 720,
  height = 220,
  yPrefix = '',
  className,
}: LineChartProps) {
  if (!points || points.length < 2) {
    return (
      <div
        className={cn(
          'flex items-center justify-center text-xs text-muted-foreground italic border border-dashed rounded',
          className,
        )}
        style={{ width: '100%', height }}
      >
        Not enough data points to render a chart.
      </div>
    );
  }

  const padTop = 16;
  const padBottom = 28;
  const padLeft = 56;
  const padRight = 16;
  const plotW = Math.max(1, width - padLeft - padRight);
  const plotH = Math.max(1, height - padTop - padBottom);

  const ys = points.map((p) => p.y);
  let minY = Math.min(...ys);
  let maxY = Math.max(...ys);
  if (baseline !== undefined) {
    minY = Math.min(minY, baseline);
    maxY = Math.max(maxY, baseline);
  }
  // Add ~5% headroom so the line doesn't kiss the box.
  const range = maxY - minY || 1;
  const headroom = range * 0.05;
  minY -= headroom;
  maxY += headroom;
  const span = maxY - minY || 1;

  const xAt = (i: number) => padLeft + (i / (points.length - 1)) * plotW;
  const yAt = (val: number) => padTop + (1 - (val - minY) / span) * plotH;

  const polyline = points.map((p, i) => `${xAt(i).toFixed(1)},${yAt(p.y).toFixed(1)}`).join(' ');

  // Index by x for marker positioning. If a marker's x isn't a known point
  // (e.g. trade closed mid-window), we fall back to nearest by string order.
  const xIndex = new Map<string, number>();
  points.forEach((p, i) => xIndex.set(p.x, i));

  // Direction-aware line colour: green if last >= first, red otherwise.
  const positive = ys[ys.length - 1] >= ys[0];
  const lineCls = positive ? 'stroke-emerald-500' : 'stroke-rose-500';
  const fillCls = positive ? 'fill-emerald-500/10' : 'fill-rose-500/10';

  // Area path under the line for soft fill.
  const baselineY = padTop + plotH;
  const areaPath =
    `M ${padLeft} ${baselineY} ` +
    `L ${polyline.split(' ').join(' L ')} ` +
    `L ${padLeft + plotW} ${baselineY} Z`;

  // Midpoint x-axis tick (lots cleaner than rendering every date).
  const midIdx = Math.floor((points.length - 1) / 2);
  const midY = minY + span / 2;

  return (
    <div className={cn('w-full', className)}>
      <svg
        width="100%"
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className="block"
      >
        {/* Gridlines: y at min / mid / max */}
        {[minY, midY, maxY].map((val) => (
          <g key={`grid-${val}`}>
            <line
              x1={padLeft}
              y1={yAt(val)}
              x2={padLeft + plotW}
              y2={yAt(val)}
              className="stroke-border"
              strokeWidth={0.5}
              strokeDasharray="2,3"
            />
            <text
              x={padLeft - 4}
              y={yAt(val) + 3}
              textAnchor="end"
              className="fill-muted-foreground"
              fontSize={9}
              fontFamily="ui-monospace, monospace"
            >
              {yPrefix}
              {formatYTick(val)}
            </text>
          </g>
        ))}

        {/* Baseline reference (e.g. initial capital) */}
        {baseline !== undefined && (
          <g>
            <line
              x1={padLeft}
              y1={yAt(baseline)}
              x2={padLeft + plotW}
              y2={yAt(baseline)}
              className="stroke-muted-foreground"
              strokeWidth={0.75}
              strokeDasharray="4,3"
              opacity={0.6}
            />
            <text
              x={padLeft + plotW + 4}
              y={yAt(baseline) + 3}
              className="fill-muted-foreground"
              fontSize={8}
              fontFamily="ui-monospace, monospace"
            >
              start
            </text>
          </g>
        )}

        {/* Soft area + line */}
        <path d={areaPath} className={fillCls} />
        <polyline
          points={polyline}
          fill="none"
          strokeWidth={1.5}
          className={lineCls}
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {/* Trade markers — small vertical ticks colour-coded by kind. */}
        {markers.map((m, i) => {
          const idx = xIndex.get(m.x);
          if (idx === undefined) return null;
          const x = xAt(idx);
          const cls =
            m.kind === 'entry'
              ? 'stroke-amber-500'
              : m.kind === 'exit'
                ? 'stroke-sky-500'
                : 'stroke-muted-foreground';
          return (
            <g key={`marker-${i}`}>
              <line
                x1={x}
                y1={padTop + 2}
                x2={x}
                y2={padTop + plotH - 2}
                className={cls}
                strokeWidth={1}
                opacity={0.6}
              >
                {m.label && <title>{m.label}</title>}
              </line>
            </g>
          );
        })}

        {/* End-point dot */}
        <circle
          cx={xAt(points.length - 1)}
          cy={yAt(points[points.length - 1].y)}
          r={2.5}
          className={positive ? 'fill-emerald-500' : 'fill-rose-500'}
        />

        {/* X-axis labels: first, mid, last */}
        <text
          x={padLeft}
          y={height - 8}
          className="fill-muted-foreground"
          fontSize={9}
          fontFamily="ui-monospace, monospace"
        >
          {points[0].x}
        </text>
        <text
          x={padLeft + plotW / 2}
          y={height - 8}
          textAnchor="middle"
          className="fill-muted-foreground"
          fontSize={9}
          fontFamily="ui-monospace, monospace"
        >
          {points[midIdx].x}
        </text>
        <text
          x={padLeft + plotW}
          y={height - 8}
          textAnchor="end"
          className="fill-muted-foreground"
          fontSize={9}
          fontFamily="ui-monospace, monospace"
        >
          {points[points.length - 1].x}
        </text>
      </svg>
    </div>
  );
}

function formatYTick(n: number): string {
  if (!Number.isFinite(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  if (abs >= 10) return n.toFixed(0);
  return n.toFixed(2);
}
