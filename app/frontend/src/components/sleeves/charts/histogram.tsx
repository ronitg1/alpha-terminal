/**
 * Histogram — inline-SVG distribution chart. Buckets a list of numeric
 * values and renders bar heights proportional to count.
 *
 * Built for the backtest panel's P&L-distribution viz. Direction-aware
 * colour: bars centered on negative bucket midpoints render rose, positive
 * render emerald.
 */

import { cn } from '@/lib/utils';

interface HistogramProps {
  values: number[];
  /** How many buckets to slice the range into. */
  buckets?: number;
  /** Prefix in axis labels (e.g. ``"$"``). */
  prefix?: string;
  /** Suffix in axis labels (e.g. ``"%"``). */
  suffix?: string;
  width?: number;
  height?: number;
  className?: string;
}

export function Histogram({
  values,
  buckets = 20,
  prefix = '',
  suffix = '',
  width = 720,
  height = 180,
  className,
}: HistogramProps) {
  if (!values.length) {
    return (
      <div
        className={cn(
          'flex items-center justify-center text-xs text-muted-foreground italic border border-dashed rounded',
          className,
        )}
        style={{ width: '100%', height }}
      >
        No data to bucket.
      </div>
    );
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const bucketSize = range / buckets;

  const counts = new Array(buckets).fill(0);
  for (const v of values) {
    let idx = Math.floor((v - min) / bucketSize);
    if (idx >= buckets) idx = buckets - 1;
    if (idx < 0) idx = 0;
    counts[idx] += 1;
  }
  const maxCount = Math.max(...counts) || 1;

  const padTop = 12;
  const padBottom = 28;
  const padLeft = 32;
  const padRight = 16;
  const plotW = Math.max(1, width - padLeft - padRight);
  const plotH = Math.max(1, height - padTop - padBottom);
  const barW = plotW / buckets;
  const gap = Math.min(1.5, barW * 0.1);

  // Find the zero line if the range straddles 0, for visual reference.
  const zeroX =
    min < 0 && max > 0 ? padLeft + ((0 - min) / range) * plotW : null;

  return (
    <div className={cn('w-full', className)}>
      <svg
        width="100%"
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className="block"
      >
        {/* Bars */}
        {counts.map((c, i) => {
          const bucketMid = min + bucketSize * (i + 0.5);
          const positive = bucketMid >= 0;
          const cls = positive ? 'fill-emerald-500' : 'fill-rose-500';
          const h = (c / maxCount) * plotH;
          return (
            <rect
              key={i}
              x={padLeft + i * barW + gap / 2}
              y={padTop + plotH - h}
              width={barW - gap}
              height={h}
              className={cls}
              opacity={c > 0 ? 0.85 : 0.15}
            >
              <title>
                {prefix}
                {formatTick(bucketMid)}
                {suffix} · count {c}
              </title>
            </rect>
          );
        })}

        {/* Zero-line marker (if applicable) */}
        {zeroX !== null && (
          <line
            x1={zeroX}
            y1={padTop}
            x2={zeroX}
            y2={padTop + plotH}
            className="stroke-muted-foreground"
            strokeWidth={0.5}
            strokeDasharray="3,3"
          />
        )}

        {/* X-axis labels: min / mid / max */}
        <text
          x={padLeft}
          y={height - 8}
          className="fill-muted-foreground"
          fontSize={9}
          fontFamily="ui-monospace, monospace"
        >
          {prefix}
          {formatTick(min)}
          {suffix}
        </text>
        {zeroX !== null && (
          <text
            x={zeroX}
            y={height - 8}
            textAnchor="middle"
            className="fill-muted-foreground"
            fontSize={9}
            fontFamily="ui-monospace, monospace"
          >
            0
          </text>
        )}
        <text
          x={padLeft + plotW}
          y={height - 8}
          textAnchor="end"
          className="fill-muted-foreground"
          fontSize={9}
          fontFamily="ui-monospace, monospace"
        >
          {prefix}
          {formatTick(max)}
          {suffix}
        </text>

        {/* Y-axis count label (max count only — keeps the chart clean) */}
        <text
          x={padLeft - 4}
          y={padTop + 4}
          textAnchor="end"
          className="fill-muted-foreground"
          fontSize={9}
          fontFamily="ui-monospace, monospace"
        >
          {maxCount}
        </text>
      </svg>
    </div>
  );
}

function formatTick(n: number): string {
  if (!Number.isFinite(n)) return '—';
  if (Math.abs(n) >= 1000) return n.toFixed(0);
  if (Math.abs(n) >= 10) return n.toFixed(1);
  return n.toFixed(2);
}
