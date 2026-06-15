/**
 * ChartWidget.jsx — Professional Trading Chart using Apache ECharts
 */
import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import * as echarts from 'echarts';
import { useStore } from '../store/useStore';
import { useSettingsStore } from '../store/useSettingsStore';
import { getChartEchartsTheme, hexToRgba } from '../settings/applySettings';
import { CHART_LAYOUT_RESET_EVENT, DEFAULT_TERMINAL_SETTINGS } from '../settings/defaults';
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
import { getCandles, getOldestBarTime, toUnixSeconds } from '../services/candleBuffer';
import { fetchOlderCandles } from '../api/endpoints';
import { Action } from '../api/protocol';
import { parseTradeTimestamp, parseSignalBarTime } from '@/lib/botAttribution';

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

/** Default visible bars; grows when user scrolls into archived history */
const CHART_DISPLAY_BARS = 600;
const CHART_DISPLAY_MAX = 15000;
const ARCHIVE_LOAD_CHUNK = 1000;
const ARCHIVE_1M_RETENTION_SEC = 90 * 86400;
const FUTURE_PADDING = 15;

const pad = (n) => String(n).padStart(2, '0');

function formatTimeLabel(timeSec) {
  const d = new Date(timeSec * 1000);
  return `${pad(d.getUTCMonth() + 1)}/${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

function buildCategoryAxisData(bars) {
  const keys = bars.map((c) => toUnixSeconds(c.time));
  for (let i = 0; i < FUTURE_PADDING; i++) keys.push(`__pad_${i}__`);
  return keys;
}

function indexOfCategoryKey(categoryData, key) {
  if (key == null || !categoryData.length) return -1;
  const sec = toUnixSeconds(key);
  for (let i = 0; i < categoryData.length; i++) {
    const k = categoryData[i];
    if (k === key) return i;
    if (sec != null && toUnixSeconds(k) === sec) return i;
  }
  return -1;
}

function lastRealCategoryIndex(categoryData) {
  return Math.max(0, categoryData.length - FUTURE_PADDING - 1);
}

function defaultDataZoomPercent(candleCount, totalCategoryCount, visibleBars = 50) {
  const endPct = (candleCount / totalCategoryCount) * 100;
  const startPct = Math.max(0, ((candleCount - visibleBars) / totalCategoryCount) * 100);
  return { start: startPct, end: endPct };
}

function preserveDataZoomPercent(prevCategoryData, nextCategoryData, prevDz, candleCount, nextCandleCount) {
  if (prevDz?.start == null || prevDz?.end == null) return null;

  const prevRealEnd = lastRealCategoryIndex(prevCategoryData);
  const prevEndIdx = prevDz.endValue != null
    ? indexOfCategoryKey(prevCategoryData, prevDz.endValue)
    : Math.round((prevDz.end / 100) * prevCategoryData.length);
  const wasAtEnd = prevEndIdx >= prevRealEnd - 1;
  const diff = nextCategoryData.length - prevCategoryData.length;

  if (diff > 0 && wasAtEnd) {
    return defaultDataZoomPercent(nextCandleCount, nextCategoryData.length);
  }

  return { start: prevDz.start, end: prevDz.end };
}

function isDataZoomNearLeftEdge(dz, categoryData) {
  if (!dz || !categoryData.length) return false;
  if (typeof dz.start === 'number' && dz.start <= 2) return true;
  const start = dz.startValue;
  if (start == null) return false;
  let idx = indexOfCategoryKey(categoryData, start);
  if (idx < 0 && typeof start === 'number' && start <= 10) idx = Math.floor(start);
  if (idx < 0) return false;
  return idx <= 10;
}

/** TOS-style: buys below bar, sells above; exits at fill price. */
function markerYForTrade(candle, side, { isExit = false, fillPrice } = {}) {
  if (isExit) return fillPrice ?? candle?.close;
  if (side === 'BUY') return candle?.low ?? fillPrice;
  if (side === 'SELL') return candle?.high ?? fillPrice;
  return fillPrice ?? candle?.close;
}

function categoryAxisLabelFormatter(val) {
  if (val == null || val === '' || String(val).startsWith('__pad_')) return '';
  const sec = toUnixSeconds(val);
  return sec == null ? '' : formatTimeLabel(sec);
}

/** Shared x-axis category config — unix keys with human-readable labels. */
function categoryXAxisOpts(categoryData, gridIndex, { showLabels = true, chartTheme } = {}) {
  const gridColor = chartTheme?.gridColor ?? 'rgba(255,255,255,0.03)';
  const axisLineColor = chartTheme?.axisLineColor ?? 'rgba(255,255,255,0.06)';
  const axisLabelColor = chartTheme?.axisLabelColor ?? '#9ca3af';
  return {
    type: 'category',
    data: categoryData,
    gridIndex,
    scale: true,
    boundaryGap: false,
    axisLine: { onZero: false, lineStyle: { color: axisLineColor } },
    splitLine: { show: true, lineStyle: { color: gridColor } },
    axisLabel: {
      show: showLabels,
      color: axisLabelColor,
      formatter: categoryAxisLabelFormatter,
    },
  };
}

function buildMainSeriesData(bars, chartType) {
  const data = chartType === 'line'
    ? bars.map(c => c.close)
    : bars.map(c => [c.open, c.close, c.low, c.high]);
  for (let i = 0; i < FUTURE_PADDING; i++) data.push('-');
  return data;
}

function buildVolumeSeriesData(bars, chartTheme) {
  const data = bars.map(c => volumeSeriesEntry(c, chartTheme));
  for (let i = 0; i < FUTURE_PADDING; i++) data.push(null);
  return data;
}

function normalizeEchartsList(value) {
  if (value == null) return [];
  return Array.isArray(value) ? value : [value];
}

function aggregateBucket(raw, cfg) {
  if (!raw.length) return null;
  const lastSec = toUnixSeconds(raw[raw.length - 1].time);
  if (lastSec == null) return null;
  const t = Math.floor(lastSec / cfg.secs) * cfg.secs;
  const bucket = raw.filter((c) => {
    const sec = toUnixSeconds(c.time);
    return sec != null && Math.floor(sec / cfg.secs) * cfg.secs === t;
  });
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

function bucketCandles(raw, intervalSecs) {
  const buckets = new Map();
  for (const c of raw) {
    const sec = toUnixSeconds(c.time);
    if (sec == null) continue;
    const t = Math.floor(sec / intervalSecs) * intervalSecs;
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
  return Array.from(buckets.values()).sort((a, b) => a.time - b.time);
}

function volumeSeriesEntry(bar, chartTheme) {
  const bullish = chartTheme?.bullishColor ?? '#10b981';
  const bearish = chartTheme?.bearishColor ?? '#ef4444';
  return {
    value: bar.volume || 0,
    itemStyle: {
      color: bar.close >= bar.open
        ? hexToRgba(bullish, 0.35)
        : hexToRgba(bearish, 0.35),
    },
  };
}

const MACD_WARMUP = 33;
const RSI_WARMUP = 14;
const ATR_WARMUP = 14;
const BB_WARMUP = 19;

function padIndicatorValues(data) {
  const out = [...data];
  for (let i = 0; i < FUTURE_PADDING; i++) out.push(null);
  return out;
}

function mapEmaSeries(candles, period) {
  const ema = calcEMA(candles, period);
  const offset = period - 1;
  return padIndicatorValues(candles.map((_, i) => (i >= offset ? ema[i - offset]?.value : null)));
}

function mapRsiSeries(candles) {
  const rsi = calcRSI(candles, 14);
  return padIndicatorValues(candles.map((_, i) => (i >= RSI_WARMUP ? rsi[i - RSI_WARMUP]?.value : null)));
}

function mapAtrSeries(candles) {
  const atr = calcATR(candles, 14);
  return padIndicatorValues(candles.map((_, i) => (i >= ATR_WARMUP ? atr[i - ATR_WARMUP]?.value : null)));
}

function mapMacdSeries(candles) {
  const macd = calcMACD(candles, 12, 26, 9);
  const mapper = (mList) => padIndicatorValues(
    candles.map((_, i) => (i >= MACD_WARMUP ? mList[i - MACD_WARMUP]?.value : null)),
  );
  const hist = padIndicatorValues(
    candles.map((_, i) => {
      if (i < MACD_WARMUP) return null;
      const item = macd.histogram[i - MACD_WARMUP];
      return item ? {
        value: item.value,
        itemStyle: { color: item.value >= 0 ? 'rgba(16,185,129,0.4)' : 'rgba(239,68,68,0.4)' },
      } : null;
    }),
  );
  return { macd: mapper(macd.macdLine), signal: mapper(macd.signalLine), hist };
}

function mapBbSeries(candles) {
  const bb = calcBollingerBands(candles, 20, 2);
  const mapper = (bbList) => padIndicatorValues(
    candles.map((_, i) => (i >= BB_WARMUP ? bbList[i - BB_WARMUP]?.value : null)),
  );
  return { upper: mapper(bb.upper), middle: mapper(bb.middle), lower: mapper(bb.lower) };
}

function mapVwapSeries(candles) {
  const vwap = calcVWAP(candles);
  return padIndicatorValues(candles.map((_, i) => vwap[i]?.value ?? null));
}

/** Series patches for live candle updates — keeps sub-panes in sync with price/volume. */
function buildIndicatorSeriesPatches(bars, active, chartTheme) {
  const patches = [];
  if (active.ema9) patches.push({ id: 'ema9', data: mapEmaSeries(bars, 9) });
  if (active.ema21) patches.push({ id: 'ema21', data: mapEmaSeries(bars, 21) });
  if (active.ema50) patches.push({ id: 'ema50', data: mapEmaSeries(bars, 50) });
  if (active.bb) {
    const bb = mapBbSeries(bars);
    patches.push({ id: 'bb-upper', data: bb.upper });
    patches.push({ id: 'bb-mid', data: bb.middle });
    patches.push({ id: 'bb-lower', data: bb.lower });
  }
  if (active.vwap) patches.push({ id: 'vwap', data: mapVwapSeries(bars) });
  if (active.volume) patches.push({ id: 'volume', data: buildVolumeSeriesData(bars, chartTheme) });
  if (active.rsi) patches.push({ id: 'rsi', data: mapRsiSeries(bars) });
  if (active.macd) {
    const m = mapMacdSeries(bars);
    patches.push({ id: 'macd', data: m.macd });
    patches.push({ id: 'macd-signal', data: m.signal });
    patches.push({ id: 'macd-hist', data: m.hist });
  }
  if (active.atr) patches.push({ id: 'atr', data: mapAtrSeries(bars) });
  return patches;
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

/** Map a bar open-time (unix sec) to the displayed candle index. */
function findBarIndexForBarTime(candles, barTimeSec, bucketSecs = 60) {
  if (!candles.length) return -1;

  const tsSec = toUnixSeconds(barTimeSec);
  if (tsSec == null) return -1;

  const bucketTs = Math.floor(tsSec / bucketSecs) * bucketSecs;
  const first = toUnixSeconds(candles[0].time);
  const last = toUnixSeconds(candles[candles.length - 1].time);
  if (first != null && bucketTs < first) return -1;
  if (last != null && bucketTs > last) return -1;

  for (let i = 0; i < candles.length; i++) {
    if (toUnixSeconds(candles[i].time) === bucketTs) return i;
  }

  for (let i = candles.length - 1; i >= 0; i--) {
    if (toUnixSeconds(candles[i].time) <= tsSec) return i;
  }
  return -1;
}

/** Resolve marker x/y — bot signals anchor to the closed bar, not fill clock time. */
function resolveBotMarkerAnchor(trade, candles, bucketSecs) {
  const isExit = trade.is_exit === 1 || trade.is_exit === true;
  const signalBar = parseSignalBarTime(trade);
  if (signalBar != null) {
    const idx = findBarIndexForBarTime(candles, signalBar, bucketSecs);
    if (idx < 0) return null;
    const candle = candles[idx];
    return {
      idx,
      yPrice: markerYForTrade(candle, trade.side, { isExit, fillPrice: trade.price }),
    };
  }

  const d = parseTradeTimestamp(trade.timestamp);
  const tsSec = d ? Math.floor(d.getTime() / 1000) : null;
  if (tsSec == null) return null;
  const idx = findBarIndexForBarTime(candles, tsSec, bucketSecs);
  if (idx < 0) return null;
  const candle = candles[idx];
  return {
    idx,
    yPrice: markerYForTrade(candle, trade.side, { isExit, fillPrice: trade.price }),
  };
}

/** Map a trade timestamp to the candle index that contains it (bucket-aligned). */
function findBarIndexForTrade(candles, timestamp, bucketSecs = 60) {
  const d = parseTradeTimestamp(timestamp);
  const tsSec = d ? Math.floor(d.getTime() / 1000) : null;
  if (tsSec == null) return -1;
  return findBarIndexForBarTime(candles, tsSec, bucketSecs);
}

function toSignalScatterPoint(candles, barIndex, yPrice, { value, symbol, symbolSize, itemStyle }) {
  if (barIndex < 0) return null;
  const clamped = Math.max(0, Math.min(barIndex, candles.length - 1));
  const cat = toUnixSeconds(candles[clamped]?.time);
  if (cat == null) return null;
  return {
    value: [cat, yPrice],
    name: value,
    symbol,
    symbolSize,
    itemStyle,
  };
}

function tradeMarkerPoint(candles, timestamp, price, bucketSecs, marker) {
  const idx = findBarIndexForTrade(candles, timestamp, bucketSecs);
  if (idx < 0) return null;
  const candle = candles[idx];
  const side = marker.side ?? (String(marker.value).startsWith('BUY') ? 'BUY' : 'SELL');
  const yPrice = markerYForTrade(candle, side, { fillPrice: price });
  return toSignalScatterPoint(candles, idx, yPrice, marker);
}

function buildBotTradeMarkers(botTrades, candles, bucketSecs) {
  if (!botTrades?.length || !candles.length) return [];
  return botTrades.map((t) => {
    const isExit = t.is_exit === 1 || t.is_exit === true;
    const anchor = resolveBotMarkerAnchor(t, candles, bucketSecs);
    if (!anchor) return null;
    return toSignalScatterPoint(candles, anchor.idx, anchor.yPrice, {
      value: `${t.side}${isExit ? ' exit' : ''}`,
      symbol: isExit ? 'pin' : (t.side === 'BUY' ? 'path://M0,10 L5,0 L10,10 Z' : 'path://M0,0 L5,10 L10,0 Z'),
      symbolSize: isExit ? 14 : 11,
      itemStyle: { color: isExit ? '#f59e0b' : (t.side === 'BUY' ? '#10b981' : '#ef4444') },
    });
  }).filter(Boolean);
}

function buildTradeMarkers(tradeHistory, activeSymbol, candles, bucketSecs, { excludeBotId } = {}) {
  return tradeHistory
    .filter((t) => t.symbol === activeSymbol && t.status === 'FILLED')
    .filter((t) => !(excludeBotId && t.bot_id === excludeBotId))
    .map((t) => {
      const price = t.average_fill_price || t.price;
      const qty = (t.filled_quantity ?? t.quantity)?.toFixed(4);
      return tradeMarkerPoint(candles, t.timestamp, price, bucketSecs, {
        side: t.side,
        value: `${t.side} ${qty}`,
        symbol: t.side === 'BUY' ? 'path://M0,10 L5,0 L10,10 Z' : 'path://M0,0 L5,10 L10,0 Z',
        symbolSize: 10,
        itemStyle: { color: t.side === 'BUY' ? '#10b981' : '#ef4444' },
      });
    })
    .filter(Boolean);
}

// ─── Child Component: Header Ticker ──────────────────────────────────
function ChartHeaderPrice({ symbol }) {
  const ticker = useStore(state => state.tickerData[symbol]);
  const direction = useStore(state => state.priceDirections[symbol]);

  if (!ticker) return null;
  const dec = getPriceDecimals(ticker.price);

  return (
    <div className="flex min-w-0 items-center gap-[var(--icon-gap-loose)] overflow-hidden text-sm">
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
  const loadingOlderRef = useRef(false);
  const olderExhaustedRef = useRef({});
  const loadOlderRef = useRef(null);

  const [displayBarLimit, setDisplayBarLimit] = useState(CHART_DISPLAY_BARS);
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
  const selectedBotId = useStore(state => state.selectedBotId);
  const botDetail = useStore(state => state.botDetail);
  const botOverlayKey = useStore(state => {
    if (!state.selectedBotId || !state.botDetail?.trades) return '';
    return state.botDetail.trades.map(
      (t) => `${t.id}:${t.signal_bar_time ?? ''}:${t.signal_id ?? ''}:${t.side}`,
    ).join(';');
  });
  const chartInteractionMode = useStore(state => state.chartInteractionMode);
  const setChartInteractionMode = useStore(state => state.setChartInteractionMode);

  const settings = useSettingsStore(state => state.settings);
  const resolvedTheme = useSettingsStore(state => state.resolvedTheme);
  const updateChartLayout = useSettingsStore(state => state.updateChartLayout);
  const chartTheme = useMemo(
    () => getChartEchartsTheme(settings, resolvedTheme),
    [settings, resolvedTheme],
  );

  const [timeframe, setTimeframe] = useState(() => { try { return localStorage.getItem('terminal_tf') || '1m'; } catch { return '1m'; } });
  const prevConfigRef = useRef({ symbol: activeSymbol, timeframe: timeframe });

  useEffect(() => {
    setDisplayBarLimit(CHART_DISPLAY_BARS);
    olderExhaustedRef.current[activeSymbol] = false;
  }, [activeSymbol, timeframe]);
  const [chartType, setChartType] = useState(() => {
    try {
      const saved = localStorage.getItem('terminal_chart_type');
      if (saved === 'line' || saved === 'candle') return saved;
    } catch (_) {}
    return 'candle';
  });
  const [active, setActive] = useState(() => {
    try {
      const s = localStorage.getItem('terminal_chart_indicators_active');
      if (s) return JSON.parse(s);
    } catch (_) {}
    return { ...DEFAULT_TERMINAL_SETTINGS.chartLayout.activeIndicators };
  });


  useEffect(() => { try { localStorage.setItem('terminal_tf', timeframe); } catch {} }, [timeframe]);
  useEffect(() => { try { localStorage.setItem('terminal_chart_type', chartType); } catch {} }, [chartType]);
  useEffect(() => { try { localStorage.setItem('terminal_chart_indicators_active', JSON.stringify(active)); } catch {} }, [active]);

  useEffect(() => {
    updateChartLayout({ timeframe, chartType, activeIndicators: active });
  }, [timeframe, chartType, active, updateChartLayout]);

  useEffect(() => {
    const onReset = (e) => {
      const cl = e.detail?.chartLayout ?? DEFAULT_TERMINAL_SETTINGS.chartLayout;
      setTimeframe(cl.timeframe);
      setChartType(cl.chartType);
      setActive({ ...cl.activeIndicators });
      chartReadyRef.current = false;
      try { chartRef.current?.clear(); } catch (_) {}
    };
    window.addEventListener(CHART_LAYOUT_RESET_EVENT, onReset);
    return () => window.removeEventListener(CHART_LAYOUT_RESET_EVENT, onReset);
  }, []);

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
    const series = bucketCandles(raw, cfg.secs);
    const limit = displayBarLimit;
    return series.length > limit ? series.slice(-limit) : series;
  }, [timeframe, activeSymbol, historyRev, displayBarLimit]);

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

    const categoryData = buildCategoryAxisData(candles);

    // Preserve zoom (% window) — stable under indicator toggles; timestamp keys on scatter x
    let zoomStart = null;
    let zoomEnd = null;

    if (prevConfigRef.current.symbol === activeSymbol && prevConfigRef.current.timeframe === timeframe && chartRef.current) {
      try {
        const currentOption = chartRef.current.getOption();
        const dataZoomList = normalizeEchartsList(currentOption?.dataZoom);
        const xAxisList = normalizeEchartsList(currentOption?.xAxis);
        if (dataZoomList[0] && xAxisList[0]?.data) {
          const preserved = preserveDataZoomPercent(
            xAxisList[0].data,
            categoryData,
            dataZoomList[0],
            candles.length,
            candles.length,
          );
          if (preserved) {
            zoomStart = preserved.start;
            zoomEnd = preserved.end;
          }
        }
      } catch (err) {
        console.warn('[ChartWidget] zoom preservation failed:', err);
      }
    }

    if (zoomStart == null || zoomEnd == null) {
      ({ start: zoomStart, end: zoomEnd } = defaultDataZoomPercent(candles.length, categoryData.length));
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
      ...categoryXAxisOpts(categoryData, 0, { showLabels: grids.length === 1, chartTheme }),
    });

    yAxes.push({
      scale: true,
      gridIndex: 0,
      position: 'right',
      splitLine: { show: true, lineStyle: { color: chartTheme.gridColor } },
      axisLine: { lineStyle: { color: chartTheme.axisLineColor } },
      axisLabel: { color: chartTheme.axisLabelColor, formatter: val => val.toFixed(dec) }
    });

    // Sub grids axes
    const gridCount = grids.length;
    subPanes.forEach((pane, idx) => {
      const gIdx = paneGridMap[pane];
      const isLowest = gIdx === gridCount - 1;

      xAxes.push({
        id: `x-${xAxes.length}`,
        ...categoryXAxisOpts(categoryData, gIdx, { showLabels: isLowest, chartTheme }),
        axisTick: { show: isLowest },
      });

      let yAxisOpt = {
        scale: true,
        gridIndex: gIdx,
        position: 'right',
        splitLine: { show: true, lineStyle: { color: chartTheme.gridColor } },
        axisLine: { lineStyle: { color: chartTheme.axisLineColor } },
        axisLabel: { color: chartTheme.axisLabelColor, fontSize: 9 }
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
        lineStyle: { color: chartTheme.crosshairLabelBg, width: 2 },
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
          color: chartTheme.bullishColor,
          color0: chartTheme.bearishColor,
          borderColor: chartTheme.bullishColor,
          borderColor0: chartTheme.bearishColor,
        },
      });
    }

    // Signal markers — scatter layer shares the category x-axis (stable under zoom/pan)
    series.push({
      id: 'signal-markers',
      name: 'Signals',
      type: 'scatter',
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: [],
      clip: true,
      z: 6,
      animation: false,
      tooltip: { show: false },
    });

    // Overlay indicators
    if (active.ema9) {
      series.push({
        id: 'ema9',
        name: 'EMA 9', type: 'line', data: mapEmaSeries(candles, 9), xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: { color: '#f59e0b', width: 1, opacity: 0.85 }
      });
    }
    if (active.ema21) {
      series.push({
        id: 'ema21',
        name: 'EMA 21', type: 'line', data: mapEmaSeries(candles, 21), xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: { color: '#8b5cf6', width: 1, opacity: 0.85 }
      });
    }
    if (active.ema50) {
      series.push({
        id: 'ema50',
        name: 'EMA 50', type: 'line', data: mapEmaSeries(candles, 50), xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: { color: '#06b6d4', width: 1, opacity: 0.85 }
      });
    }
    if (active.bb) {
      const bb = mapBbSeries(candles);
      series.push(
        { id: 'bb-upper', name: 'BB Upper', type: 'line', data: bb.upper, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#6366f1', width: 1, type: 'dashed', opacity: 0.7 } },
        { id: 'bb-mid', name: 'BB Mid', type: 'line', data: bb.middle, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: 'rgba(99,102,241,0.3)', width: 1, type: 'dotted' } },
        { id: 'bb-lower', name: 'BB Lower', type: 'line', data: bb.lower, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#6366f1', width: 1, type: 'dashed', opacity: 0.7 } }
      );
    }
    if (active.vwap) {
      series.push({
        id: 'vwap',
        name: 'VWAP', type: 'line', data: mapVwapSeries(candles), xAxisIndex: 0, yAxisIndex: 0,
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
        data: buildVolumeSeriesData(candles, chartTheme),
      });
    }

    if (showRsi) {
      const gIdx = paneGridMap.rsi;
      series.push({
        id: 'rsi',
        name: 'RSI', type: 'line', data: mapRsiSeries(candles), xAxisIndex: gIdx, yAxisIndex: gIdx,
        showSymbol: false, lineStyle: { color: '#fbbf24', width: 1.5 }
      });
    }

    if (showMacd) {
      const gIdx = paneGridMap.macd;
      const macd = mapMacdSeries(candles);
      series.push(
        { id: 'macd', name: 'MACD', type: 'line', data: macd.macd, xAxisIndex: gIdx, yAxisIndex: gIdx, showSymbol: false, lineStyle: { color: '#34d399', width: 1.2 } },
        { id: 'macd-signal', name: 'Signal', type: 'line', data: macd.signal, xAxisIndex: gIdx, yAxisIndex: gIdx, showSymbol: false, lineStyle: { color: '#f87171', width: 1.2 } },
        {
          id: 'macd-hist',
          name: 'Hist',
          type: 'bar',
          xAxisIndex: gIdx,
          yAxisIndex: gIdx,
          data: macd.hist,
        }
      );
    }

    if (showAtr) {
      const gIdx = paneGridMap.atr;
      series.push({
        id: 'atr',
        name: 'ATR', type: 'line', data: mapAtrSeries(candles), xAxisIndex: gIdx, yAxisIndex: gIdx,
        showSymbol: false, lineStyle: { color: '#94a3b8', width: 1.5 }
      });
    }

    // Zoom and pan links
    const zoomXIndices = grids.map((_, i) => i);

    const option = {
      backgroundColor: chartTheme.backgroundColor,
      axisPointer: {
        link: [{ xAxisIndex: 'all' }],
        label: { backgroundColor: chartTheme.crosshairLabelBg }
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
        { type: 'inside', xAxisIndex: zoomXIndices, start: zoomStart, end: zoomEnd },
        { type: 'slider', xAxisIndex: zoomXIndices, start: zoomStart, end: zoomEnd, bottom: '3%', height: 18, borderColor: 'transparent', fillerColor: chartTheme.dataZoomFiller, textStyle: { color: chartTheme.axisLabelColor } }
      ],
      series: series
    };

    chartRef.current.setOption(option, { notMerge: true });

    chartLayoutRef.current = { xAxisCount: xAxes.length, showVolume: showVol };
    chartReadyRef.current = true;

    // Initial legend display
    const lastBar = candles[candles.length - 1];
    updateLegendDOM(lastBar);

    requestAnimationFrame(() => applyOverlayPatchRef.current?.());
  }, [aggregatedCandles, activeSymbol, timeframe, active, chartType, updateLegendDOM, chartTheme]);

  // Lightweight overlay patch — SL/TP lines and trade markers only
  const applyOverlayPatch = useCallback(() => {
    const chart = chartRef.current;
    const bars = candlesRef.current;
    if (!chart || !bars.length || !chartReadyRef.current) return;

    const cfg = TF_CONFIGS.find((t) => t.label === timeframe) || TF_CONFIGS[0];
    const bucketSecs = cfg.secs;
    const dec = getPriceDecimals(bars[bars.length - 1]?.close);
    const markLineData = buildMarkLineData(symbolPosition, dec);
    const showBotMarkers = selectedBotId
      && botDetail?.bot?.symbol === activeSymbol
      && botDetail?.trades?.length;
    const tradeMarkers = buildTradeMarkers(
      tradeHistory,
      activeSymbol,
      bars,
      bucketSecs,
      { excludeBotId: showBotMarkers ? selectedBotId : null },
    );
    const botMarkers = showBotMarkers
      ? buildBotTradeMarkers(botDetail.trades, bars, bucketSecs)
      : [];
    const scatterData = [...tradeMarkers, ...botMarkers];

    try {
      chart.setOption({
        series: [
          {
            id: 'main',
            markLine: { symbol: ['none', 'none'], animation: false, data: markLineData },
            markPoint: { data: [] },
          },
          {
            id: 'signal-markers',
            data: scatterData,
          },
        ],
      }, { lazyUpdate: false });
    } catch (err) {
      console.warn('[ChartWidget] overlay patch failed:', err);
    }
  }, [activeSymbol, timeframe, symbolPosition, tradeHistory, selectedBotId, botDetail, botOverlayKey]);

  configureChartRef.current = configureChart;
  applyOverlayPatchRef.current = applyOverlayPatch;

  const loadOlderHistory = useCallback(async () => {
    if (loadingOlderRef.current || olderExhaustedRef.current[activeSymbol]) return;

    const oldest = getOldestBarTime(activeSymbol);
    if (oldest == null) return;

    const cfg = TF_CONFIGS.find((t) => t.label === timeframe) || TF_CONFIGS[0];
    const barSecs = cfg.secs <= 60 ? 60 : cfg.secs;
    const nowSec = Math.floor(Date.now() / 1000);
    let interval = cfg.secs >= 3600 ? '1h' : '1m';
    if (oldest < nowSec - ARCHIVE_1M_RETENTION_SEC) {
      interval = 'auto';
    }
    const chunk = interval === 'auto' || interval === '1h' ? ARCHIVE_LOAD_CHUNK : Math.min(ARCHIVE_LOAD_CHUNK, 500);
    const from = oldest - chunk * barSecs;
    const to = oldest - barSecs;

    loadingOlderRef.current = true;
    try {
      const added = await fetchOlderCandles(activeSymbol, from, to, interval);
      if (added <= 0) {
        olderExhaustedRef.current[activeSymbol] = true;
      } else {
        setDisplayBarLimit((prev) => Math.min(prev + added, CHART_DISPLAY_MAX));
      }
    } catch (err) {
      console.warn('[ChartWidget] load older history failed:', err);
    } finally {
      loadingOlderRef.current = false;
    }
  }, [activeSymbol, timeframe]);

  loadOlderRef.current = loadOlderHistory;

  // Init ECharts once the container has non-zero layout (avoids zero-size init warning).
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
      chartReadyRef.current = false;

      chart.on('updateAxisPointer', (event) => {
        const axesInfo = event.axesInfo;
        const candles = candlesRef.current;
        if (axesInfo && axesInfo[0] && candles?.length) {
          const info = axesInfo[0];
          let idx = info.value;
          if (typeof idx !== 'number' || idx >= candles.length || idx < 0) {
            idx = candles.findIndex((c) => toUnixSeconds(c.time) === toUnixSeconds(info.value));
          }
          if (idx >= 0 && idx < candles.length && candles[idx]) {
            updateLegendDOM(candles[idx]);
            return;
          }
        }
        if (candles?.length) {
          updateLegendDOM(candles[candles.length - 1]);
        }
      });

      chart.on('datazoom', () => {
        if (!chartReadyRef.current) return;
        try {
          const currentOption = chart.getOption();
          const dataZoomList = normalizeEchartsList(currentOption?.dataZoom);
          const xAxisList = normalizeEchartsList(currentOption?.xAxis);
          const dz = dataZoomList[0];
          const cats = xAxisList[0]?.data ?? [];
          if (isDataZoomNearLeftEdge(dz, cats)) {
            loadOlderRef.current?.();
          }
        } catch (err) {
          console.warn('[ChartWidget] datazoom handler failed:', err);
        }
      });

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

      if (import.meta.env.DEV) {
        el.__chartInstance = chart;
      }

      if (displayBarsRef.current.length > 0) {
        requestAnimationFrame(() => {
          configureChartRef.current();
          applyOverlayPatchRef.current();
        });
      }
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
      if (el.__chartInstance) delete el.__chartInstance;
    };
  }, [updateLegendDOM, chartTheme.echartsTheme, resolvedTheme]);

  // Full rebuild when structure/data/indicators change
  useEffect(() => {
    configureChart();
  }, [configureChart]);

  // Lightweight overlay patch — trades, positions, and after full rebuild
  useEffect(() => {
    applyOverlayPatch();
  }, [applyOverlayPatch, positionOverlayKey, tradeOverlayKey, botOverlayKey]);

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
        if (bars.length > displayBarLimit) bars.shift();
    } else {
      return;
    }

    candlesRef.current = bars;

    const categoryData = buildCategoryAxisData(bars);
    const { xAxisCount } = chartLayoutRef.current;
    const patch = {
      xAxis: Array.from({ length: xAxisCount }, (_, i) => ({
        id: `x-${i}`,
        data: categoryData,
      })),
      series: [
        {
          id: 'main',
          type: chartType === 'line' ? 'line' : 'candlestick',
          data: buildMainSeriesData(bars, chartType),
        },
        ...buildIndicatorSeriesPatches(bars, active, chartTheme),
      ],
    };

    try {
      chart.setOption(patch, { lazyUpdate: false });
      updateLegendDOM(aggregatedLive);
      applyOverlayPatchRef.current?.();
    } catch (err) {
      console.warn('[ChartWidget] live candle update failed:', err);
    }
  }, [activeSymbol, timeframe, chartType, updateLegendDOM, active, displayBarLimit, chartTheme]);

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
        import('../api/transport').then(({ sendAction }) => {
          sendAction(Action.UPDATE_POSITION_SL_TP, { symbol: activeSymbol, stop_loss_price: price });
        });
      } else if (chartInteractionMode === 'edit_tp') {
        import('../api/transport').then(({ sendAction }) => {
          sendAction(Action.UPDATE_POSITION_SL_TP, { symbol: activeSymbol, take_profit_price: price });
        });
      }
      
      setChartInteractionMode('normal');
    };
    
    window.addEventListener('chart-click', handleChartClick);
    return () => window.removeEventListener('chart-click', handleChartClick);
  }, [chartInteractionMode, activeSymbol, setChartInteractionMode]);

  const chartToolbar = (
    <div className="chart-toolbar-stack">
      <div className="chart-toolbar-row">
        <div className="scroll-fade-x">
          <WidgetToolbar className="scroll-panel-x no-scrollbar flex-nowrap border-0 py-0">
            <ToggleGroup type="single" value={timeframe} onValueChange={(v) => v && setTimeframe(v)} spacing={0}>
              {TF_CONFIGS.map(tf => (
                <ToggleGroupItem key={tf.label} value={tf.label} size="sm" className="px-2 text-[0.68rem] font-bold">
                  {tf.label}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
            <WidgetToolbarDivider />
            <ToggleGroup type="single" value={chartType} onValueChange={(v) => v && setChartType(v)} spacing={0}>
              <ToggleGroupItem value="candle" size="sm" className="px-2 text-[0.68rem] font-bold">
                <AreaChart data-icon="inline-start" />
                Candle
              </ToggleGroupItem>
              <ToggleGroupItem value="line" size="sm" className="px-2 text-[0.68rem] font-bold">
                <TrendingUp data-icon="inline-start" />
                Line
              </ToggleGroupItem>
            </ToggleGroup>
            {chartInteractionMode !== 'normal' && (
              <Button
                variant="destructive"
                size="sm"
                className="ml-auto h-6 shrink-0 text-[0.62rem]"
                onClick={() => setChartInteractionMode('normal')}
              >
                Cancel {chartInteractionMode === 'edit_sl' ? 'SL' : 'TP'} Edit
              </Button>
            )}
          </WidgetToolbar>
        </div>
      </div>
      <div className="chart-toolbar-row">
        <div className="scroll-fade-x">
          <WidgetToolbar compact className="scroll-panel-x no-scrollbar flex-nowrap border-0">
            <ToggleGroup
              type="multiple"
              value={activeIndicatorKeys}
              onValueChange={handleIndicatorsChange}
              className="flex flex-nowrap gap-[var(--icon-gap)]"
              spacing={1}
            >
              {Object.entries(INDICATORS).map(([key, ind]) => (
                <ToggleGroupItem
                  key={key}
                  value={key}
                  size="sm"
                  className="gap-[var(--icon-gap)] text-[0.62rem] font-semibold data-[state=on]:border-[var(--ind-c)] data-[state=on]:bg-[color-mix(in_srgb,var(--ind-c)_14%,transparent)] data-[state=on]:text-[var(--ind-c)]"
                  style={{ '--ind-c': ind.color }}
                >
                  <span className="size-1.5 shrink-0 rounded-full bg-[var(--ind-c)] opacity-70" />
                  {ind.label}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </WidgetToolbar>
        </div>
      </div>
    </div>
  );

  return (
    <WidgetShell
      className={cn(chartInteractionMode !== 'normal' && 'chart-interactive-mode relative')}
      icon={AreaChart}
      title={activeSymbol}
      headerRight={
        <div className="flex min-w-0 items-center gap-[var(--icon-gap-loose)]">
          <ChartHeaderPrice symbol={activeSymbol} />
          <ChartSignalBadge symbol={activeSymbol} />
        </div>
      }
      toolbar={chartToolbar}
      contentClassName="relative flex min-h-0 flex-1 flex-col overflow-hidden p-0"
    >
      {chartInteractionMode !== 'normal' && (
        <Badge className="icon-label pointer-events-none absolute top-2 left-1/2 z-[100] -translate-x-1/2 border-primary/40 bg-primary/90 px-3 py-1 text-[0.68rem] font-bold text-primary-foreground shadow-[0_0_15px_var(--color-accent-bg)]">
          Click chart to set {chartInteractionMode === 'edit_sl' ? 'Stop Loss' : 'Take Profit'}
          <span className="font-normal opacity-80">(ESC to cancel)</span>
        </Badge>
      )}

      <div className="relative min-h-0 flex-1 overflow-hidden">
        <div className="pointer-events-none absolute top-1.5 left-2.5 z-10 flex select-none items-center gap-[var(--icon-gap-loose)] font-mono text-[11px]">
          {[
            ['O', 'o'],
            ['H', 'h'],
            ['L', 'l'],
            ['C', 'c'],
          ].map(([label, id]) => (
            <span key={label} className="icon-label-tight">
              <span className="font-normal text-muted-foreground">{label}</span>
              <span id={`chart-legend-${id}`} className="font-bold">—</span>
            </span>
          ))}
          <span className="icon-label-tight">
            <span className="font-normal text-muted-foreground">V</span>
            <span id="chart-legend-v" className="font-bold text-trading-accent">—</span>
          </span>
          <span id="chart-legend-pct" className="text-[10px] font-bold opacity-90">—</span>
        </div>

        <div ref={containerRef} className="h-full w-full" data-chart-root="main" />
      </div>
    </WidgetShell>
  );
}
