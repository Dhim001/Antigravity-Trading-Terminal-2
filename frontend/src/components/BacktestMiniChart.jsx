/**
 * Compact equity + drawdown charts for backtest preview.
 */
import React, { useEffect, useRef, useMemo } from 'react';
import * as echarts from 'echarts';
import { cn } from '@/lib/utils';

function nearestEquityIndex(equityCurve, tradeTime) {
  if (!equityCurve?.length || tradeTime == null) return -1;
  let best = 0;
  let bestDiff = Math.abs(equityCurve[0].time - tradeTime);
  for (let i = 1; i < equityCurve.length; i++) {
    const diff = Math.abs(equityCurve[i].time - tradeTime);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = i;
    }
  }
  return best;
}

export default function BacktestMiniChart({
  equityCurve,
  drawdownCurve,
  totalPnl,
  trades,
  className,
}) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const hasDrawdown = Boolean(drawdownCurve?.length);

  const tradeMarkers = useMemo(() => {
    if (!trades?.length || !equityCurve?.length) return [];
    return trades.map((t) => {
      const idx = nearestEquityIndex(equityCurve, t.time);
      if (idx < 0) return null;
      const isExit = Boolean(t.is_exit);
      const y = equityCurve[idx]?.equity;
      if (y == null) return null;
      return {
        name: `${t.side} ${t.reason ?? ''}`.trim(),
        coord: [idx, y],
        symbol: isExit ? 'pin' : 'triangle',
        symbolSize: isExit ? 10 : 8,
        itemStyle: {
          color: isExit
            ? ((t.pnl ?? 0) >= 0 ? '#f59e0b' : '#ef4444')
            : '#60a5fa',
        },
      };
    }).filter(Boolean);
  }, [trades, equityCurve]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    let chart = null;
    let disposed = false;

    const mountChart = () => {
      if (disposed || chart) return false;
      const { clientWidth, clientHeight } = el;
      if (clientWidth < 2 || clientHeight < 2) return false;

      chart = echarts.init(el, 'dark');
      chartRef.current = chart;
      return true;
    };

    const ro = new ResizeObserver(() => {
      if (chart) {
        chart.resize();
        return;
      }
      mountChart();
    });
    ro.observe(el);
    mountChart();

    return () => {
      disposed = true;
      ro.disconnect();
      chart?.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    if (!equityCurve?.length) {
      chart.clear();
      return;
    }

    const labels = equityCurve.map((p) => {
      const d = new Date(p.time * 1000);
      return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    });
    const values = equityCurve.map((p) => p.equity);
    const ddValues = (drawdownCurve ?? equityCurve).map(
      (p, i) => drawdownCurve?.[i]?.drawdown_pct ?? 0,
    );
    const up = (totalPnl ?? 0) >= 0;
    const color = up ? '#10b981' : '#ef4444';

    const grids = hasDrawdown
      ? [
          { left: 4, right: 4, top: 8, height: '58%' },
          { left: 4, right: 4, top: '72%', height: '22%' },
        ]
      : [{ left: 4, right: 4, top: 12, bottom: 4, containLabel: true }];

    chart.setOption({
      backgroundColor: 'transparent',
      grid: grids,
      xAxis: hasDrawdown
        ? [
            { type: 'category', data: labels, show: false, boundaryGap: false, gridIndex: 0 },
            { type: 'category', data: labels, show: false, boundaryGap: false, gridIndex: 1 },
          ]
        : {
            type: 'category',
            data: labels,
            show: false,
            boundaryGap: false,
          },
      yAxis: hasDrawdown
        ? [
            {
              type: 'value', scale: true, gridIndex: 0,
              splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
              axisLabel: { fontSize: 9, color: '#9ca3af' },
            },
            {
              type: 'value', gridIndex: 1, max: 100, min: 0,
              splitLine: { show: false },
              axisLabel: { fontSize: 8, color: '#9ca3af', formatter: (v) => `${v}%` },
            },
          ]
        : {
            type: 'value',
            scale: true,
            splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
            axisLabel: { fontSize: 9, color: '#9ca3af' },
          },
      series: [
        {
          type: 'line',
          data: values,
          xAxisIndex: hasDrawdown ? 0 : undefined,
          yAxisIndex: hasDrawdown ? 0 : undefined,
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1.5, color },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: up ? 'rgba(16,185,129,0.25)' : 'rgba(239,68,68,0.25)' },
              { offset: 1, color: 'rgba(0,0,0,0)' },
            ]),
          },
          markPoint: tradeMarkers.length
            ? { symbolKeepAspect: true, data: tradeMarkers, label: { show: false } }
            : undefined,
        },
        ...(hasDrawdown ? [{
          type: 'line',
          data: ddValues,
          xAxisIndex: 1,
          yAxisIndex: 1,
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1, color: '#f87171' },
          areaStyle: { color: 'rgba(248,113,113,0.15)' },
        }] : []),
      ],
      tooltip: {
        trigger: 'axis',
        textStyle: { fontSize: 11 },
        formatter: (params) => {
          const lines = params.map((p) => {
            if (p.seriesIndex === 1) return `DD: ${Number(p.value).toFixed(2)}%`;
            return `Equity: $${Number(p.value).toLocaleString()}`;
          });
          return `${params[0]?.name ?? ''}<br/>${lines.join('<br/>')}`;
        },
      },
    }, { notMerge: true });
  }, [equityCurve, drawdownCurve, totalPnl, tradeMarkers, hasDrawdown]);

  if (!equityCurve?.length) return null;

  return (
    <div
      ref={containerRef}
      className={cn('backtest-mini-chart', hasDrawdown && 'backtest-mini-chart--dual', className)}
      aria-label="Backtest equity and drawdown"
    />
  );
}
