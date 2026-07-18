/**
 * Horizontal bar chart — per-symbol PnL contribution for portfolio backtests.
 */
import { useEffect, useMemo, useRef } from 'react';
import * as echarts from 'echarts';
import { cn } from '@/lib/utils';

function shortSymbol(sym) {
  const s = String(sym || '');
  if (s.endsWith('USDT') && s.length > 4) return s.slice(0, -4);
  return s;
}

export default function PortfolioContributionChart({ rows, className }) {
  const containerRef = useRef(null);

  const data = useMemo(() => (
    (rows || [])
      .filter((r) => r && !r.error)
      .map((r) => ({
        symbol: r.symbol,
        label: shortSymbol(r.symbol),
        pnl: Number(r.total_pnl) || 0,
        share: r.pnl_contribution_pct,
      }))
      .sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl))
  ), [rows]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || data.length < 1) return undefined;

    let chart = null;
    let disposed = false;

    const mount = () => {
      if (disposed || chart) return false;
      if (el.clientWidth < 2 || el.clientHeight < 2) return false;
      chart = echarts.init(el, 'dark');
      return true;
    };

    const paint = () => {
      if (!chart && !mount()) return;
      if (!chart) return;
      const labels = data.map((d) => d.label);
      const values = data.map((d) => d.pnl);
      chart.setOption({
        backgroundColor: 'transparent',
        animation: false,
        grid: { left: 52, right: 48, top: 8, bottom: 8, containLabel: false },
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'shadow' },
          backgroundColor: 'rgba(15, 23, 42, 0.94)',
          borderColor: 'rgba(148, 163, 184, 0.25)',
          textStyle: { color: '#e2e8f0', fontSize: 11 },
          formatter: (params) => {
            const p = Array.isArray(params) ? params[0] : params;
            const row = data[p.dataIndex];
            if (!row) return '';
            const share = row.share != null ? ` · ${Number(row.share).toFixed(0)}% of |PnL|` : '';
            return `<strong>${row.symbol}</strong><br/>PnL $${Number(row.pnl).toFixed(2)}${share}`;
          },
        },
        xAxis: {
          type: 'value',
          axisLabel: { color: '#94a3b8', fontSize: 10 },
          splitLine: { lineStyle: { color: 'rgba(148,163,184,0.12)' } },
          axisLine: { show: false },
        },
        yAxis: {
          type: 'category',
          data: labels,
          inverse: true,
          axisLabel: { color: '#cbd5e1', fontSize: 10 },
          axisTick: { show: false },
          axisLine: { show: false },
        },
        series: [{
          type: 'bar',
          data: values.map((v) => ({
            value: v,
            itemStyle: {
              color: v >= 0 ? 'rgba(34, 197, 94, 0.75)' : 'rgba(239, 68, 68, 0.75)',
              borderRadius: [0, 2, 2, 0],
            },
          })),
          barMaxWidth: 14,
          label: {
            show: true,
            position: 'right',
            color: '#94a3b8',
            fontSize: 9,
            formatter: (p) => {
              const v = Number(p.value);
              return `${v >= 0 ? '+' : ''}${v.toFixed(0)}`;
            },
          },
        }],
      }, true);
    };

    const ro = new ResizeObserver(() => {
      if (chart) {
        chart.resize();
        return;
      }
      paint();
    });
    ro.observe(el);
    paint();

    return () => {
      disposed = true;
      ro.disconnect();
      if (chart) chart.dispose();
    };
  }, [data]);

  if (data.length < 1) return null;

  const height = Math.min(220, Math.max(88, data.length * 28 + 16));

  return (
    <div className={cn('portfolio-bt-contrib', className)}>
      <p className="algo-backtest-table-scroll__caption mb-1">PnL contribution by symbol</p>
      <div
        ref={containerRef}
        className="portfolio-bt-contrib__chart"
        style={{ height }}
        role="img"
        aria-label="Portfolio PnL contribution by symbol"
      />
    </div>
  );
}
