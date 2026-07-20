/**
 * Sub-minute tick archive viewer (GET_MARKET_TICKS).
 */
import React, { useEffect, useRef, useCallback, useState } from 'react';
import * as echarts from 'echarts';
import { initEcharts } from '@/lib/echartsInit';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { Button } from '@/components/ui/button';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { WidgetEmpty } from './WidgetShell';
import { RefreshCw, Zap } from 'lucide-react';
import { cn } from '@/lib/utils';

const RANGES = [
  { label: '15m', ms: 15 * 60_000 },
  { label: '1h', ms: 60 * 60_000 },
  { label: '4h', ms: 4 * 60 * 60_000 },
];

const EMPTY_TICKS = Object.freeze([]);

export default function TickViewerTab() {
  const activeSymbol = useStore(s => s.activeSymbol);
  const archiveTicksEnabled = useStore(s => s.archiveTicksEnabled);
  const ticks = useStore((s) => {
    const sym = s.activeSymbol;
    if (!sym) return EMPTY_TICKS;
    return s.tickData?.[sym] ?? EMPTY_TICKS;
  });
  const tickMeta = useStore(s => s.tickMeta);
  const chartRef = useRef(null);
  const instRef = useRef(null);
  const [rangeLabel, setRangeLabel] = useState('1h');
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

  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;

    let chart = null;
    let disposed = false;

    const mount = () => {
      if (disposed || chart) return false;
      const { clientWidth, clientHeight } = el;
      if (clientWidth < 2 || clientHeight < 2) return false;
      chart = initEcharts(el, 'dark');
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

  const priceDelta = ticks.length >= 2
    ? ticks[ticks.length - 1].price - ticks[0].price
    : 0;
  const deltaPositive = priceDelta >= 0;

  if (!archiveTicksEnabled) {
    return (
      <div className="dock-panel-tab">
        <header className="dock-panel-tab__toolbar">
          <div className="dock-panel-tab__toolbar-lead">
            <div className="dock-panel-tab__toolbar-icon" aria-hidden>
              <Zap size={14} />
            </div>
            <div className="dock-panel-tab__toolbar-copy">
              <span className="dock-panel-tab__toolbar-title">Tick Archive</span>
              <span className="dock-panel-tab__toolbar-subtitle">Sub-minute price snapshots</span>
            </div>
          </div>
        </header>
        <div className="dock-panel-tab__empty">
          <WidgetEmpty
            icon={Zap}
            title="Tick archive disabled"
            description="Set ARCHIVE_TICKS_ENABLED=true on the server to capture sub-minute price snapshots."
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
            <Zap size={14} />
          </div>
          <div className="dock-panel-tab__toolbar-copy">
            <span className="dock-panel-tab__toolbar-title">Tick Archive</span>
            <span className="dock-panel-tab__toolbar-subtitle num-mono">
              {activeSymbol} · {rangeLabel} window
            </span>
          </div>
        </div>
        <div className="dock-panel-tab__toolbar-actions">
          {ticks.length > 0 && (
            <div className="dock-panel-tab__toolbar-meta">
              <span className="dock-panel-tab__meta-label">Range Δ</span>
              <span
                className={cn(
                  'dock-panel-tab__meta-value num-mono',
                  deltaPositive ? 'dock-panel-tab__meta-value--up' : 'dock-panel-tab__meta-value--down',
                )}
              >
                {deltaPositive ? '+' : ''}{priceDelta.toFixed(4)}
              </span>
            </div>
          )}
          <ToggleGroup
            type="single"
            size="sm"
            spacing={1}
            value={rangeLabel}
            onValueChange={(v) => {
              if (!v) return;
              const r = RANGES.find(x => x.label === v);
              if (!r) return;
              setRangeLabel(v);
              rangeRef.current = r.ms;
              fetchTicks(r.ms);
            }}
            className="shrink-0"
          >
            {RANGES.map(r => (
              <ToggleGroupItem key={r.label} value={r.label} className="px-2 text-[0.62rem] font-semibold">
                {r.label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          <Button variant="outline" size="sm" className="h-7 text-xs" onClick={() => fetchTicks()} title="Refresh">
            <RefreshCw data-icon="inline-start" aria-hidden />
            Refresh
          </Button>
        </div>
      </header>

      {ticks.length === 0 ? (
        <div className="dock-panel-tab__empty">
          <WidgetEmpty
            icon={Zap}
            title="No ticks yet"
            description="Ticks accumulate while the server runs with ARCHIVE_TICKS_ENABLED. Try again shortly."
          />
        </div>
      ) : (
        <>
          <div ref={chartRef} className="dock-panel-tab__chart-wrap" aria-label="Tick price chart" />
          <footer className="dock-panel-tab__footer">
            <span>
              {tickMeta?.count != null
                ? `${tickMeta.count.toLocaleString()} ticks loaded`
                : `${ticks.length.toLocaleString()} ticks displayed`}
            </span>
            <span className="dock-panel-tab__footer-highlight">
              Last:{' '}
              <span className="num-mono font-bold">
                ${Number(ticks[ticks.length - 1]?.price ?? 0).toFixed(4)}
              </span>
            </span>
          </footer>
        </>
      )}
    </div>
  );
}
