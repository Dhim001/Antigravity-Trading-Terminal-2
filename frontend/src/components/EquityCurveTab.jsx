/**
 * EquityCurveTab.jsx
 * Cumulative P&L equity curve + drawdown visualization.
 * Uses lightweight-charts LineSeries from existing trade history data.
 */
import React, { useEffect, useRef, useMemo, useState } from 'react';
import { createChart, LineSeries, AreaSeries } from 'lightweight-charts';
import { useStore } from '../store/useStore';
import { TrendingUp, TrendingDown, BarChart2, Target, Award, Activity } from 'lucide-react';

const fmt = (n, d = 2) =>
  n == null ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

const PERIODS = [
  { label: '1D', days: 1 },
  { label: '1W', days: 7 },
  { label: '1M', days: 30 },
  { label: 'ALL', days: Infinity },
];

function StatPill({ label, value, positive, negative, icon: Icon }) {
  const color = positive ? 'var(--color-up)' : negative ? 'var(--color-down)' : '#60a5fa';
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 3,
      background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)',
      borderRadius: 'var(--r-md)', padding: '8px 12px', minWidth: 100, flex: '1 1 100px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 600 }}>{label}</span>
        {Icon && <Icon size={11} style={{ color, opacity: 0.8 }} />}
      </div>
      <span className="num-mono" style={{ fontSize: 'var(--fs-lg)', fontWeight: 800, color }}>{value}</span>
    </div>
  );
}

export default function EquityCurveTab() {
  const { tradeHistory, tradeStats } = useStore();
  const chartRef = useRef(null);
  const chartInst = useRef(null);
  const [period, setPeriod] = useState('ALL');

  // Filter trades by period and build cumulative equity series
  const { equitySeries, drawdownSeries, filteredStats } = useMemo(() => {
    const now = Date.now();
    const selectedPeriod = PERIODS.find(p => p.label === period);
    const cutoff = selectedPeriod?.days === Infinity ? 0 : now - selectedPeriod.days * 86400000;

    const filledSells = tradeHistory
      .filter(t => t.status === 'FILLED' && t.side === 'SELL' && t.realized_pnl != null && t.timestamp >= cutoff)
      .sort((a, b) => a.timestamp - b.timestamp);

    let cumPnl = 0;
    let peak = 0;
    let maxDrawdown = 0;
    let wins = 0, losses = 0;

    const eqSeries = [];
    const ddSeries = [];

    filledSells.forEach(t => {
      cumPnl += t.realized_pnl;
      if (cumPnl > peak) peak = cumPnl;
      const dd = peak > 0 ? ((peak - cumPnl) / peak) * 100 : 0;
      if (dd > maxDrawdown) maxDrawdown = dd;
      if (t.realized_pnl > 0) wins++;
      else losses++;

      const tsec = Math.floor(t.timestamp / 1000);
      eqSeries.push({ time: tsec, value: parseFloat(cumPnl.toFixed(2)) });
      ddSeries.push({ time: tsec, value: parseFloat((-dd).toFixed(2)) });
    });

    const totalPnl = filledSells.reduce((s, t) => s + t.realized_pnl, 0);
    const winRate = wins + losses > 0 ? (wins / (wins + losses)) * 100 : 0;
    const gross = filledSells.reduce((s, t) => s + (t.trade_value || 0), 0);

    return {
      equitySeries: eqSeries,
      drawdownSeries: ddSeries,
      filteredStats: { totalPnl, maxDrawdown, winRate, wins, losses, gross, count: filledSells.length },
    };
  }, [tradeHistory, period]);

  // Build chart
  useEffect(() => {
    if (!chartRef.current) return;

    const chart = createChart(chartRef.current, {
      width: chartRef.current.clientWidth,
      height: chartRef.current.clientHeight || 180,
      layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#6b7280', fontFamily: 'Inter, sans-serif' },
      grid: { vertLines: { color: 'rgba(255,255,255,0.03)' }, horzLines: { color: 'rgba(255,255,255,0.03)' } },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.06)', minimumWidth: 70 },
      timeScale: { borderColor: 'rgba(255,255,255,0.06)', timeVisible: true, secondsVisible: false },
      crosshair: {
        vertLine: { color: 'rgba(59,130,246,0.4)', width: 1, style: 3, labelBackgroundColor: '#1e3a8a' },
        horzLine: { color: 'rgba(59,130,246,0.4)', width: 1, style: 3, labelBackgroundColor: '#1e3a8a' },
      },
      handleScroll: true,
      handleScale: true,
    });

    const isProfit = filteredStats.totalPnl >= 0;
    const lineColor = isProfit ? '#10b981' : '#ef4444';

    const equityLine = chart.addSeries(AreaSeries, {
      lineColor,
      topColor: isProfit ? 'rgba(16,185,129,0.25)' : 'rgba(239,68,68,0.25)',
      bottomColor: isProfit ? 'rgba(16,185,129,0.02)' : 'rgba(239,68,68,0.02)',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
    });

    if (equitySeries.length > 0) {
      equityLine.setData(equitySeries);
      chart.timeScale().fitContent();
    }

    chartInst.current = chart;

    const ro = new ResizeObserver(() => {
      if (chartRef.current) {
        chart.resize(chartRef.current.clientWidth, chartRef.current.clientHeight || 180);
      }
    });
    if (chartRef.current) ro.observe(chartRef.current);

    return () => { ro.disconnect(); try { chart.remove(); } catch (_) {} chartInst.current = null; };
  }, [equitySeries, filteredStats.totalPnl]);

  const isPos = filteredStats.totalPnl >= 0;

  if (tradeHistory.length === 0) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 12, color: 'var(--text-muted)' }}>
        <TrendingUp size={32} style={{ opacity: 0.3 }} />
        <span style={{ fontSize: 'var(--fs-base)' }}>No trade data yet</span>
        <span style={{ fontSize: 'var(--fs-xs)', opacity: 0.6 }}>Place trades to see your equity curve</span>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Stats row */}
      <div style={{ display: 'flex', gap: 6, padding: '8px 14px', flexShrink: 0, borderBottom: '1px solid rgba(255,255,255,0.05)', flexWrap: 'wrap' }}>
        <StatPill
          label="Total P&L" icon={isPos ? TrendingUp : TrendingDown}
          value={`${isPos ? '+' : ''}$${fmt(filteredStats.totalPnl)}`}
          positive={filteredStats.totalPnl > 0} negative={filteredStats.totalPnl < 0}
        />
        <StatPill
          label="Win Rate" icon={Target}
          value={`${fmt(filteredStats.winRate, 1)}%`}
          positive={filteredStats.winRate >= 50} negative={filteredStats.winRate < 40}
        />
        <StatPill
          label="Max Drawdown" icon={TrendingDown}
          value={`${fmt(filteredStats.maxDrawdown, 1)}%`}
          negative={filteredStats.maxDrawdown > 10}
          positive={filteredStats.maxDrawdown <= 5}
        />
        <StatPill
          label="Total Trades" icon={Activity}
          value={filteredStats.count}
          positive={false} negative={false}
        />
        <StatPill
          label="Gross Volume" icon={BarChart2}
          value={`$${fmt(filteredStats.gross)}`}
          positive={false} negative={false}
        />

        {/* Period selector aligned right */}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 4, alignItems: 'center', flexShrink: 0 }}>
          {PERIODS.map(p => (
            <button
              key={p.label}
              onClick={() => setPeriod(p.label)}
              className={`tf-btn${period === p.label ? ' active' : ''}`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Equity chart */}
      <div ref={chartRef} style={{ flex: 1, minHeight: 0, padding: '4px 0' }} />

      {/* Footer note */}
      {equitySeries.length === 0 && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>
          No closed trades in this period
        </div>
      )}
    </div>
  );
}
