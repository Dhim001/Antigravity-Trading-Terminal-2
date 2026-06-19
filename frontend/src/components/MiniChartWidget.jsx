/**
 * MiniChartWidget.jsx — Compact ECharts panel for multi-chart grid.
 */
import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import * as echarts from 'echarts';
import { useStore } from '../store/useStore';
import { useSettingsStore } from '../store/useSettingsStore';
import { getChartEchartsTheme } from '../settings/applySettings';
import { calcEMA } from '../utils/indicators';
import { cn } from '@/lib/utils';
import { getCandles } from '../services/candleBuffer';
import { Maximize2, Minimize2, Link2 } from 'lucide-react';
import { cycleLinkGroup, LINK_GROUP_COLORS } from '../lib/chartLinkGroups';
import { Button } from '@/components/ui/button';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';

const SYMBOL_COLORS = {
  BTCUSDT: '#f59e0b',
  ETHUSDT: '#8b5cf6',
  AAPL: '#34d399',
  TSLA: '#f87171',
  MSFT: '#06b6d4',
};

const pad = (n) => String(n).padStart(2, '0');
const MINI_FUTURE_PADDING = 8;
const MINI_VISIBLE_BARS = 24;
const LIVE_MIN_INTERVAL_MS = 250;

function formatMiniTimeLabel(timeSec) {
  const d = new Date(timeSec * 1000);
  return `${pad(d.getUTCMonth() + 1)}/${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

function buildMiniCategoryData(candles) {
  const keys = candles.map((c) => formatMiniTimeLabel(c.time));
  for (let i = 0; i < MINI_FUTURE_PADDING; i++) keys.push(`__pad_${i}__`);
  return keys;
}

function miniCategoryLabelFormatter(val) {
  if (val == null || val === '' || String(val).startsWith('__pad_')) return '';
  return val;
}

function lastRealMiniCategoryIndex(categoryData) {
  return Math.max(0, categoryData.length - MINI_FUTURE_PADDING - 1);
}

function defaultMiniDataZoom(candleCount, totalCategoryCount, visibleBars = MINI_VISIBLE_BARS) {
  const liveGap = Math.min(3, MINI_FUTURE_PADDING);
  const endPct = Math.min(100, ((candleCount + liveGap) / totalCategoryCount) * 100);
  const startPct = Math.max(0, ((candleCount - visibleBars) / totalCategoryCount) * 100);
  return { start: startPct, end: endPct };
}

function dataZoomEndIndex(dz, categoryData) {
  if (!dz || !categoryData.length) return -1;
  if (typeof dz.end === 'number') {
    return Math.round((dz.end / 100) * categoryData.length);
  }
  return -1;
}

function isDataZoomAtLiveEdge(dz, categoryData) {
  if (!dz || !categoryData.length) return true;
  const realEnd = lastRealMiniCategoryIndex(categoryData);
  const endIdx = dataZoomEndIndex(dz, categoryData);
  if (endIdx < 0) return true;
  return endIdx >= realEnd - 1;
}

function liveEdgeDataZoomForMini(candleCount, categoryData) {
  return defaultMiniDataZoom(candleCount, categoryData.length);
}

function normalizeEchartsList(value) {
  if (value == null) return [];
  return Array.isArray(value) ? value : [value];
}

function readDataZoomWindow(chart) {
  try {
    const dz = normalizeEchartsList(chart.getOption()?.dataZoom)[0];
    if (dz && typeof dz.start === 'number' && typeof dz.end === 'number') {
      return { start: dz.start, end: dz.end };
    }
  } catch (_) {}
  return null;
}

function buildMiniChartSeriesData(candles) {
  const candlestickData = candles.map((c) => [c.open, c.close, c.low, c.high]);
  const ema9 = calcEMA(candles, 9);
  const ema9Data = candles.map((_, i) => (i >= 8 ? ema9[i - 8]?.value : null));
  const ema21 = calcEMA(candles, 21);
  const ema21Data = candles.map((_, i) => (i >= 20 ? ema21[i - 20]?.value : null));

  for (let i = 0; i < MINI_FUTURE_PADDING; i++) {
    candlestickData.push('-');
    ema9Data.push(null);
    ema21Data.push(null);
  }

  return { candlestickData, ema9Data, ema21Data };
}

function MiniChartHeaderPrice({ symbol }) {
  const ticker = useStore(state => state.tickerData[symbol]);
  const direction = useStore(state => state.priceDirections[symbol]);

  if (!ticker) {
    return <span className="text-[0.7rem] text-muted-foreground">Loading…</span>;
  }

  const dec = (
    symbol.includes('XRP') || symbol.includes('ADA') || symbol.includes('DOGE') || ticker.price < 2.0
  ) ? 4 : 2;

  const priceClass =
    direction === 'up' ? 'text-trading-up'
      : direction === 'down' ? 'text-trading-down'
        : 'text-foreground';

  return (
    <div className="flex min-w-0 items-center gap-1.5 overflow-hidden text-ellipsis whitespace-nowrap text-xs">
      <span className={cn('num-mono text-[0.8rem] font-bold transition-colors', priceClass)}>
        {ticker.price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
      </span>
      <span className={cn(
        'num-mono text-[0.7rem]',
        ticker.change_24h >= 0 ? 'text-trading-up' : 'text-trading-down',
      )}>
        {ticker.change_24h >= 0 ? '+' : ''}{ticker.change_24h}%
      </span>
    </div>
  );
}

const MINI_DISPLAY_BARS = 120;

export default function MiniChartWidget({
  defaultSymbol = 'BTCUSDT',
  isFocused = false,
  onFocus,
  isMaximized = false,
  onToggleMaximize,
  linkGroup = null,
  onLinkGroupChange,
}) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const chartReadyRef = useRef(false);
  const liveRafRef = useRef(null);
  const liveLastPaintMs = useRef(0);
  const prevSymbolRef = useRef(defaultSymbol);
  const configureChartRef = useRef(() => {});
  const pinnedToLiveRef = useRef(true);
  const categoryDataRef = useRef([]);
  const candlesRef = useRef([]);
  const suppressDataZoomEventsRef = useRef(0);

  const [symbol, setSymbol] = useState(defaultSymbol);

  const setActiveSymbol = useStore(state => state.setActiveSymbol);
  const symbolsList = useStore(state => state.symbolsList);
  const settings = useSettingsStore(state => state.settings);
  const resolvedTheme = useSettingsStore(state => state.resolvedTheme);
  const chartTheme = useMemo(
    () => getChartEchartsTheme(settings, resolvedTheme),
    [settings, resolvedTheme],
  );

  const accentCol = SYMBOL_COLORS[symbol] || '#6366f1';

  const priceDecimals = useCallback((candles) => (
    symbol.includes('XRP') || symbol.includes('ADA') || symbol.includes('DOGE') ||
    candles[candles.length - 1]?.close < 2.0
  ) ? 4 : 2, [symbol]);

  const getDisplayCandles = useCallback(() => {
    const raw = getCandles(symbol);
    return raw.length > MINI_DISPLAY_BARS ? raw.slice(-MINI_DISPLAY_BARS) : raw;
  }, [symbol]);

  const buildFullOption = useCallback((candles, zoomWindow) => {
    const dec = priceDecimals(candles);
    const categoryData = buildMiniCategoryData(candles);
    const { candlestickData, ema9Data, ema21Data } = buildMiniChartSeriesData(candles);
    const zoom = zoomWindow ?? liveEdgeDataZoomForMini(candles.length, categoryData);

    return {
      animation: false,
      backgroundColor: chartTheme.backgroundColor,
      grid: { left: '2%', right: '8%', top: '6%', bottom: '18%' },
      tooltip: { show: false },
      xAxis: {
        type: 'category',
        data: categoryData,
        scale: true,
        boundaryGap: false,
        axisLine: { lineStyle: { color: chartTheme.axisLineColor } },
        splitLine: { show: true, lineStyle: { color: chartTheme.gridColor } },
        axisLabel: {
          color: chartTheme.axisLabelColor,
          fontSize: 9,
          formatter: miniCategoryLabelFormatter,
        },
      },
      yAxis: {
        scale: true,
        position: 'right',
        splitLine: { show: true, lineStyle: { color: chartTheme.gridColor } },
        axisLine: { lineStyle: { color: chartTheme.axisLineColor } },
        axisLabel: { color: chartTheme.axisLabelColor, fontSize: 9, formatter: (val) => val.toFixed(dec) },
      },
      dataZoom: [{ type: 'inside', start: zoom.start, end: zoom.end }],
      series: [
        {
          id: 'candles',
          name: symbol,
          type: 'candlestick',
          data: candlestickData,
          animation: false,
          itemStyle: {
            color: chartTheme.bullishColor,
            color0: chartTheme.bearishColor,
            borderColor: chartTheme.bullishColor,
            borderColor0: chartTheme.bearishColor,
          },
        },
        {
          id: 'ema9',
          name: 'EMA 9',
          type: 'line',
          data: ema9Data,
          showSymbol: false,
          animation: false,
          lineStyle: { color: '#f59e0b', width: 1, opacity: 0.8 },
        },
        {
          id: 'ema21',
          name: 'EMA 21',
          type: 'line',
          data: ema21Data,
          showSymbol: false,
          animation: false,
          lineStyle: { color: '#8b5cf6', width: 1, opacity: 0.8 },
        },
      ],
    };
  }, [priceDecimals, symbol, chartTheme]);

  const resolveZoomWindow = useCallback((chart, candles, resetZoom) => {
    const categoryData = buildMiniCategoryData(candles);
    if (resetZoom) {
      pinnedToLiveRef.current = true;
      return liveEdgeDataZoomForMini(candles.length, categoryData);
    }

    const prev = readDataZoomWindow(chart);
    const prevCategory = categoryDataRef.current;
    if (!prev || !prevCategory.length) {
      pinnedToLiveRef.current = true;
      return liveEdgeDataZoomForMini(candles.length, categoryData);
    }

    if (isDataZoomAtLiveEdge(prev, prevCategory)) {
      pinnedToLiveRef.current = true;
      return liveEdgeDataZoomForMini(candles.length, categoryData);
    }

    pinnedToLiveRef.current = false;
    return prev;
  }, []);

  const configureChart = useCallback((opts = {}) => {
    const chart = chartRef.current;
    const candles = getDisplayCandles();
    if (!chart || candles.length === 0) return;

    const resetZoom = opts.resetZoom === true;
    const zoomWindow = resolveZoomWindow(chart, candles, resetZoom);
    const categoryData = buildMiniCategoryData(candles);

    chart.setOption(buildFullOption(candles, zoomWindow), { notMerge: true });
    categoryDataRef.current = categoryData;
    candlesRef.current = candles;
    chartReadyRef.current = true;
  }, [buildFullOption, getDisplayCandles, resolveZoomWindow]);

  const barMatches = (a, b) => (
    a.open === b.open && a.high === b.high && a.low === b.low && a.close === b.close
  );

  const applyLiveUpdate = useCallback(() => {
    const chart = chartRef.current;
    if (!chart || !chartReadyRef.current) return;

    const candles = getDisplayCandles();
    if (!candles.length) return;

    const prev = candlesRef.current;
    const last = candles[candles.length - 1];
    const prevLast = prev[prev.length - 1];
    let isNewBar = false;

    if (!prev.length || !last) {
      configureChart();
      return;
    }

    if (last.time === prevLast?.time) {
      if (barMatches(last, prevLast)) return;
    } else if (last.time > (prevLast?.time ?? 0)) {
      isNewBar = true;
    } else {
      return;
    }

    candlesRef.current = candles;
    const { candlestickData, ema9Data, ema21Data } = buildMiniChartSeriesData(candles);

    try {
      const patch = {
        series: [
          { id: 'candles', data: candlestickData },
          { id: 'ema9', data: ema9Data },
          { id: 'ema21', data: ema21Data },
        ],
      };

      if (isNewBar) {
        const categoryData = buildMiniCategoryData(candles);
        patch.xAxis = { data: categoryData };
        categoryDataRef.current = categoryData;
        if (pinnedToLiveRef.current) {
          const zoom = liveEdgeDataZoomForMini(candles.length, categoryData);
          patch.dataZoom = [{ type: 'inside', start: zoom.start, end: zoom.end }];
          suppressDataZoomEventsRef.current += 1;
        }
      }

      chart.setOption(patch, { lazyUpdate: true });

      if (isNewBar && suppressDataZoomEventsRef.current > 0) {
        requestAnimationFrame(() => {
          suppressDataZoomEventsRef.current = Math.max(0, suppressDataZoomEventsRef.current - 1);
        });
      }
    } catch (err) {
      console.warn('[MiniChartWidget] live update failed:', err);
    }
  }, [getDisplayCandles, configureChart]);

  configureChartRef.current = configureChart;

  const pumpLiveUpdate = useCallback(() => {
    const now = performance.now();
    if (now - liveLastPaintMs.current < LIVE_MIN_INTERVAL_MS) {
      if (liveRafRef.current == null) {
        liveRafRef.current = requestAnimationFrame(() => {
          liveRafRef.current = null;
          pumpLiveUpdate();
        });
      }
      return;
    }
    liveLastPaintMs.current = now;
    applyLiveUpdate();
  }, [applyLiveUpdate]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    let chart = null;
    let disposed = false;

    const mountChart = () => {
      if (disposed || chart) return false;
      const { clientWidth, clientHeight } = el;
      if (clientWidth < 2 || clientHeight < 2) return false;

      chart = echarts.init(el, chartTheme.echartsTheme || undefined);
      chartRef.current = chart;

      chart.on('datazoom', (ev) => {
        if (suppressDataZoomEventsRef.current > 0) return;
        const batch = ev.batch?.[0] ?? ev;
        if (typeof batch.start !== 'number' || typeof batch.end !== 'number') return;
        pinnedToLiveRef.current = isDataZoomAtLiveEdge(
          { start: batch.start, end: batch.end },
          categoryDataRef.current,
        );
      });

      requestAnimationFrame(() => configureChartRef.current({ resetZoom: true }));
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
      chartReadyRef.current = false;
    };
  }, [chartTheme.echartsTheme, resolvedTheme]);

  useEffect(() => {
    if (!chartRef.current) return;
    if (prevSymbolRef.current === symbol) return;
    prevSymbolRef.current = symbol;
    pinnedToLiveRef.current = true;
    candlesRef.current = [];
    categoryDataRef.current = [];
    configureChart({ resetZoom: true });
  }, [symbol, configureChart]);

  useEffect(() => {
    if (!chartRef.current || !chartReadyRef.current) return;
    configureChart();
  }, [chartTheme, configureChart]);

  useEffect(() => {
    const sym = symbol;
    const unsubscribe = useStore.subscribe(
      (state) => state.candleRevision[sym] || 0,
      () => {
        if (liveRafRef.current != null) return;
        liveRafRef.current = requestAnimationFrame(() => {
          liveRafRef.current = null;
          pumpLiveUpdate();
        });
      },
    );
    return () => {
      unsubscribe();
      if (liveRafRef.current != null) {
        cancelAnimationFrame(liveRafRef.current);
        liveRafRef.current = null;
      }
    };
  }, [symbol, pumpLiveUpdate]);

  useEffect(() => {
    const sym = symbol;
    const unsubscribe = useStore.subscribe(
      (state) => state.candleHistoryRevision[sym] || 0,
      () => {
        if (!chartRef.current) return;
        configureChart({ resetZoom: false });
      },
    );
    return unsubscribe;
  }, [symbol, configureChart]);

  useEffect(() => {
    setSymbol(defaultSymbol);
  }, [defaultSymbol]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    requestAnimationFrame(() => chart.resize());
  }, [isMaximized]);

  const handleFocusClick = () => {
    setActiveSymbol(symbol);
    if (onFocus) onFocus(symbol);
  };

  const handleSymbolChange = (next) => {
    setSymbol(next);
    setActiveSymbol(next);
    if (onFocus) onFocus(next);
  };

  return (
    <div
      className={cn(
        'relative flex h-full w-full cursor-pointer flex-col overflow-hidden rounded-md bg-card transition-all duration-200',
        isFocused ? 'border-[1.5px] shadow-[0_0_12px]' : 'border border-border',
        isMaximized && 'shadow-2xl',
      )}
      style={{
        borderColor: isFocused ? accentCol : undefined,
        boxShadow: isFocused && !isMaximized ? `0 0 12px ${accentCol}30` : undefined,
      }}
      onClick={handleFocusClick}
    >
      <div
        className="mini-chart-header relative z-20 select-none"
        onClick={e => e.stopPropagation()}
        onPointerDown={e => e.stopPropagation()}
        onDoubleClick={(e) => {
          e.stopPropagation();
          onToggleMaximize?.();
        }}
      >
        <Select value={symbol} onValueChange={handleSymbolChange}>
          <SelectTrigger
            size="sm"
            className="h-7 w-auto gap-1.5 border-0 bg-transparent px-1 shadow-none focus-visible:ring-0"
            style={{ color: accentCol }}
          >
            <span
              className="size-2 shrink-0 rounded-full"
              style={{ background: accentCol, boxShadow: `0 0 6px ${accentCol}` }}
            />
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {symbolsList.map(s => (
              <SelectItem key={s} value={s} className="text-xs">
                <span className="flex items-center gap-2">
                  <span
                    className="size-1.5 shrink-0 rounded-full"
                    style={{ background: SYMBOL_COLORS[s] || '#6366f1' }}
                  />
                  {s}
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className={cn(
            'mini-chart-link-group h-6 w-6 shrink-0',
            !linkGroup && 'text-muted-foreground/60',
          )}
          style={linkGroup ? { color: LINK_GROUP_COLORS[linkGroup] } : undefined}
          title={linkGroup ? `Link group ${linkGroup} — click to change` : 'Unlinked — click to assign group A'}
          onClick={(e) => {
            e.stopPropagation();
            onLinkGroupChange?.(cycleLinkGroup(linkGroup));
          }}
        >
          {linkGroup ? (
            <span className="text-[0.62rem] font-bold leading-none">{linkGroup}</span>
          ) : (
            <Link2 className="size-3 opacity-50" aria-hidden />
          )}
        </Button>

        <MiniChartHeaderPrice symbol={symbol} />

        <Button
          variant="ghost"
          size="icon-xs"
          className="relative z-20 shrink-0 text-muted-foreground hover:text-foreground"
          onPointerDown={(e) => {
            e.preventDefault();
            e.stopPropagation();
          }}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onToggleMaximize?.();
          }}
          title={isMaximized ? 'Restore grid layout' : 'Maximize chart'}
        >
          {isMaximized ? <Minimize2 /> : <Maximize2 />}
        </Button>
      </div>

      <div ref={containerRef} className="min-h-0 flex-1" />
    </div>
  );
}
