/**
 * MiniSpark — tiny inline-SVG sparkline. No axes, no labels.
 *
 * Pair with formatted price + pct outside the SVG. Used in:
 *   - HighConvictionTiles  (width ~120)
 *   - SleeveSummaryCard    (width ~80)
 *   - Rich TickerRow       (width ~100)
 *
 * The existing PriceSparkline is reserved for the drawer/expanded view —
 * it carries axes + endpoint labels + dates and is too noisy for tile use.
 */
import { cn } from '@/lib/utils';

interface MiniSparkProps {
  closes: number[];
  width?: number;
  height?: number;
  className?: string;
  /** Force a color regardless of net direction (otherwise emerald/rose). */
  forceColor?: 'emerald' | 'rose' | 'muted';
}

export function MiniSpark({
  closes,
  width = 96,
  height = 28,
  className,
  forceColor,
}: MiniSparkProps) {
  if (!closes || closes.length < 2) {
    return (
      <div
        className={cn('opacity-40', className)}
        style={{ width, height }}
        aria-hidden="true"
      />
    );
  }

  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const span = max - min || 1;
  const stepX = closes.length > 1 ? width / (closes.length - 1) : 0;

  const points = closes
    .map((c, i) => {
      const x = i * stepX;
      const y = (1 - (c - min) / span) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  const first = closes[0];
  const last = closes[closes.length - 1];
  const positive = last >= first;
  const direction = forceColor ?? (positive ? 'emerald' : 'rose');
  const stroke =
    direction === 'emerald'
      ? 'stroke-emerald-500'
      : direction === 'rose'
        ? 'stroke-rose-500'
        : 'stroke-muted-foreground';
  const fill =
    direction === 'emerald'
      ? 'fill-emerald-500/15'
      : direction === 'rose'
        ? 'fill-rose-500/15'
        : 'fill-muted-foreground/10';

  const areaPath = `M 0 ${height} L ${points.split(' ').join(' L ')} L ${width} ${height} Z`;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={cn('block', className)}
    >
      <path d={areaPath} className={fill} />
      <polyline
        points={points}
        fill="none"
        strokeWidth={1.2}
        className={stroke}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
