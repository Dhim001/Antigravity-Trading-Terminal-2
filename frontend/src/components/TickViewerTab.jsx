/**
 * Sub-minute tick archive viewer (GET_MARKET_TICKS).
 */
import React, { useEffect, useRef, useCallback } from 'react';
import * as echarts from 'echarts';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { Button } from '@/components/ui/button';
import { WidgetEmpty } from './WidgetShell';
import { RefreshCw, Zap } from 'lucide-react';

const RANGES = [
  { label: '15m', ms: 15 * 60_000 },
  { label: '1h', ms: 60 * 60_000 },
  { label: '4h', ms: 4 * 60 * 60_000 },
];

export default function TickViewerTab() {
  const activeSymbol = useStore(s => s.activeSymbol);
  const archiveTicksEnabled = useStore(s => s.archiveTicksEnabled);
  const tickData = useStore(s => s.tickData);
  const tickMeta = useStore(s => s.tickMeta);
  const chartRef = useRef(null);
  const instRef = useRef(null);
  const rangeRef = useRef(RANGES[1].ms);

  const fetchTicks = useCallback((rangeMs = rangeRef.current) => {
    if (!archiveTicksEnabled || !activeSymbol) return;
    const now = Date.now();
    sendAction(Action.GET_MARKET_TICKS, {
      symbol: activeSymbol,
      from: now - rangeMs,
      to: now,
    });
  }, [activeSymbol, archiveTicksEnabled]);

  useEffect(() => {
    fetchTicks();
  }, [fetchTicks]);

  const ticks = tickData?.[activeSymbol] ?? [];

  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;

    let chart = null;
    let disposed = false;

    const mount = () => {
      if (disposed || chart) return false;
      const { clientWidth, clientHeight } = el;
      if (clientWidth < 2 || clientHeight < 2) return false;
      chart = echarts.init(el, 'dark');
      instRef.current = chart;
      return true;
    };

    const ro = new ResizeObserver(() => {
      if (chart) chart.resize();
      else mount();
    });
    ro.observe(el);
    mount();

    return () => {
      disposed = true;
      ro.disconnect();
      chart?.dispose();
      instRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = instRef.current;
    if (!chart) return;

    if (!ticks.length) {
      chart.clear();
      return;
    }

    const labels = ticks.map(t => {
      const d = new Date(t.time_ms);
      return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
    });
    const prices = ticks.map(t => t.price);
    const first = prices[0];
    const last = prices[prices.length - 1];
    const up = last >= first;
    const color = up ? '#10b981' : '#ef4444';

    chart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 8, right: 12, top: 16, bottom: 24, containLabel: true },
      xAxis: {
        type: 'category',
        data: labels,
        axisLabel: { fontSize: 8, color: '#6b7280', interval: Math.max(0, Math.floor(labels.length / 8) - 1) },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
      },
      yAxis: {
        type: 'value',
        scale: true,
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.04)' } },
        axisLabel: { fontSize: 9, color: '#9ca3af' },
      },
      dataZoom: [{ type: 'inside' }],
      series: [{
        name: 'Price',
        type: 'line',
        data: prices,
        showSymbol: false,
        lineStyle: { width: 1, color },
        sampling: 'lttb',
      }],
      tooltip: {
        trigger: 'axis',
        textStyle: { fontSize: 11 },
        formatter: (params) => {
          const idx = params[0]?.dataIndex ?? 0;
          const tick = ticks[idx];
          if (!tick) return '';
          return `${new Date(tick.time_ms).toLocaleTimeString()}<br/>$${Number(tick.price).toFixed(4)}`;
        },
      },
    }, { notMerge: true });
  }, [ticks]);

  if (!archiveTicksEnabled) {
    return (
      <WidgetEmpty
        icon={Zap}
        title="Tick archive disabled"
        description="Set ARCHIVE_TICKS_ENABLED=true on the server to capture sub-minute price snapshots."
      />
    );
  }

  return (
    <div className="tick-viewer-tab flex min-h-0 flex-1 flex-col gap-2 p-2">
      <div className="tick-viewer-tab__toolbar flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold text-foreground">{activeSymbol}</span>
        <span className="text-[0.62rem] text-muted-foreground">
          {tickMeta?.count != null ? `${tickMeta.count.toLocaleString()} ticks` : '—'}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {RANGES.map(r => (
            <Button
              key={r.label}
              variant="outline"
              size="xs"
              className="h-6 text-[0.62rem]"
              onClick={() => {
                rangeRef.current = r.ms;
                fetchTicks(r.ms);
              }}
            >
              {r.label}
            </Button>
          ))}
          <Button variant="ghost" size="icon-sm" className="h-6 w-6" onClick={() => fetchTicks()} title="Refresh">
            <RefreshCw className="size-3" />
          </Button>
        </div>
      </div>

      {ticks.length === 0 ? (
        <WidgetEmpty
          icon={Zap}
          title="No ticks yet"
          description="Ticks accumulate while the server runs with ARCHIVE_TICKS_ENABLED. Try again shortly."
        />
      ) : (
        <div ref={chartRef} className="tick-viewer-chart min-h-[140px] flex-1" aria-label="Tick price chart" />
      )}
    </div>
  );
}
