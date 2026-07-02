/**
 * Canonical (timeframe, lookback) menus for scheduling a pre-scan, mirroring the
 * Pattern Scanner's TIMEFRAME_OPTIONS. The lookbacks per timeframe stay within
 * the backend's per-timeframe max (it clamps anyway); daily includes a 2yr option
 * so a "daily 2yr" premarket/aftermarket scan can be scheduled.
 */
export interface TimeframeOption {
  value: string; // week | day | 1h | 15m
  label: string;
  lookbacks: { label: string; value: number }[];
  defaultLookback: number;
}

export const SCAN_TIMEFRAMES: TimeframeOption[] = [
  {
    value: 'week',
    label: 'Weekly',
    lookbacks: [
      { label: '1yr', value: 365 },
      { label: '2yr', value: 730 },
      { label: '3yr', value: 1095 },
      { label: '5yr', value: 1825 },
    ],
    defaultLookback: 1095,
  },
  {
    value: 'day',
    label: 'Daily',
    lookbacks: [
      { label: '30d', value: 30 },
      { label: '60d', value: 60 },
      { label: '90d', value: 90 },
      { label: '180d', value: 180 },
      { label: '1yr', value: 365 },
      { label: '2yr', value: 730 },
    ],
    defaultLookback: 180,
  },
  {
    value: '1h',
    label: '1h',
    lookbacks: [
      { label: '5d', value: 5 },
      { label: '10d', value: 10 },
      { label: '30d', value: 30 },
      { label: '60d', value: 60 },
      { label: '90d', value: 90 },
    ],
    defaultLookback: 30,
  },
  {
    value: '15m',
    label: '15m',
    lookbacks: [
      { label: '2d', value: 2 },
      { label: '5d', value: 5 },
      { label: '10d', value: 10 },
      { label: '20d', value: 20 },
      { label: '30d', value: 30 },
    ],
    defaultLookback: 10,
  },
];

export function timeframeConfig(tf: string): TimeframeOption {
  return SCAN_TIMEFRAMES.find((t) => t.value === tf) ?? SCAN_TIMEFRAMES[1];
}

export function timeframeLabel(tf: string): string {
  return SCAN_TIMEFRAMES.find((t) => t.value === tf)?.label ?? tf;
}

export function lookbackLabel(tf: string, days: number): string {
  const match = timeframeConfig(tf).lookbacks.find((l) => l.value === days);
  return match?.label ?? `${days}d`;
}
