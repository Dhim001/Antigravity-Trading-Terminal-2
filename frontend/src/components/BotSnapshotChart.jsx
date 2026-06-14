/**
 * Bot equity curve from periodic snapshots (bot_detail.snapshots[]).
 */
import React, { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

function parseSnapshotTime(ts) {
  if (ts == null) return null;
  if (typeof ts === 'number') {
    const ms = ts > 1e12 ? ts : ts * 1000;
    return Math.floor(ms / 1000);
  }
  const d = new Date(String(ts).endsWith('Z') ? ts : `${ts}Z`);
  return Number.isNaN(d.getTime()) ? null : Math.floor(d.getTime() / 1000);
}

export default function BotSnapshotChart({ snapshots, allocation = 0 }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);

  const series = (snapshots ?? [])
    .map(s => {
      const time = parseSnapshotTime(s.timestamp);
      if (time == null || s.equity == null) return null;
      return { time, equity: Number(s.equity) };
    })
    .filter(Boolean)
    .sort((a, b) => a.time - b.time);

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
      if (chart) chart.resize();
      else mountChart();
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

    if (series.length < 2) {
      chart.clear();
      return;
    }

    const baseline = allocation > 0 ? allocation : series[0].equity;
    const labels = series.map(p => {
      const d = new Date(p.time * 1000);
      return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    });
    const values = series.map(p => p.equity);
    const last = values[values.length - 1];
    const up = last >= baseline;
    const color = up ? '#10b981' : '#ef4444';

    chart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 8, right: 8, top: 12, bottom: 8, containLabel: true },
      xAxis: { type: 'category', data: labels, show: false, boundaryGap: false },
      yAxis: {
        type: 'value',
        scale: true,
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
        axisLabel: { fontSize: 9, color: '#9ca3af', formatter: v => `$${v}` },
      },
      series: [{
        name: 'Bot equity',
        type: 'line',
        data: values,
        showSymbol: series.length <= 20,
        symbolSize: 4,
        smooth: true,
        lineStyle: { width: 1.5, color },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: up ? 'rgba(16,185,129,0.22)' : 'rgba(239,68,68,0.22)' },
            { offset: 1, color: 'rgba(0,0,0,0)' },
          ]),
        },
      }],
      tooltip: {
        trigger: 'axis',
        textStyle: { fontSize: 11 },
        formatter: (params) => {
          const p = params[0];
          const delta = baseline > 0 ? p.value - baseline : 0;
          const sign = delta >= 0 ? '+' : '';
          return `${p.name}<br/>Equity: $${Number(p.value).toLocaleString()}<br/>vs alloc: ${sign}$${delta.toFixed(2)}`;
        },
      },
    }, { notMerge: true });
  }, [series, allocation]);

  if (series.length < 2) {
    return (
      <p className="bot-snapshot-chart-empty text-[0.65rem] text-muted-foreground">
        Equity snapshots appear every ~5 min while the bot runs.
      </p>
    );
  }

  return (
    <div
      ref={containerRef}
      className="bot-snapshot-chart"
      aria-label="Bot equity snapshots"
    />
  );
}
