/**
 * ChartWidget.jsx — Professional Trading Chart using Apache ECharts
 */
import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import * as echarts from 'echarts';
import { useStore } from '../store/useStore';
import {
  calcSMA, calcEMA, calcBollingerBands, calcRSI, calcMACD, calcVWAP, calcATR, generateSignal
} from '../utils/indicators';
import { AreaChart, TrendingUp, Activity } from 'lucide-react';
import { WidgetShell, WidgetToolbar, WidgetToolbarDivider } from './WidgetShell';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Popover, PopoverContent, PopoverHeader, PopoverTitle, PopoverTrigger,
} from '@/components/ui/popover';
import { cn } from '@/lib/utils';
import { getCandles } from '../services/candleBuffer';

const INDICATORS = {
  ema9:   { label: 'EMA 9',  color: '#f59e0b' },
  ema21:  { label: 'EMA 21', color: '#8b5cf6' },
  ema50:  { label: 'EMA 50', color: '#06b6d4' },
  bb:     { label: 'BB 20',  color: '#6366f1' },
  vwap:   { label: 'VWAP',   color: '#ec4899' },
  volume: { label: 'Volume', color: '#00b0ff' },
  rsi:    { label: 'RSI 14', color: '#fbbf24' },
  macd:   { label: 'MACD',   color: '#34d399' },
  atr:    { label: 'ATR 14', color: '#94a3b8' },
};

const TF_CONFIGS = [
  { label: '1m',  secs: 60    },
  { label: '5m',  secs: 300   },
  { label: '15m', secs: 900   },
  { label: '1H',  secs: 3600  },
  { label: '4H',  secs: 14400 },
  { label: '1D',  secs: 86400 },
];

/** Bars rendered on chart — full history stays in store for backtest/signals */
const CHART_DISPLAY_BARS = 600;
const FUTURE_PADDING = 15;

const pad = (n) => String(n).padStart(2, '0');

function formatTimeLabel(timeSec) {
  const d = new Date(timeSec * 1000);
  return `${pad(d.getUTCMonth() + 1)}/${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

function buildCategoryLabels(bars) {
  const labels = bars.map(c => formatTimeLabel(c.time));
  for (let i = 0; i < FUTURE_PADDING; i++) labels.push('');
  return labels;
}

function buildMainSeriesData(bars, chartType) {
  const data = chartType === 'line'
    ? bars.map(c => c.close)
    : bars.map(c => [c.open, c.close, c.low, c.high]);
  for (let i = 0; i < FUTURE_PADDING; i++) data.push('-');
  return data;
}

function buildVolumeSeriesData(bars) {
  const data = bars.map(c => volumeSeriesEntry(c));
  for (let i = 0; i < FUTURE_PADDING; i++) data.push(null);
  return data;
}

function normalizeEchartsList(value) {
  if (value == null) return [];
  return Array.isArray(value) ? value : [value];
}

function aggregateBucket(raw, cfg) {
  if (!raw.length) return null;
  const t = Math.floor(raw[raw.length - 1].time / cfg.secs) * cfg.secs;
  const bucket = cfg.secs <= 60 ? [raw[raw.length - 1]] : raw.filter(c => c.time >= t);
  if (!bucket.length) return null;
  return {
    time: t,
    open: bucket[0].open,
    high: Math.max(...bucket.map(c => c.high)),
    low: Math.min(...bucket.map(c => c.low)),
    close: bucket[bucket.length - 1].close,
    volume: bucket.reduce((sum, c) => sum + (c.volume || 0), 0),
  };
}

function volumeSeriesEntry(bar) {
  return {
    value: bar.volume || 0,
    itemStyle: {
      color: bar.close >= bar.open ? 'rgba(16,185,129,0.35)' : 'rgba(239,68,68,0.35)',
    },
  };
}

const SIGNAL_STYLES = {
  'STRONG BUY':  { bg: 'rgba(16,185,129,0.2)',  border: '#10b981', color: '#10b981', dot: '#10b981' },
  'BUY':         { bg: 'rgba(16,185,129,0.1)',  border: '#6ee7b7', color: '#6ee7b7', dot: '#6ee7b7' },
  'NEUTRAL':     { bg: 'rgba(148,163,184,0.1)', border: '#94a3b8', color: '#94a3b8', dot: '#94a3b8' },
  'SELL':        { bg: 'rgba(239,68,68,0.1)',   border: '#fca5a5', color: '#fca5a5', dot: '#fca5a5' },
  'STRONG SELL': { bg: 'rgba(239,68,68,0.2)',   border: '#ef4444', color: '#ef4444', dot: '#ef4444' },
};

function formatVol(v) {
  if (!v) return '—';
  if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B';
  if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return v.toFixed(2);
}

function getPriceDecimals(price) {
  if (!price || price <= 0) return 2;
  if (price < 0.0001)  return 8;
  if (price < 0.001)   return 6;
  if (price < 0.1)     return 5;
  if (price < 1)       return 4;
  if (price < 10)      return 3;
  return 2;
}

function buildMarkLineData(symbolPosition, dec) {
  const markLineData = [];
  if (!symbolPosition || symbolPosition.size === 0) return markLineData;

  markLineData.push({
    yAxis: symbolPosition.avg_price,
    lineStyle: { color: '#3b82f6', width: 2, type: 'solid' },
    label: {
      show: true,
      position: 'end',
      formatter: `ENTRY ${symbolPosition.size > 0 ? 'LONG' : 'SHORT'} (${Math.abs(symbolPosition.size).toFixed(4)})`,
    },
  });
  if (symbolPosition.stop_loss_price > 0) {
    markLineData.push({
      yAxis: symbolPosition.stop_loss_price,
      lineStyle: { color: '#ef4444', width: 1, type: 'dashed' },
      label: { show: true, position: 'end', formatter: `SL: ${symbolPosition.stop_loss_price.toFixed(dec)}` },
    });
  }
  if (symbolPosition.take_profit_price > 0) {
    markLineData.push({
      yAxis: symbolPosition.take_profit_price,
      lineStyle: { color: '#10b981', width: 1, type: 'dashed' },
      label: { show: true, position: 'end', formatter: `TP: ${symbolPosition.take_profit_price.toFixed(dec)}` },
    });
  }
  return markLineData;
}

function buildTradeMarkers(tradeHistory, activeSymbol, candles, categoryData) {
  return tradeHistory
    .filter(t => t.symbol === activeSymbol && t.status === 'FILLED')
    .map(t => {
      const timeVal = Math.floor(new Date(t.timestamp).getTime() / 1000);
      let categoryIndex = candles.findIndex(c => c.time >= timeVal);
      if (categoryIndex === -1) categoryIndex = candles.length - 1;

      return {
        coord: [categoryData[categoryIndex], t.average_fill_price || t.price],
        value: `${t.side} ${(t.filled_quantity ?? t.quantity)?.toFixed(4)}`,
        symbol: t.side === 'BUY' ? 'path://M0,10 L5,0 L10,10 Z' : 'path://M0,0 L5,10 L10,0 Z',
        symbolSize: 10,
        itemStyle: { color: t.side === 'BUY' ? '#10b981' : '#ef4444' },
      };
    });
}

// ─── Child Component: Header Ticker ──────────────────────────────────
function ChartHeaderPrice({ symbol }) {
  const ticker = useStore(state => state.tickerData[symbol]);
  const direction = useStore(state => state.priceDirections[symbol]);

  if (!ticker) return null;
  const dec = getPriceDecimals(ticker.price);

  return (
    <div className="flex min-w-0 items-center gap-2 overflow-hidden text-sm">
      <span className={cn(
        'num-mono shrink-0 text-lg font-extrabold transition-colors',
        direction === 'up' ? 'text-trading-up' : direction === 'down' ? 'text-trading-down' : 'text-foreground'
      )}>
        {ticker.price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
      </span>
      <span className={cn('num-mono shrink-0 font-bold', ticker.change_24h >= 0 ? 'text-trading-up' : 'text-trading-down')}>
        {ticker.change_24h >= 0 ? '+' : ''}{Number(ticker.change_24h).toFixed(2)}%
      </span>
      <span className="hidden whitespace-nowrap text-xs text-muted-foreground xl:inline">
        H:<span className="num-mono"> {ticker.high_24h?.toFixed(dec)}</span>
        {' '}L:<span className="num-mono"> {ticker.low_24h?.toFixed(dec)}</span>
        {' '}V:<span className="num-mono"> {ticker.volume_24h ? formatVol(ticker.volume_24h) : '—'}</span>
      </span>
    </div>
  );
}

// ─── Child Component: Signal Badge ───────────────────────────────────
function ChartSignalBadge({ symbol }) {
  const [signal, setSignal] = useState({ signal: 'NEUTRAL', score: 0, reasons: [] });
  const lastCandleTime = useStore(state => {
    const rev = state.candleRevision[symbol];
    if (!rev) return 0;
    const candles = getCandles(symbol);
    return candles.length > 0 ? candles[candles.length - 1].time : 0;
  });

  useEffect(() => {
    const candles = getCandles(symbol);
    if (candles.length > 0) {
      setSignal(generateSignal(candles));
    }
  }, [lastCandleTime, symbol]);

  const sigStyle = SIGNAL_STYLES[signal.signal] || SIGNAL_STYLES.NEUTRAL;
  const isStrong = signal.signal.startsWith('STRONG');

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-7 shrink-0 gap-1.5 rounded-full px-3 text-xs font-bold tracking-wide"
          style={{
            borderColor: sigStyle.border,
            color: sigStyle.color,
            backgroundColor: sigStyle.bg,
          }}
        >
          <span
            className={cn('size-1.5 rounded-full', isStrong && 'animate-pulse')}
            style={{
              background: sigStyle.dot,
              boxShadow: isStrong ? `0 0 8px ${sigStyle.dot}` : undefined,
            }}
          />
          {signal.signal}
          <span className="text-[0.62rem] opacity-70">
            ({signal.score > 0 ? '+' : ''}{signal.score})
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-60 p-3" style={{ borderColor: sigStyle.border }}>
        <PopoverHeader className="gap-1">
          <PopoverTitle className="text-[0.62rem] uppercase tracking-wide text-muted-foreground">
            Signal Analysis
          </PopoverTitle>
        </PopoverHeader>
        {signal.reasons.length === 0 ? (
          <p className="text-xs text-muted-foreground">No detailed reasons available.</p>
        ) : (
          <ul className="space-y-1 text-xs">
            {signal.reasons.map((r, i) => (
              <li key={i} className="flex gap-2" style={{ color: sigStyle.color }}>
                <span className="opacity-40">•</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}

// ─── Main Component ──────────────────────────────────────────────────
export default function ChartWidget() {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const candlesRef = useRef([]);
  const displayBarsRef = useRef([]);
  const chartLayoutRef = useRef({ xAxisCount: 1, showVolume: true });
  const liveRafRef = useRef(null);
  const configureChartRef = useRef(() => {});
  const applyOverlayPatchRef = useRef(() => {});
  const chartReadyRef = useRef(false);

  const activeSymbol = useStore(state => state.activeSymbol);
  const historyRev = useStore(state => state.candleHistoryRevision[activeSymbol] || 0);
  const candleRev = useStore(state => state.candleRevision[activeSymbol] || 0);
  const lastCandleTime = useMemo(() => {
    const candles = getCandles(activeSymbol);
    return candles.length > 0 ? candles[candles.length - 1].time : 0;
  }, [activeSymbol, candleRev]);
  const symbolPosition = useStore(state => state.positions[activeSymbol]);
  const positionOverlayKey = useStore(state => {
    const p = state.positions[activeSymbol];
    if (!p || p.size === 0) return '';
    return `${p.size}|${p.avg_price}|${p.stop_loss_price}|${p.take_profit_price}`;
  });
  const tradeOverlayKey = useStore(state => {
    let key = '';
    for (const t of state.tradeHistory) {
      if (t.symbol === activeSymbol && t.status === 'FILLED') {
        key += `${t.timestamp}:${t.side}:${t.filled_quantity ?? t.quantity}:${t.average_fill_price ?? t.price};`;
      }
    }
    return key;
  });
  const tradeHistory = useStore(state => state.tradeHistory);
  const chartInteractionMode = useStore(state => state.chartInteractionMode);
  const setChartInteractionMode = useStore(state => state.setChartInteractionMode);

  const [timeframe, setTimeframe] = useState(() => { try { return localStorage.getItem('terminal_tf') || '1m'; } catch { return '1m'; } });
  const prevConfigRef = useRef({ symbol: activeSymbol, timeframe: timeframe });
  const [chartType, setChartType] = useState('candle');
  const [active, setActive] = useState(() => {
    try {
      const s = localStorage.getItem('terminal_chart_indicators_active');
      if (s) return JSON.parse(s);
    } catch (_) {}
    return { ema9: true, ema21: true, ema50: false, bb: true, vwap: false, rsi: true, macd: true, atr: false, volume: true };
  });


  useEffect(() => { try { localStorage.setItem('terminal_tf', timeframe); } catch {} }, [timeframe]);
  useEffect(() => { try { localStorage.setItem('terminal_chart_indicators_active', JSON.stringify(active)); } catch {} }, [active]);

  const activeIndicatorKeys = useMemo(
    () => Object.entries(active).filter(([, on]) => on).map(([k]) => k),
    [active]
  );

  const handleIndicatorsChange = useCallback((vals) => {
    setActive(prev => {
      const next = { ...prev };
      for (const k of Object.keys(INDICATORS)) next[k] = vals.includes(k);
      return next;
    });
  }, []);

  // Aggregate candles based on timeframe; chart renders a rolling window only
  const aggregatedCandles = useMemo(() => {
    const raw = getCandles(activeSymbol);
    if (!raw.length) return [];

    const cfg = TF_CONFIGS.find(t => t.label === timeframe) || TF_CONFIGS[0];
    let series;
    if (cfg.secs <= 60) {
      series = raw;
    } else {
      const buckets = new Map();
      for (const c of raw) {
        const t = Math.floor(c.time / cfg.secs) * cfg.secs;
        if (!buckets.has(t)) {
          buckets.set(t, { time: t, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume || 0 });
        } else {
          const b = buckets.get(t);
          b.high = Math.max(b.high, c.high);
          b.low = Math.min(b.low, c.low);
          b.close = c.close;
          b.volume += (c.volume || 0);
        }
      }
      series = Array.from(buckets.values()).sort((a, b) => a.time - b.time);
    }
    return series.length > CHART_DISPLAY_BARS ? series.slice(-CHART_DISPLAY_BARS) : series;
  }, [timeframe, activeSymbol, historyRev]);

  useEffect(() => {
    displayBarsRef.current = aggregatedCandles.map(c => ({ ...c }));
    candlesRef.current = displayBarsRef.current;
  }, [aggregatedCandles]);

  // Direct DOM Legend update
  const updateLegendDOM = useCallback((bar) => {
    const openEl = document.getElementById('chart-legend-o');
    const highEl = document.getElementById('chart-legend-h');
    const lowEl  = document.getElementById('chart-legend-l');
    const closeEl = document.getElementById('chart-legend-c');
    const volEl   = document.getElementById('chart-legend-v');
    const pctEl   = document.getElementById('chart-legend-pct');
    if (!openEl || !bar) return;

    const isBull = bar.close >= bar.open;
    const color = isBull ? 'var(--color-up)' : 'var(--color-down)';
    const dec = getPriceDecimals(bar.close);

    openEl.textContent = bar.open.toFixed(dec);
    openEl.style.color = color;
    highEl.textContent = bar.high.toFixed(dec);
    highEl.style.color = color;
    lowEl.textContent  = bar.low.toFixed(dec);
    lowEl.style.color  = color;
    closeEl.textContent = bar.close.toFixed(dec);
    closeEl.style.color = color;
    volEl.textContent   = formatVol(bar.volume);
    
    if (bar.open > 0) {
      const pct = ((bar.close - bar.open) / bar.open) * 100;
      pctEl.textContent = `${isBull ? '+' : ''}${pct.toFixed(2)}%`;
      pctEl.style.color = color;
    } else {
      pctEl.textContent = '';
    }
  }, []);

  // Set up chart options
  const configureChart = useCallback(() => {
    if (!chartRef.current || aggregatedCandles.length === 0) {
      chartReadyRef.current = false;
      return;
    }

    const candles = aggregatedCandles;
    const dec = getPriceDecimals(candles[candles.length - 1]?.close);

    const categoryData = candles.map(c => formatTimeLabel(c.time));
    for (let i = 0; i < FUTURE_PADDING; i++) {
      categoryData.push('');
    }

    // Dynamic Zoom preservation using absolute indices
    let startVal = null;
    let endVal = null;
    const totalCount = categoryData.length;

    if (prevConfigRef.current.symbol === activeSymbol && prevConfigRef.current.timeframe === timeframe && chartRef.current) {
      try {
        const currentOption = chartRef.current.getOption();
        const dataZoomList = normalizeEchartsList(currentOption?.dataZoom);
        const xAxisList = normalizeEchartsList(currentOption?.xAxis);
        if (dataZoomList[0] && xAxisList[0]?.data) {
          const prevStart = dataZoomList[0].startValue;
          const prevEnd = dataZoomList[0].endValue;

          if (prevStart !== undefined && prevEnd !== undefined && prevStart !== null && prevEnd !== null) {
            const prevTotal = xAxisList[0].data.length;
            const diff = totalCount - prevTotal;
            if (diff > 0) {
              // If the user was looking at the end of the chart, auto-scroll to the right
              const wasAtEnd = prevEnd >= prevTotal - 2;
              if (wasAtEnd) {
                startVal = prevStart + diff;
                endVal = prevEnd + diff;
              } else {
                // Keep the view fixed at the same historical candles (indices)
                startVal = prevStart;
                endVal = prevEnd;
              }
            } else {
              startVal = prevStart;
              endVal = prevEnd;
            }
          }
        }
      } catch (err) {
        console.warn('[ChartWidget] zoom preservation failed:', err);
      }
    }

    if (startVal === null || endVal === null || startVal === undefined || endVal === undefined) {
      // Default view: show last 50 candles (including future padding)
      startVal = Math.max(0, totalCount - 50);
      endVal = totalCount - 1;
    }
    prevConfigRef.current = { symbol: activeSymbol, timeframe: timeframe };

    const candlestickData = candles.map(c => [c.open, c.close, c.low, c.high]);
    for (let i = 0; i < FUTURE_PADDING; i++) {
      candlestickData.push('-');
    }

    // ── Grid Configurations ──
    const showVol = active.volume;
    const showRsi = active.rsi;
    const showMacd = active.macd;
    const showAtr = active.atr;

    const subPanes = [];
    if (showVol) subPanes.push('volume');
    if (showRsi) subPanes.push('rsi');
    if (showMacd) subPanes.push('macd');
    if (showAtr) subPanes.push('atr');

    const totalSubPanes = subPanes.length;
    const subPaneHeightPct = 9;
    const gapPct = 3;
    const mainHeightPct = 83 - (totalSubPanes * (subPaneHeightPct + gapPct));

    const grids = [{
      left: '3%', right: '5%', top: '5%',
      height: `${mainHeightPct}%`
    }];

    let currentTop = 5 + mainHeightPct + gapPct;
    const paneGridMap = {};
    subPanes.forEach(pane => {
      grids.push({
        left: '3%', right: '5%',
        top: `${currentTop}%`,
        height: `${subPaneHeightPct}%`
      });
      paneGridMap[pane] = grids.length - 1;
      currentTop += subPaneHeightPct + gapPct;
    });

    // Axes
    const xAxes = [];
    const yAxes = [];
    
    // Main grid axis
    xAxes.push({
      id: 'x-0',
      type: 'category',
      data: categoryData,
      gridIndex: 0,
      scale: true,
      boundaryGap: false,
      axisLine: { onZero: false, lineStyle: { color: 'rgba(255,255,255,0.06)' } },
      splitLine: { show: true, lineStyle: { color: 'rgba(255,255,255,0.03)' } },
      axisLabel: { show: grids.length === 1, color: '#9ca3af' }
    });

    yAxes.push({
      scale: true,
      gridIndex: 0,
      position: 'right',
      splitLine: { show: true, lineStyle: { color: 'rgba(255,255,255,0.03)' } },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
      axisLabel: { color: '#9ca3af', formatter: val => val.toFixed(dec) }
    });

    // Sub grids axes
    const gridCount = grids.length;
    subPanes.forEach((pane, idx) => {
      const gIdx = paneGridMap[pane];
      const isLowest = gIdx === gridCount - 1;

      xAxes.push({
        id: `x-${xAxes.length}`,
        type: 'category',
        data: categoryData,
        gridIndex: gIdx,
        scale: true,
        boundaryGap: false,
        axisLine: { onZero: false, lineStyle: { color: 'rgba(255,255,255,0.06)' } },
        splitLine: { show: true, lineStyle: { color: 'rgba(255,255,255,0.03)' } },
        axisLabel: { show: isLowest, color: '#9ca3af' },
        axisTick: { show: isLowest }
      });

      let yAxisOpt = {
        scale: true,
        gridIndex: gIdx,
        position: 'right',
        splitLine: { show: true, lineStyle: { color: 'rgba(255,255,255,0.03)' } },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
        axisLabel: { color: '#9ca3af', fontSize: 9 }
      };

      if (pane === 'volume') {
        yAxisOpt.axisLabel.formatter = val => formatVol(val);
      } else if (pane === 'rsi') {
        yAxisOpt.min = 0;
        yAxisOpt.max = 100;
        yAxisOpt.interval = 30;
      }
      yAxes.push(yAxisOpt);
    });

    // Series
    const series = [];

    // Main Candlestick / Line Series
    if (chartType === 'line') {
      const lineData = candles.map(c => c.close);
      for (let i = 0; i < FUTURE_PADDING; i++) lineData.push('-');
      series.push({
        id: 'main',
        name: activeSymbol,
        type: 'line',
        data: lineData,
        xAxisIndex: 0,
        yAxisIndex: 0,
        showSymbol: false,
        lineStyle: { color: '#3b82f6', width: 2 },
      });
    } else {
      series.push({
        id: 'main',
        name: activeSymbol,
        type: 'candlestick',
        data: candlestickData,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: {
          color: '#10b981',
          color0: '#ef4444',
          borderColor: '#10b981',
          borderColor0: '#ef4444',
        },
      });
    }

    // Overlay indicators
    if (active.ema9) {
      const ema9 = calcEMA(candles, 9);
      const ema9Data = candles.map((c, i) => i >= 8 ? ema9[i - 8]?.value : null);
      series.push({
        name: 'EMA 9', type: 'line', data: ema9Data, xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: { color: '#f59e0b', width: 1, opacity: 0.85 }
      });
    }
    if (active.ema21) {
      const ema21 = calcEMA(candles, 21);
      const ema21Data = candles.map((c, i) => i >= 20 ? ema21[i - 20]?.value : null);
      series.push({
        name: 'EMA 21', type: 'line', data: ema21Data, xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: { color: '#8b5cf6', width: 1, opacity: 0.85 }
      });
    }
    if (active.ema50) {
      const ema50 = calcEMA(candles, 50);
      const ema50Data = candles.map((c, i) => i >= 49 ? ema50[i - 49]?.value : null);
      series.push({
        name: 'EMA 50', type: 'line', data: ema50Data, xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: { color: '#06b6d4', width: 1, opacity: 0.85 }
      });
    }
    if (active.bb) {
      const bb = calcBollingerBands(candles, 20, 2);
      const mapper = (bbList) => candles.map((c, i) => i >= 19 ? bbList[i - 19]?.value : null);
      series.push(
        { name: 'BB Upper', type: 'line', data: mapper(bb.upper), xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#6366f1', width: 1, type: 'dashed', opacity: 0.7 } },
        { name: 'BB Mid', type: 'line', data: mapper(bb.middle), xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: 'rgba(99,102,241,0.3)', width: 1, type: 'dotted' } },
        { name: 'BB Lower', type: 'line', data: mapper(bb.lower), xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#6366f1', width: 1, type: 'dashed', opacity: 0.7 } }
      );
    }
    if (active.vwap) {
      const vwap = calcVWAP(candles);
      const vwapData = candles.map((c, i) => vwap[i]?.value ?? null);
      series.push({
        name: 'VWAP', type: 'line', data: vwapData, xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: { color: '#ec4899', width: 1.5 }
      });
    }

    // Sub grids series
    if (showVol) {
      const gIdx = paneGridMap.volume;
      series.push({
        id: 'volume',
        name: 'Volume',
        type: 'bar',
        xAxisIndex: gIdx,
        yAxisIndex: gIdx,
        data: [
          ...candles.map(c => ({
            value: c.volume || 0,
            itemStyle: { color: c.close >= c.open ? 'rgba(16,185,129,0.35)' : 'rgba(239,68,68,0.35)' },
          })),
          ...Array(FUTURE_PADDING).fill(null),
        ],
      });
    }

    if (showRsi) {
      const gIdx = paneGridMap.rsi;
      const rsi = calcRSI(candles, 14);
      const rsiData = candles.map((c, i) => i >= 14 ? rsi[i - 14]?.value : null);
      series.push({
        name: 'RSI', type: 'line', data: rsiData, xAxisIndex: gIdx, yAxisIndex: gIdx,
        showSymbol: false, lineStyle: { color: '#fbbf24', width: 1.5 }
      });
    }

    if (showMacd) {
      const gIdx = paneGridMap.macd;
      const macd = calcMACD(candles, 12, 26, 9);
      const mapper = (mList) => candles.map((c, i) => i >= 33 ? mList[i - 33]?.value : null);

      series.push(
        { name: 'MACD', type: 'line', data: mapper(macd.macdLine), xAxisIndex: gIdx, yAxisIndex: gIdx, showSymbol: false, lineStyle: { color: '#34d399', width: 1.2 } },
        { name: 'Signal', type: 'line', data: mapper(macd.signalLine), xAxisIndex: gIdx, yAxisIndex: gIdx, showSymbol: false, lineStyle: { color: '#f87171', width: 1.2 } },
        {
          name: 'Hist',
          type: 'bar',
          xAxisIndex: gIdx,
          yAxisIndex: gIdx,
          data: candles.map((c, i) => {
            if (i < 33) return null;
            const item = macd.histogram[i - 33];
            return item ? {
              value: item.value,
              itemStyle: { color: item.value >= 0 ? 'rgba(16,185,129,0.4)' : 'rgba(239,68,68,0.4)' }
            } : null;
          })
        }
      );
    }

    if (showAtr) {
      const gIdx = paneGridMap.atr;
      const atr = calcATR(candles, 14);
      const atrData = candles.map((c, i) => i >= 14 ? atr[i - 14]?.value : null);
      series.push({
        name: 'ATR', type: 'line', data: atrData, xAxisIndex: gIdx, yAxisIndex: gIdx,
        showSymbol: false, lineStyle: { color: '#94a3b8', width: 1.5 }
      });
    }

    // Zoom and pan links
    const zoomXIndices = grids.map((_, i) => i);

    const option = {
      backgroundColor: '#080d14',
      axisPointer: {
        link: [{ xAxisIndex: 'all' }],
        label: { backgroundColor: '#1d4ed8' }
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        show: false // we use our own legend instead
      },
      grid: grids,
      xAxis: xAxes,
      yAxis: yAxes,
      dataZoom: [
        { type: 'inside', xAxisIndex: zoomXIndices, startValue: startVal, endValue: endVal },
        { type: 'slider', xAxisIndex: zoomXIndices, startValue: startVal, endValue: endVal, bottom: '3%', height: 18, borderColor: 'transparent', fillerColor: 'rgba(37,99,235,0.12)', textStyle: { color: '#9ca3af' } }
      ],
      series: series
    };

    chartRef.current.setOption(option, { notMerge: true });

    chartLayoutRef.current = { xAxisCount: xAxes.length, showVolume: showVol };
    chartReadyRef.current = true;

    // Initial legend display
    const lastBar = candles[candles.length - 1];
    updateLegendDOM(lastBar);
  }, [aggregatedCandles, activeSymbol, timeframe, active, chartType, updateLegendDOM]);

  // Lightweight overlay patch — SL/TP lines and trade markers only
  const applyOverlayPatch = useCallback(() => {
    const chart = chartRef.current;
    const bars = candlesRef.current;
    if (!chart || !bars.length) return;

    const dec = getPriceDecimals(bars[bars.length - 1]?.close);
    const categoryData = buildCategoryLabels(bars);
    const markLineData = buildMarkLineData(symbolPosition, dec);
    const tradeMarkers = buildTradeMarkers(tradeHistory, activeSymbol, bars, categoryData);

    try {
      chart.setOption({
        series: [{
          id: 'main',
          markLine: { symbol: ['none', 'none'], data: markLineData },
          markPoint: { data: tradeMarkers, label: { show: false } },
        }],
      }, { lazyUpdate: true });
    } catch (err) {
      console.warn('[ChartWidget] overlay patch failed:', err);
    }
  }, [activeSymbol, symbolPosition, tradeHistory]);

  configureChartRef.current = configureChart;
  applyOverlayPatchRef.current = applyOverlayPatch;

  // Init ECharts Instance
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = echarts.init(containerRef.current, 'dark');
    chartRef.current = chart;
    chartReadyRef.current = false;

    // Crosshair Hover / axisPointer listener to update legend DOM dynamically
    chart.on('updateAxisPointer', (event) => {
      const axesInfo = event.axesInfo;
      if (axesInfo && axesInfo[0]) {
        const dataIndex = axesInfo[0].value;
        const candles = candlesRef.current;
        if (candles && candles[dataIndex]) {
          updateLegendDOM(candles[dataIndex]);
        }
      } else {
        const candles = candlesRef.current;
        if (candles && candles.length > 0) {
          updateLegendDOM(candles[candles.length - 1]);
        }
      }
    });

    // Click coordinate to price conversion for interactive mode
    chart.getZr().on('click', (params) => {
      const mode = useStore.getState().chartInteractionMode;
      if (mode === 'normal') return;

      const pointInPixel = [params.offsetX, params.offsetY];
      if (chart.containPoint({ gridIndex: 0 }, pointInPixel)) {
        const pointInValue = chart.convertFromPixel({ gridIndex: 0 }, pointInPixel);
        const price = pointInValue[1];
        if (price !== null && price > 0) {
          window.dispatchEvent(new CustomEvent('chart-click', { detail: price }));
        }
      }
    });

    // Resize observer
    const ro = new ResizeObserver(() => {
      chart.resize();
    });
    ro.observe(containerRef.current);

    if (displayBarsRef.current.length > 0) {
      requestAnimationFrame(() => {
        configureChartRef.current();
        applyOverlayPatchRef.current();
      });
    }

    return () => {
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
      chartReadyRef.current = false;
    };
  }, [updateLegendDOM]);

  // Full rebuild when structure/data/indicators change
  useEffect(() => {
    configureChart();
  }, [configureChart]);

  // Lightweight overlay patch — trades, positions, and after full rebuild
  useEffect(() => {
    applyOverlayPatch();
  }, [applyOverlayPatch, positionOverlayKey, tradeOverlayKey]);

  // Live tick updates — patch by series/xAxis id (no getOption mutation)
  const applyLiveCandleUpdate = useCallback(() => {
    const chart = chartRef.current;
    if (!chart || !chartReadyRef.current) return;

    const cfg = TF_CONFIGS.find(t => t.label === timeframe) || TF_CONFIGS[0];
    const raw = getCandles(activeSymbol);
    const aggregatedLive = aggregateBucket(raw, cfg);
    if (!aggregatedLive) return;

    const bars = displayBarsRef.current;
    if (!bars.length) return;

    const last = bars[bars.length - 1];
    if (last && last.time === aggregatedLive.time) {
      bars[bars.length - 1] = aggregatedLive;
    } else if (!last || aggregatedLive.time > last.time) {
      bars.push({ ...aggregatedLive });
      if (bars.length > CHART_DISPLAY_BARS) bars.shift();
    } else {
      return;
    }

    candlesRef.current = bars;

    const labels = buildCategoryLabels(bars);
    const { xAxisCount, showVolume } = chartLayoutRef.current;
    const patch = {
      xAxis: Array.from({ length: xAxisCount }, (_, i) => ({
        id: `x-${i}`,
        data: labels,
      })),
      series: [{
        id: 'main',
        type: chartType === 'line' ? 'line' : 'candlestick',
        data: buildMainSeriesData(bars, chartType),
      }],
    };
    if (showVolume) {
      patch.series.push({ id: 'volume', data: buildVolumeSeriesData(bars) });
    }

    try {
      chart.setOption(patch, { lazyUpdate: false });
      updateLegendDOM(aggregatedLive);
    } catch (err) {
      console.warn('[ChartWidget] live candle update failed:', err);
    }
  }, [activeSymbol, timeframe, chartType, updateLegendDOM]);

  useEffect(() => {
    const symbol = activeSymbol;
    const unsubscribe = useStore.subscribe(
      (state) => state.candleRevision[symbol] || 0,
      () => {
        if (liveRafRef.current != null) return;
        liveRafRef.current = requestAnimationFrame(() => {
          liveRafRef.current = null;
          applyLiveCandleUpdate();
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
  }, [activeSymbol, applyLiveCandleUpdate]);

  // Handle ESC key to cancel interaction mode
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape' && chartInteractionMode !== 'normal') {
        setChartInteractionMode('normal');
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [chartInteractionMode, setChartInteractionMode]);

  // Handle Chart Click for SL/TP
  useEffect(() => {
    const handleChartClick = (e) => {
      if (chartInteractionMode === 'normal') return;
      const price = e.detail;
      
      if (chartInteractionMode === 'edit_sl') {
        import('../services/websocket').then(({ sendWebSocketAction }) => {
          sendWebSocketAction("update_position_sl_tp", { symbol: activeSymbol, stop_loss_price: price });
        });
      } else if (chartInteractionMode === 'edit_tp') {
        import('../services/websocket').then(({ sendWebSocketAction }) => {
          sendWebSocketAction("update_position_sl_tp", { symbol: activeSymbol, take_profit_price: price });
        });
      }
      
      setChartInteractionMode('normal');
    };
    
    window.addEventListener('chart-click', handleChartClick);
    return () => window.removeEventListener('chart-click', handleChartClick);
  }, [chartInteractionMode, activeSymbol, setChartInteractionMode]);

  const chartToolbar = (
    <>
      <WidgetToolbar className="h-8 flex-nowrap py-0">
        <ToggleGroup type="single" value={timeframe} onValueChange={(v) => v && setTimeframe(v)} spacing={0}>
          {TF_CONFIGS.map(tf => (
            <ToggleGroupItem key={tf.label} value={tf.label} size="sm" className="px-2 text-[0.68rem] font-bold">
              {tf.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <WidgetToolbarDivider />
        <ToggleGroup type="single" value={chartType} onValueChange={(v) => v && setChartType(v)} spacing={0}>
          <ToggleGroupItem value="candle" size="sm" className="gap-1 px-2 text-[0.68rem] font-bold">
            <AreaChart data-icon="inline-start" />Candle
          </ToggleGroupItem>
          <ToggleGroupItem value="line" size="sm" className="gap-1 px-2 text-[0.68rem] font-bold">
            <TrendingUp data-icon="inline-start" />Line
          </ToggleGroupItem>
        </ToggleGroup>
        {chartInteractionMode !== 'normal' && (
          <Button
            variant="destructive"
            size="sm"
            className="ml-auto h-6 text-[0.62rem]"
            onClick={() => setChartInteractionMode('normal')}
          >
            Cancel {chartInteractionMode === 'edit_sl' ? 'SL' : 'TP'} Edit
          </Button>
        )}
      </WidgetToolbar>
      <WidgetToolbar className="min-h-8 flex-wrap py-1">
        <ToggleGroup
          type="multiple"
          value={activeIndicatorKeys}
          onValueChange={handleIndicatorsChange}
          className="flex flex-wrap gap-0.5"
          spacing={0}
        >
          {Object.entries(INDICATORS).map(([key, ind]) => (
            <ToggleGroupItem
              key={key}
              value={key}
              size="sm"
              className="gap-1 text-[0.62rem] font-semibold data-[state=on]:border-[var(--ind-c)] data-[state=on]:bg-[color-mix(in_srgb,var(--ind-c)_14%,transparent)] data-[state=on]:text-[var(--ind-c)]"
              style={{ '--ind-c': ind.color }}
            >
              <span className="size-1.5 shrink-0 rounded-full bg-[var(--ind-c)] opacity-70" />
              {ind.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </WidgetToolbar>
    </>
  );

  return (
    <WidgetShell
      className={cn(chartInteractionMode !== 'normal' && 'chart-interactive-mode relative')}
      icon={AreaChart}
      title={activeSymbol}
      headerRight={
        <div className="flex min-w-0 items-center gap-2">
          <ChartHeaderPrice symbol={activeSymbol} />
          <ChartSignalBadge symbol={activeSymbol} />
        </div>
      }
      toolbar={chartToolbar}
      contentClassName="relative flex min-h-0 flex-1 flex-col overflow-hidden p-0"
    >
      {chartInteractionMode !== 'normal' && (
        <Badge className="pointer-events-none absolute top-2 left-1/2 z-[100] -translate-x-1/2 gap-1.5 border-primary/40 bg-primary/90 px-3 py-1 text-[0.68rem] font-bold text-primary-foreground shadow-[0_0_15px_var(--color-accent-bg)]">
          Click chart to set {chartInteractionMode === 'edit_sl' ? 'Stop Loss' : 'Take Profit'}
          <span className="font-normal opacity-80">(ESC to cancel)</span>
        </Badge>
      )}

      <div className="relative min-h-0 flex-1 overflow-hidden">
        <div className="pointer-events-none absolute top-1.5 left-2.5 z-10 flex select-none items-center gap-3 font-mono text-[11px]">
          {[
            ['O', 'o'],
            ['H', 'h'],
            ['L', 'l'],
            ['C', 'c'],
          ].map(([label, id]) => (
            <span key={label} className="flex gap-1">
              <span className="font-normal text-muted-foreground">{label}</span>
              <span id={`chart-legend-${id}`} className="font-bold">—</span>
            </span>
          ))}
          <span className="flex gap-1">
            <span className="font-normal text-muted-foreground">V</span>
            <span id="chart-legend-v" className="font-bold text-trading-accent">—</span>
          </span>
          <span id="chart-legend-pct" className="text-[10px] font-bold opacity-90">—</span>
        </div>

        <div ref={containerRef} className="h-full w-full" />
      </div>
    </WidgetShell>
  );
}
