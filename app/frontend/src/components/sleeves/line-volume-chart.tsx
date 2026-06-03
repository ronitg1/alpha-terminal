/**
 * LineVolumeChart — lightweight-charts line series (price) + histogram (volume).
 *
 * Replaces the SVG PriceSparkline in the Market view ticker detail. Uses the
 * same lightweight-charts instance that chart-modal.tsx uses for pattern charts.
 */

import { useEffect, useRef } from 'react';
import { createChart, ColorType } from 'lightweight-charts';
import type { PriceBar } from '@/types/sleeves';

const DARK_THEME = {
  layout: {
    background: { type: ColorType.Solid, color: 'transparent' },
    textColor: '#9ca3af',
  },
  grid: {
    vertLines: { color: '#1f293720' },
    horzLines: { color: '#1f293720' },
  },
  rightPriceScale: { borderColor: '#374151' },
  timeScale: { borderColor: '#374151', timeVisible: false },
};

interface LineVolumeChartProps {
  bars: PriceBar[];
  priceHeight?: number;
  volHeight?: number;
}

export function LineVolumeChart({ bars, priceHeight = 160, volHeight = 48 }: LineVolumeChartProps) {
  const priceRef = useRef<HTMLDivElement>(null);
  const volRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!priceRef.current || !volRef.current || bars.length < 2) return;

    let removed = false;

    const first = bars[0].close;
    const last = bars[bars.length - 1].close;
    const positive = last >= first;
    const lineColor = positive ? '#10b981' : '#ef4444';
    const areaTopColor = positive ? '#10b98130' : '#ef444430';

    // ── Price chart ──────────────────────────────────────────────────────────
    const priceChart = createChart(priceRef.current, {
      ...DARK_THEME,
      width: priceRef.current.clientWidth,
      height: priceHeight,
      handleScroll: { mouseWheel: false, pressedMouseMove: true },
      handleScale: { mouseWheel: false, pinch: false },
    });

    const lineSeries = priceChart.addAreaSeries({
      lineColor,
      topColor: areaTopColor,
      bottomColor: 'transparent',
      lineWidth: 2,
      crosshairMarkerVisible: true,
    });

    lineSeries.setData(
      bars.map((b) => ({ time: b.time as string, value: b.close })),
    );
    priceChart.timeScale().fitContent();

    // ── Volume chart ─────────────────────────────────────────────────────────
    const volChart = createChart(volRef.current, {
      ...DARK_THEME,
      width: volRef.current.clientWidth,
      height: volHeight,
      handleScroll: { mouseWheel: false, pressedMouseMove: false },
      handleScale: { mouseWheel: false, pinch: false },
      rightPriceScale: { visible: false },
      timeScale: { ...DARK_THEME.timeScale, timeVisible: true, borderVisible: false },
    });

    const volSeries = volChart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'vol',
    });
    volChart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.1, bottom: 0 }, visible: false });

    volSeries.setData(
      bars.map((b) => ({
        time: b.time as string,
        value: b.volume,
        color: b.close >= b.open ? '#10b98150' : '#ef444450',
      })),
    );
    volChart.timeScale().fitContent();

    // ── Sync crosshair / scroll ───────────────────────────────────────────────
    priceChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (range && !removed) volChart.timeScale().setVisibleLogicalRange(range);
    });

    // ── Resize observer ───────────────────────────────────────────────────────
    const ro = new ResizeObserver(() => {
      if (!priceRef.current || !volRef.current || removed) return;
      priceChart.applyOptions({ width: priceRef.current.clientWidth });
      volChart.applyOptions({ width: volRef.current.clientWidth });
    });
    if (priceRef.current) ro.observe(priceRef.current);
    if (volRef.current) ro.observe(volRef.current);

    return () => {
      removed = true;
      ro.disconnect();
      priceChart.remove();
      volChart.remove();
    };
  }, [bars, priceHeight, volHeight]);

  if (bars.length < 2) {
    return (
      <div
        className="flex items-center justify-center text-xs text-muted-foreground italic"
        style={{ height: priceHeight + volHeight }}
      >
        No price history available.
      </div>
    );
  }

  return (
    <div className="w-full">
      <div ref={priceRef} className="w-full" />
      <div ref={volRef} className="w-full mt-0.5" />
    </div>
  );
}
