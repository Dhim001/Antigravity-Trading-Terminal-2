/**
 * Compact equity curve for backtest preview in Algo tab.
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

export default function BacktestMiniChart({ equityCurve, totalPnl, trades, className }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);

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

    const labels = equityCurve.map(p => {
      const d = new Date(p.time * 1000);
      return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    });
    const values = equityCurve.map(p => p.equity);
    const up = (totalPnl ?? 0) >= 0;
    const color = up ? '#10b981' : '#ef4444';

    chart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 4, right: 4, top: 12, bottom: 4, containLabel: true },
      xAxis: {
        type: 'category',
        data: labels,
        show: false,
        boundaryGap: false,
      },
      yAxis: {
        type: 'value',
        scale: true,
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
        axisLabel: { fontSize: 9, color: '#9ca3af' },
      },
      series: [{
        type: 'line',
        data: values,
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
          ? {
              symbolKeepAspect: true,
              data: tradeMarkers,
              label: { show: false },
            }
          : undefined,
      }],
      tooltip: {
        trigger: 'axis',
        textStyle: { fontSize: 11 },
        formatter: (params) => {
          const p = params[0];
          return `${p.name}<br/>Equity: $${Number(p.value).toLocaleString()}`;
        },
      },
    }, { notMerge: true });
  }, [equityCurve, totalPnl, tradeMarkers]);

  if (!equityCurve?.length) return null;

  return (
    <div
      ref={containerRef}
      className={cn('backtest-mini-chart', className)}
      aria-label="Backtest equity curve"
    />
  );
}
