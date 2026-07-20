/**
 * Stitched out-of-sample equity across walk-forward folds.
 */
import React, { useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import { initEcharts } from '@/lib/echartsInit';
import { cn } from '@/lib/utils';

export default function BacktestOosStitchChart({ stitchCurve, className }) {
  const containerRef = useRef(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !stitchCurve?.length) return undefined;

    const chart = initEcharts(el, 'dark');
    const equities = stitchCurve.map((p) => p.equity);
    const folds = stitchCurve.map((p) => p.fold);

    chart.setOption({
      animation: false,
      grid: { left: 40, right: 8, top: 8, bottom: 20 },
      tooltip: {
        trigger: 'axis',
        formatter: (params) => {
          const idx = params?.[0]?.dataIndex ?? 0;
          const pt = stitchCurve[idx];
          return `Fold ${pt?.fold ?? '—'}<br/>$${Number(pt?.equity ?? 0).toFixed(2)}`;
        },
      },
      xAxis: {
        type: 'category',
        data: stitchCurve.map((_, i) => i),
        show: false,
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: {
          fontSize: 9,
          color: '#94a3b8',
          formatter: (v) => `$${Math.round(v)}`,
        },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.12)' } },
      },
      series: [{
        type: 'line',
        data: equities,
        smooth: false,
        lineStyle: { color: '#22c55e', width: 1.5 },
        areaStyle: { color: 'rgba(34,197,94,0.12)' },
        symbol: 'none',
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: 'rgba(148,163,184,0.35)', type: 'dashed' },
          data: (() => {
            const lines = [];
            let prevFold = folds[0];
            for (let i = 1; i < folds.length; i += 1) {
              if (folds[i] !== prevFold) {
                lines.push({ xAxis: i });
                prevFold = folds[i];
              }
            }
            return lines;
          })(),
        },
      }],
    });

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [stitchCurve]);

  if (!stitchCurve?.length) return null;

  return (
    <div className={cn('backtest-oos-stitch', className)}>
      <p className="text-[0.55rem] text-muted-foreground mb-1">Stitched OOS equity (all folds)</p>
      <div ref={containerRef} className="backtest-oos-stitch__canvas" />
    </div>
  );
}
