/**
 * PriceSparkline — inline-SVG sparkline of daily closes.
 *
 * Deliberately *not* a chart library: per the dashboard's anti-scope rule
 * ("no new chart library, inline SVG only"), this renders a polyline of
 * the close series with min/max labels on the y-axis and an end-price label
 * with a % change badge (green on positive, red on negative).
 *
 * Renders nothing chrome-heavy when there are fewer than 2 bars — the
 * drawer caller is expected to render its own "loading…"/empty state
 * around it.
 */

import { cn } from '@/lib/utils';
import { PriceBar } from '@/types/sleeves';

interface PriceSparklineProps {
  prices: PriceBar[];
  width?: number;
  height?: number;
  className?: string;
}

export function PriceSparkline({
  prices,
  width = 480,
  height = 96,
  className,
}: PriceSparklineProps) {
  if (!prices || prices.length < 2) {
    return (
      <div
        className={cn(
          'flex items-center justify-center text-xs text-muted-foreground italic',
          className
        )}
        style={{ width, height }}
      >
        No price history available.
      </div>
    );
  }

  // Leave room for left-side min/max labels and right-side end-price chip.
  const padTop = 6;
  const padBottom = 14;
  const padLeft = 36;
  const padRight = 72;
  const plotW = Math.max(1, width - padLeft - padRight);
  const plotH = Math.max(1, height - padTop - padBottom);

  const closes = prices.map((p) => p.close);
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const span = max - min || 1; // avoid div-by-zero on a flat series

  const points = closes
    .map((c, i) => {
      const x = padLeft + (i / (closes.length - 1)) * plotW;
      const y = padTop + (1 - (c - min) / span) * plotH;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(' ');

  const first = closes[0];
  const last = closes[closes.length - 1];
  const pctChange = ((last - first) / first) * 100;
  const positive = pctChange >= 0;
  // Tailwind color tokens — same red/green family the SignalPill uses.
  const lineColor = positive ? 'stroke-emerald-500' : 'stroke-rose-500';
  const areaColor = positive ? 'fill-emerald-500/10' : 'fill-rose-500/10';

  // Build a closed path for the soft fill under the line.
  const areaPath = (() => {
    const baselineY = padTop + plotH;
    const firstX = padLeft;
    const lastX = padLeft + plotW;
    return `M ${firstX} ${baselineY} L ${points.split(' ').join(' L ')} L ${lastX} ${baselineY} Z`;
  })();

  return (
    <div className={cn('w-full', className)}>
      <svg
        width="100%"
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className="block"
      >
        {/* Soft fill under the line */}
        <path d={areaPath} className={areaColor} />

        {/* The line itself */}
        <polyline
          points={points}
          fill="none"
          strokeWidth={1.5}
          className={lineColor}
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {/* End-point dot */}
        <circle
          cx={padLeft + plotW}
          cy={padTop + (1 - (last - min) / span) * plotH}
          r={2.5}
          className={positive ? 'fill-emerald-500' : 'fill-rose-500'}
        />

        {/* Y-axis min / max labels */}
        <text
          x={padLeft - 4}
          y={padTop + 4}
          textAnchor="end"
          className="fill-muted-foreground"
          fontSize={9}
          fontFamily="ui-monospace, monospace"
        >
          {formatPrice(max)}
        </text>
        <text
          x={padLeft - 4}
          y={padTop + plotH}
          textAnchor="end"
          className="fill-muted-foreground"
          fontSize={9}
          fontFamily="ui-monospace, monospace"
        >
          {formatPrice(min)}
        </text>

        {/* End price + % change chip on the right */}
        <text
          x={padLeft + plotW + 6}
          y={padTop + 6}
          className="fill-foreground"
          fontSize={11}
          fontFamily="ui-monospace, monospace"
          fontWeight={600}
        >
          {formatPrice(last)}
        </text>
        <text
          x={padLeft + plotW + 6}
          y={padTop + 20}
          className={positive ? 'fill-emerald-500' : 'fill-rose-500'}
          fontSize={10}
          fontFamily="ui-monospace, monospace"
        >
          {positive ? '+' : ''}
          {pctChange.toFixed(2)}%
        </text>

        {/* First / last date labels along the bottom */}
        <text
          x={padLeft}
          y={height - 2}
          className="fill-muted-foreground"
          fontSize={9}
          fontFamily="ui-monospace, monospace"
        >
          {prices[0].time}
        </text>
        <text
          x={padLeft + plotW}
          y={height - 2}
          textAnchor="end"
          className="fill-muted-foreground"
          fontSize={9}
          fontFamily="ui-monospace, monospace"
        >
          {prices[prices.length - 1].time}
        </text>
      </svg>
    </div>
  );
}

function formatPrice(n: number): string {
  // Sub-$1 stocks need more precision; otherwise 2dp is fine.
  if (n < 1) return n.toFixed(4);
  if (n < 100) return n.toFixed(2);
  return n.toFixed(1);
}
