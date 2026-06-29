/**
 * EquityCurveTab.jsx
 * Cumulative P&L equity curve + drawdown visualization using Apache ECharts.
 */
import React, { useEffect, useRef, useMemo, useState, useCallback } from 'react';
import * as echarts from 'echarts';
import { useStore } from '../store/useStore';
import { cn } from '@/lib/utils';
import { TrendingUp, TrendingDown, BarChart2, Target, Activity } from 'lucide-react';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { WidgetEmpty } from './WidgetShell';
import { StatCard } from './StatCard';

const pad = (n) => String(n).padStart(2, '0');

const fmt = (n, d = 2) =>
  n == null ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

const PERIODS = [
  { label: '1D', days: 1 },
  { label: '1W', days: 7 },
  { label: '1M', days: 30 },
  { label: 'ALL', days: Infinity },
];

export default function EquityCurveTab() {
  const tradeHistory = useStore((state) => state.tradeHistory);
  const chartRef = useRef(null);
  const chartInst = useRef(null);
  const [period, setPeriod] = useState('ALL');

  const { equitySeries, filteredStats } = useMemo(() => {
    const now = Date.now();
    const selectedPeriod = PERIODS.find(p => p.label === period);
    const cutoff = selectedPeriod?.days === Infinity ? 0 : now - selectedPeriod.days * 86400000;

    const filledSells = tradeHistory
      .filter(t => t.status === 'FILLED' && t.side === 'SELL' && t.realized_pnl != null && t.timestamp >= cutoff)
      .sort((a, b) => a.timestamp - b.timestamp);

    let cumPnl = 0;
    let peak = 0;
    let maxDrawdown = 0;
    let wins = 0;
    let losses = 0;

    const eqSeries = [];

    filledSells.forEach(t => {
      cumPnl += t.realized_pnl;
      if (cumPnl > peak) peak = cumPnl;
      const dd = peak > 0 ? ((peak - cumPnl) / peak) * 100 : 0;
      if (dd > maxDrawdown) maxDrawdown = dd;
      if (t.realized_pnl > 0) wins++;
      else losses++;

      const tsec = Math.floor(t.timestamp / 1000);
      eqSeries.push({ time: tsec, value: parseFloat(cumPnl.toFixed(2)) });
    });

    const totalPnl = filledSells.reduce((s, t) => s + t.realized_pnl, 0);
    const winRate = wins + losses > 0 ? (wins / (wins + losses)) * 100 : 0;
    const gross = filledSells.reduce((s, t) => s + (t.trade_value || 0), 0);

    return {
      equitySeries: eqSeries,
      filteredStats: { totalPnl, maxDrawdown, winRate, wins, losses, gross, count: filledSells.length },
    };
  }, [tradeHistory, period]);

  const configureChart = useCallback(() => {
    if (!chartInst.current || equitySeries.length === 0) return;

    const categoryData = equitySeries.map(s => {
      const d = new Date(s.time * 1000);
      return `${d.toLocaleDateString()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    });

    const lineData = equitySeries.map(s => s.value);
    const isProfit = filteredStats.totalPnl >= 0;
    const lineColor = isProfit ? '#10b981' : '#ef4444';
    const areaColor = isProfit ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)';

    // Compute drawdown % from equity series
    let peak = lineData[0] ?? 0;
    const ddData = lineData.map((v) => {
      if (v > peak) peak = v;
      if (peak <= 0) return 0;
      return -Math.max(0, ((peak - v) / peak) * 100);
    });

    const option = {
      backgroundColor: 'transparent',
      grid: [
        { left: '2%', right: '6%', top: '10%', bottom: '32%' },
        { left: '2%', right: '6%', top: '74%', bottom: '6%' },
      ],
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', label: { backgroundColor: '#1e3a8a' } },
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      legend: {
        data: ['Equity P&L', 'Drawdown %'],
        textStyle: { color: '#9ca3af', fontSize: 10 },
      },
      xAxis: [
        {
          type: 'category',
          data: categoryData,
          axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
          splitLine: { show: true, lineStyle: { color: 'rgba(255,255,255,0.02)' } },
          axisLabel: { color: '#6b7280', fontSize: 9 },
          gridIndex: 0,
        },
        {
          type: 'category',
          data: categoryData,
          axisLabel: { show: false },
          axisTick: { show: false },
          axisLine: { show: false },
          gridIndex: 1,
        },
      ],
      yAxis: [
        {
          type: 'value',
          position: 'right',
          splitLine: { show: true, lineStyle: { color: 'rgba(255,255,255,0.02)' } },
          axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
          axisLabel: { color: '#6b7280', fontSize: 9, formatter: val => `$${val}` },
          gridIndex: 0,
        },
        {
          type: 'value',
          position: 'right',
          axisLabel: { color: '#6b7280', fontSize: 8, formatter: (v) => `${v}%` },
          splitLine: { lineStyle: { color: 'rgba(255,255,255,0.03)' } },
          gridIndex: 1,
          max: 0,
        },
      ],
      dataZoom: [{ type: 'inside', xAxisIndex: [0, 1] }],
      series: [
        {
          name: 'Equity P&L',
          type: 'line',
          data: lineData,
          showSymbol: false,
          xAxisIndex: 0,
          yAxisIndex: 0,
          lineStyle: { color: lineColor, width: 2 },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: areaColor },
              { offset: 1, color: 'transparent' },
            ]),
          },
        },
        {
          name: 'Drawdown %',
          type: 'line',
          data: ddData,
          showSymbol: false,
          xAxisIndex: 1,
          yAxisIndex: 1,
          lineStyle: { color: '#ef4444', width: 1 },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(239,68,68,0.25)' },
              { offset: 1, color: 'rgba(239,68,68,0.03)' },
            ]),
          },
        },
      ],
    };

    chartInst.current.setOption(option, { notMerge: true });
  }, [equitySeries, filteredStats.totalPnl]);

  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;

    let chart = null;
    let disposed = false;

    const mountChart = () => {
      if (disposed || chart) return false;
      const { clientWidth, clientHeight } = el;
      if (clientWidth < 2 || clientHeight < 2) return false;

      chart = echarts.init(el, 'dark');
      chartInst.current = chart;
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
      chartInst.current = null;
    };
  }, []);

  useEffect(() => {
    configureChart();
  }, [configureChart]);

  const isPos = filteredStats.totalPnl >= 0;

  if (tradeHistory.length === 0) {
    return (
      <div className="dock-panel-tab">
        <header className="dock-panel-tab__toolbar">
          <div className="dock-panel-tab__toolbar-lead">
            <div className="dock-panel-tab__toolbar-icon" aria-hidden>
              <TrendingUp size={14} />
            </div>
            <div className="dock-panel-tab__toolbar-copy">
              <span className="dock-panel-tab__toolbar-title">Equity Curve</span>
              <span className="dock-panel-tab__toolbar-subtitle">Realized P&L over time</span>
            </div>
          </div>
        </header>
        <div className="dock-panel-tab__empty">
          <WidgetEmpty
            icon={TrendingUp}
            message="No trade data yet — place trades to see your equity curve"
            className="gap-3"
          />
        </div>
      </div>
    );
  }

  return (
    <div className="dock-panel-tab">
      <header className="dock-panel-tab__toolbar">
        <div className="dock-panel-tab__toolbar-lead">
          <div className="dock-panel-tab__toolbar-icon" aria-hidden>
            <TrendingUp size={14} />
          </div>
          <div className="dock-panel-tab__toolbar-copy">
            <span className="dock-panel-tab__toolbar-title">Equity Curve</span>
            <span className="dock-panel-tab__toolbar-subtitle num-mono">
              {filteredStats.count} closed trade{filteredStats.count === 1 ? '' : 's'} · {period}
            </span>
          </div>
        </div>
        <div className="dock-panel-tab__toolbar-meta">
          <span className="dock-panel-tab__meta-label">Total P&L</span>
          <span
            className={cn(
              'dock-panel-tab__meta-value num-mono',
              isPos ? 'dock-panel-tab__meta-value--up' : 'dock-panel-tab__meta-value--down',
            )}
          >
            {isPos ? '+' : ''}${fmt(filteredStats.totalPnl)}
          </span>
        </div>
      </header>

      <div className="dock-panel-tab__stats-row scroll-fade-x">
        <StatCard
          label="Total P&L"
          icon={isPos ? TrendingUp : TrendingDown}
          value={`${isPos ? '+' : ''}$${fmt(filteredStats.totalPnl)}`}
          tone={filteredStats.totalPnl > 0 ? 'up' : filteredStats.totalPnl < 0 ? 'down' : 'neutral'}
        />
        <StatCard
          label="Win Rate"
          icon={Target}
          value={`${fmt(filteredStats.winRate, 1)}%`}
          tone={filteredStats.winRate >= 50 ? 'up' : filteredStats.winRate < 40 ? 'down' : 'neutral'}
        />
        <StatCard
          label="Max Drawdown"
          icon={TrendingDown}
          value={`${fmt(filteredStats.maxDrawdown, 1)}%`}
          tone={filteredStats.maxDrawdown > 10 ? 'down' : filteredStats.maxDrawdown <= 5 ? 'up' : 'neutral'}
        />
        <StatCard
          label="Total Trades"
          icon={Activity}
          value={filteredStats.count}
          tone="accent"
        />
        <StatCard
          label="Gross Volume"
          icon={BarChart2}
          value={`$${fmt(filteredStats.gross)}`}
          tone="neutral"
        />

        <ToggleGroup
          type="single"
          size="sm"
          spacing={1}
          value={period}
          onValueChange={v => v && setPeriod(v)}
          className="ml-auto shrink-0 self-center"
        >
          {PERIODS.map(p => (
            <ToggleGroupItem key={p.label} value={p.label} className="px-2 text-[0.62rem] font-semibold">
              {p.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>

      {equitySeries.length === 0 ? (
        <div className="dock-panel-tab__empty">
          <WidgetEmpty message="No closed trades in this period" />
        </div>
      ) : (
        <>
          <div ref={chartRef} className="dock-panel-tab__chart-wrap" />
          <footer className="dock-panel-tab__footer">
            <span>
              {filteredStats.wins}W / {filteredStats.losses}L · {fmt(filteredStats.winRate, 1)}% win rate
            </span>
            <span className="dock-panel-tab__footer-highlight">
              Max drawdown:{' '}
              <span className="num-mono font-bold text-trading-down">
                {fmt(filteredStats.maxDrawdown, 1)}%
              </span>
            </span>
          </footer>
        </>
      )}
    </div>
  );
}
