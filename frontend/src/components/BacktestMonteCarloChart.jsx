/**
 * Monte Carlo confidence fan chart from bootstrap fan bands.
 */
import React, { useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import { initEcharts } from '@/lib/echartsInit';
import { cn } from '@/lib/utils';

export default function BacktestMonteCarloChart({ monteCarlo, startingEquity, className }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const fan = monteCarlo?.fan_bands;
  const isLab = className?.includes('backtest-mc-chart--lab');

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !fan?.length) return undefined;

    const chart = initEcharts(el, 'dark');
    chartRef.current = chart;

    const steps = fan.map((b) => b.step);
    const p5 = fan.map((b) => b.equity_p5 ?? b.pnl_p5);
    const p50 = fan.map((b) => b.equity_p50 ?? b.pnl_p50);
    const p95 = fan.map((b) => b.equity_p95 ?? b.pnl_p95);

    chart.setOption({
      animation: false,
      grid: {
        left: isLab ? 56 : 36,
        right: isLab ? 16 : 8,
        top: isLab ? 20 : 8,
        bottom: isLab ? 32 : 22,
      },
      tooltip: {
        trigger: 'axis',
        textStyle: { fontSize: isLab ? 11 : 10 },
        formatter: (params) => {
          const step = params?.[0]?.axisValue;
          const row = fan.find((b) => b.step === step);
          if (!row) return '';
          return `Trade ${step}<br/>P5 $${row.pnl_p5}<br/>P50 $${row.pnl_p50}<br/>P95 $${row.pnl_p95}`;
        },
      },
      xAxis: {
        type: 'category',
        data: steps,
        name: 'Trade #',
        nameTextStyle: { fontSize: isLab ? 10 : 9, color: '#94a3b8' },
        axisLabel: { fontSize: isLab ? 10 : 9, color: '#94a3b8' },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: {
          fontSize: isLab ? 10 : 9,
          color: '#94a3b8',
          formatter: (v) => `$${Math.round(v)}`,
        },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.12)' } },
      },
      series: [
        {
          name: 'P95',
          type: 'line',
          data: p95,
          lineStyle: { width: 0 },
          areaStyle: { color: 'rgba(34,197,94,0.18)' },
          stack: 'fan',
          symbol: 'none',
        },
        {
          name: 'P50 band',
          type: 'line',
          data: p50,
          lineStyle: { width: 0 },
          areaStyle: { color: 'rgba(59,130,246,0.22)' },
          stack: 'fan',
          symbol: 'none',
        },
        {
          name: 'P5',
          type: 'line',
          data: p5,
          lineStyle: { width: 0 },
          areaStyle: { color: 'rgba(15,23,42,0.85)' },
          stack: 'fan',
          symbol: 'none',
        },
        {
          name: 'Median',
          type: 'line',
          data: p50,
          lineStyle: { color: '#60a5fa', width: isLab ? 2 : 1.5 },
          symbol: 'none',
          z: 3,
        },
      ],
    });

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el);

    return () => {
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, [fan, startingEquity, className]);

  if (!fan?.length) return null;

  return (
    <section className={cn('backtest-mc-chart', className)}>
      <p className="algo-backtest-table-scroll__caption mb-1">
        Monte Carlo fan ({monteCarlo.simulations} sims · {monteCarlo.trade_count} trades)
      </p>
      <div ref={containerRef} className="backtest-mc-chart__canvas" />
      <p className="text-[0.55rem] text-muted-foreground m-0 mt-1">
        Shaded cone: 5th–95th percentile cumulative PnL as trades accumulate.
      </p>
    </section>
  );
}
