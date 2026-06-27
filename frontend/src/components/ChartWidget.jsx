/**
 * ChartWidget.jsx — Professional Trading Chart using Apache ECharts
 */
import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import * as echarts from 'echarts';
import { useStore } from '../store/useStore';
import { useSettingsStore } from '../store/useSettingsStore';
import { getChartEchartsTheme, hexToRgba } from '../settings/applySettings';
import {
  getIndicatorTheme,
  getIndicatorToolbarMeta,
  volumeBarEntry,
  macdHistogramColor,
  rsiMarkLine,
  macdZeroMarkLine,
  emaLineStyle,
} from '../settings/indicatorThemes';
import { CHART_LAYOUT_RESET_EVENT, DEFAULT_TERMINAL_SETTINGS } from '../settings/defaults';
import {
  calcSMA, calcEMA, calcBollingerBands, calcRSI, calcMACD, calcATR, buildVwapSeriesValues
} from '../utils/indicators';
import ChartAnalystBadge from './ChartAnalystBadge';
import {
  AreaChart, TrendingUp, Activity, Maximize2, Minimize2, CandlestickChart, Grid3x3,
  Spline, Minus, AlignJustify, Square, BarChart2, Trash2,
  History, Play, Pause, SkipForward, SkipBack, RotateCcw, X,
} from 'lucide-react';
import { WidgetShell, WidgetToolbar, WidgetToolbarDivider } from './WidgetShell';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Button } from '@/components/ui/button';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { GitCompareArrows } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { BACKTEST_OVERLAY_EVENT, symbolsMatch } from '@/lib/backtestDisplay';
import { getCandles, getOldestBarTime, toUnixSeconds, candleBufferKey, patchHtFormingBar, patchHtFormingBarFromPrice, applyLivePrice, chartTimeframeSecs, isHigherTimeframe, hasChartReadyHistory, hasCandleHistory, setCandleHistory, setComparePinnedCandleSymbol, CHART_SNAPSHOT_BARS, CHART_READY_MIN_BARS } from '../services/candleBuffer';
import { fetchCandles } from '../api/endpoints';
import { getStoreActions } from '../api/dispatch';
import { isLiveMassiveMode } from '../lib/massiveMarket';
import { onLivePrice } from '../services/livePriceChannel';
import { selectAgentInsight } from '../lib/agentInsights';
import { fetchOlderCandles } from '../api/endpoints';
import { Action } from '../api/protocol';
import { parseTradeTimestamp, parseSignalBarTime } from '@/lib/botAttribution';
import {
  CHART_DISPLAY_BARS_DEFAULT,
  CHART_DISPLAY_MAX_BARS,
} from '../services/memoryBudget';
import { applyCandleTransform, estimateRenkoBrickSize } from '../lib/chart/candleTransforms';
import { computeVolumeProfile, volumeProfileGraphic } from '../lib/chart/volumeProfile';
import { alignComparisonSeries } from '../lib/chart/comparison';
import {
  createDrawing, drawingsToGraphic, hitTestDrawings, timeToFractionalIndex,
} from '../lib/chart/drawings';
import { useChartDrawings } from '../hooks/useChartDrawings';

const TF_CONFIGS = [
  { label: '1m',  secs: 60    },
  { label: '5m',  secs: 300   },
  { label: '15m', secs: 900   },
  { label: '1H',  secs: 3600  },
  { label: '4H',  secs: 14400 },
  { label: '1D',  secs: 86400 },
];

/** Default visible bars; grows when user scrolls into archived history */
const CHART_DISPLAY_BARS = CHART_DISPLAY_BARS_DEFAULT;
const CHART_DISPLAY_MAX = CHART_DISPLAY_MAX_BARS;
const ARCHIVE_LOAD_CHUNK = 1000;
const ARCHIVE_1M_RETENTION_SEC = 90 * 86400;
const FUTURE_PADDING = 15;
/** Wait for bulk history before first paint (avoids 1-bar → full-history jump). */
const CHART_HISTORY_MIN_BARS = 3;
const CHART_HISTORY_CACHED_BARS = 20;
const MASSIVE_CHART_MIN_BARS = 50;
const CHART_HISTORY_GATE_MS = 4000;
const LOAD_OLDER_MIN_INTERVAL_MS = 2000;
const CONFIGURE_DEBOUNCE_MS = 80;
const CHART_VISIBLE_BARS = 50;

function sliceRawForTimeframe(raw, intervalSecs, displayLimit) {
  if (!raw.length) return raw;
  const barsPerBucket = Math.max(1, Math.ceil(intervalSecs / 60));
  const tail = Math.min(raw.length, (displayLimit + 4) * barsPerBucket + 32);
  return tail < raw.length ? raw.slice(-tail) : raw;
}

function chartStructureKey(chartType, subPanes, showBacktestEquity) {
  return `${chartType}|${subPanes.join(',')}|${showBacktestEquity ? 1 : 0}`;
}

const SERIES_ANIM_OFF = {
  animation: false,
  animationDuration: 0,
  animationDurationUpdate: 0,
};

function isChartHistoryReady(barCount, historyRev, gateForced, terminalMode, useNativeHt = false) {
  if (barCount <= 0) return false;
  if (terminalMode === 'LIVE_MASSIVE' && useNativeHt) {
    if (barCount >= CHART_READY_MIN_BARS) return true;
    if (gateForced && barCount >= CHART_HISTORY_MIN_BARS) return true;
    return false;
  }
  if (terminalMode === 'LIVE_MASSIVE') {
    if (barCount >= MASSIVE_CHART_MIN_BARS) return true;
    if (gateForced && barCount >= CHART_HISTORY_MIN_BARS) return true;
    return false;
  }
  if (gateForced) return true;
  if (barCount >= CHART_HISTORY_CACHED_BARS) return true;
  return historyRev > 0 && barCount >= CHART_HISTORY_MIN_BARS;
}

function withSeriesAnimOff(series) {
  return { ...series, ...SERIES_ANIM_OFF };
}

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
  const liveGap = Math.min(5, FUTURE_PADDING);
  const endPct = Math.min(100, ((candleCount + liveGap) / totalCategoryCount) * 100);
  const startPct = Math.max(0, ((candleCount - visibleBars) / totalCategoryCount) * 100);
  return { start: startPct, end: endPct };
}

function dataZoomEndIndex(dz, categoryData) {
  if (!dz || !categoryData.length) return -1;
  if (dz.endValue != null) {
    const idx = indexOfCategoryKey(categoryData, dz.endValue);
    if (idx >= 0) return idx;
  }
  if (typeof dz.end === 'number') {
    return Math.round((dz.end / 100) * categoryData.length);
  }
  return -1;
}

/** True when the viewport right edge is at or past the last real candle. */
function isDataZoomAtLiveEdge(dz, categoryData) {
  if (!dz || !categoryData.length) return true;
  const realEnd = lastRealCategoryIndex(categoryData);
  const endIdx = dataZoomEndIndex(dz, categoryData);
  if (endIdx < 0) return true;
  return endIdx >= realEnd - 1;
}

function liveEdgeDataZoomForBars(barCount, categoryData, visibleBars = CHART_VISIBLE_BARS) {
  return defaultDataZoomPercent(barCount, categoryData.length, visibleBars);
}

function buildDataZoomOption(start, end) {
  return [
    { type: 'inside', start, end },
    { type: 'slider', start, end },
  ];
}

function preserveDataZoomPercent(prevCategoryData, nextCategoryData, prevDz, candleCount, nextCandleCount) {
  if (prevDz?.start == null || prevDz?.end == null) return null;

  const wasAtEnd = isDataZoomAtLiveEdge(prevDz, prevCategoryData);
  const diff = nextCategoryData.length - prevCategoryData.length;

  if (diff !== 0 && wasAtEnd) {
    return liveEdgeDataZoomForBars(nextCandleCount, nextCategoryData);
  }

  return { start: prevDz.start, end: prevDz.end };
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

function buildVolumeSeriesData(bars, indicatorTheme) {
  const data = bars.map(c => volumeSeriesEntry(c, indicatorTheme));
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

  let open = null;
  let high = null;
  let low = null;
  let close = null;
  let volume = 0;
  let found = false;

  for (let i = raw.length - 1; i >= 0; i--) {
    const c = raw[i];
    const sec = toUnixSeconds(c.time);
    if (sec == null) continue;
    const bt = Math.floor(sec / cfg.secs) * cfg.secs;
    if (bt < t) break;
    if (bt !== t) continue;
    if (!found) {
      open = c.open;
      high = c.high;
      low = c.low;
      close = c.close;
      volume = c.volume || 0;
      found = true;
      continue;
    }
    open = c.open;
    high = Math.max(high, c.high);
    low = Math.min(low, c.low);
    volume += c.volume || 0;
  }

  if (!found) return null;
  return { time: t, open, high, low, close, volume };
}

function barMatches(a, b) {
  if (!a || !b) return false;
  return a.time === b.time
    && a.open === b.open
    && a.high === b.high
    && a.low === b.low
    && a.close === b.close
    && (a.volume || 0) === (b.volume || 0);
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

function volumeSeriesEntry(bar, indicatorTheme) {
  return volumeBarEntry(bar, indicatorTheme);
}

/** Aggregate a symbol's candles to the active timeframe (shared by primary + comparison). */
function aggregateCandlesForSymbol(symbol, cfg, limit, useNativeHt) {
  if (!symbol) return [];
  if (useNativeHt) {
    const native = getCandles(symbol, cfg.label, cfg.secs);
    if (native.length > 0) {
      return native.length > limit ? native.slice(-limit) : native;
    }
    const raw = getCandles(symbol, '1m', 60);
    if (!raw.length) return [];
    const rawSlice = sliceRawForTimeframe(raw, cfg.secs, limit);
    const fallback = bucketCandles(rawSlice, cfg.secs);
    return fallback.length > limit ? fallback.slice(-limit) : fallback;
  }
  const raw = getCandles(symbol, '1m', 60);
  if (!raw.length) return [];
  const rawSlice = sliceRawForTimeframe(raw, cfg.secs, limit);
  const series = bucketCandles(rawSlice, cfg.secs);
  return series.length > limit ? series.slice(-limit) : series;
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

function mapMacdSeries(candles, indicatorTheme) {
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
        itemStyle: { color: macdHistogramColor(item.value, indicatorTheme) },
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
  return padIndicatorValues(buildVwapSeriesValues(candles));
}

/** Fast live patch: price (+ volume) only — indicators update on new bar / full rebuild. */
function updateLiveSeriesCache(cache, bars, chartType, active, indicatorTheme, { forceRebuild = false } = {}) {
  const barCount = bars.length;
  const needRebuild = forceRebuild
    || !cache.main
    || cache.barCount !== barCount
    || cache.chartType !== chartType;

  if (needRebuild) {
    cache.barCount = barCount;
    cache.chartType = chartType;
    cache.main = buildMainSeriesData(bars, chartType);
    cache.volume = active.volume ? buildVolumeSeriesData(bars, indicatorTheme) : null;
    return;
  }

  const idx = barCount - 1;
  const bar = bars[idx];
  const nextMain = cache.main.slice();
  if (chartType === 'line') {
    nextMain[idx] = bar.close;
  } else {
    nextMain[idx] = [bar.open, bar.close, bar.low, bar.high];
  }
  cache.main = nextMain;
  if (active.volume) {
    if (!cache.volume) {
      cache.volume = buildVolumeSeriesData(bars, indicatorTheme);
    } else {
      const nextVol = cache.volume.slice();
      nextVol[idx] = volumeSeriesEntry(bar, indicatorTheme);
      cache.volume = nextVol;
    }
  }
}

function buildLightLiveSeriesPatchesFromCache(cache, chartType, active) {
  const patches = [
    {
      id: 'main',
      type: chartType === 'line' ? 'line' : 'candlestick',
      data: cache.main,
      ...SERIES_ANIM_OFF,
    },
  ];
  if (active.volume && cache.volume) {
    patches.push({
      id: 'volume',
      data: cache.volume,
      barCategoryGap: '30%',
      ...SERIES_ANIM_OFF,
    });
  }
  return patches;
}

/** Merge live price/volume patches with full indicator recomputation on new bar. */
function buildNewBarSeriesPatches(bars, chartType, active, indicatorTheme, cache) {
  updateLiveSeriesCache(cache, bars, chartType, active, indicatorTheme, { forceRebuild: true });
  const liveIds = new Set(['main', 'volume']);
  const live = buildLightLiveSeriesPatchesFromCache(cache, chartType, active);
  const indicators = buildIndicatorSeriesPatches(bars, active, indicatorTheme)
    .filter((p) => !liveIds.has(p.id));
  return [...live, ...indicators];
}

/** Series patches for live candle updates — keeps sub-panes in sync with price/volume. */
function buildIndicatorSeriesPatches(bars, active, indicatorTheme) {
  const patches = [];
  if (active.ema9) patches.push({ id: 'ema9', data: mapEmaSeries(bars, 9), ...SERIES_ANIM_OFF });
  if (active.ema21) patches.push({ id: 'ema21', data: mapEmaSeries(bars, 21), ...SERIES_ANIM_OFF });
  if (active.ema50) patches.push({ id: 'ema50', data: mapEmaSeries(bars, 50), ...SERIES_ANIM_OFF });
  if (active.bb) {
    const bb = mapBbSeries(bars);
    patches.push({ id: 'bb-upper', data: bb.upper, ...SERIES_ANIM_OFF });
    patches.push({ id: 'bb-mid', data: bb.middle, ...SERIES_ANIM_OFF });
    patches.push({ id: 'bb-lower', data: bb.lower, ...SERIES_ANIM_OFF });
  }
  if (active.vwap) patches.push({ id: 'vwap', data: mapVwapSeries(bars), ...SERIES_ANIM_OFF });
  if (active.volume) patches.push({ id: 'volume', data: buildVolumeSeriesData(bars, indicatorTheme), ...SERIES_ANIM_OFF });
  if (active.rsi) patches.push({ id: 'rsi', data: mapRsiSeries(bars), ...SERIES_ANIM_OFF });
  if (active.macd) {
    const m = mapMacdSeries(bars, indicatorTheme);
    patches.push({ id: 'macd', data: m.macd, ...SERIES_ANIM_OFF });
    patches.push({ id: 'macd-signal', data: m.signal, ...SERIES_ANIM_OFF });
    patches.push({ id: 'macd-hist', data: m.hist, ...SERIES_ANIM_OFF });
  }
  if (active.atr) patches.push({ id: 'atr', data: mapAtrSeries(bars), ...SERIES_ANIM_OFF });
  return patches;
}

function buildAgentMarkLines(insight, lastClose, dec) {
  if (!insight?.levels || !lastClose) return [];
  const lines = [];
  const slDist = insight.levels.stop_loss_distance;
  const tp = insight.levels.take_profit_price;
  const signal = insight.signal;

  if (slDist > 0 && signal === 'BUY') {
    lines.push({
      yAxis: lastClose - slDist,
      lineStyle: { color: '#f59e0b', width: 1, type: 'dashed' },
      label: { show: true, position: 'end', formatter: `Agent SL: ${(lastClose - slDist).toFixed(dec)}` },
    });
  } else if (slDist > 0 && signal === 'SELL') {
    lines.push({
      yAxis: lastClose + slDist,
      lineStyle: { color: '#f59e0b', width: 1, type: 'dashed' },
      label: { show: true, position: 'end', formatter: `Agent SL: ${(lastClose + slDist).toFixed(dec)}` },
    });
  }
  if (tp > 0) {
    lines.push({
      yAxis: tp,
      lineStyle: { color: '#fbbf24', width: 1, type: 'dotted' },
      label: { show: true, position: 'end', formatter: `Agent TP: ${tp.toFixed(dec)}` },
    });
  }
  return lines;
}

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

function buildBacktestTradeMarkers(backtestTrades, candles, bucketSecs) {
  if (!backtestTrades?.length || !candles.length) return [];
  return backtestTrades.map((t) => {
    const isExit = t.is_exit === 1 || t.is_exit === true;
    const tsSec = t.time != null ? Number(t.time) : null;
    if (tsSec == null) return null;
    const idx = findBarIndexForBarTime(candles, tsSec, bucketSecs);
    if (idx < 0) return null;
    const candle = candles[idx];
    return toSignalScatterPoint(candles, idx, markerYForTrade(candle, t.side, { isExit, fillPrice: t.price }), {
      value: `BT ${t.side}${isExit ? ` ${t.reason ?? ''}` : ' entry'}`,
      symbol: isExit ? 'pin' : 'path://M0,10 L5,0 L10,10 Z',
      symbolSize: isExit ? 11 : 9,
      itemStyle: {
        color: isExit
          ? ((t.pnl ?? 0) >= 0 ? '#f59e0b' : '#ef4444')
          : '#60a5fa',
        borderColor: '#1e3a5f',
        borderWidth: 1,
      },
    });
  }).filter(Boolean);
}

function mapBacktestEquityLine(equityCurve, candles) {
  if (!equityCurve?.length || !candles.length) return [];
  const data = new Array(candles.length).fill(null);
  let ei = 0;
  const startTs = toUnixSeconds(equityCurve[0]?.time);
  for (let i = 0; i < candles.length; i++) {
    const t = toUnixSeconds(candles[i].time);
    if (t == null || startTs == null || t < startTs) continue;
    while (ei < equityCurve.length - 1 && toUnixSeconds(equityCurve[ei + 1].time) <= t) {
      ei += 1;
    }
    data[i] = equityCurve[ei]?.equity ?? null;
  }
  return data;
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

// ─── Main Component ──────────────────────────────────────────────────
export default function ChartWidget() {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const candlesRef = useRef([]);
  const displayBarsRef = useRef([]);
  const chartLayoutRef = useRef({ xAxisCount: 1, showVolume: true });
  const liveRafRef = useRef(null);
  const liveLastPaintMs = useRef(0);
  const LIVE_MIN_INTERVAL_MS = 250;
  const DATAZOOM_HANDLER_MIN_MS = 400;
  const configureChartRef = useRef(() => {});
  const applyOverlayPatchRef = useRef(() => {});
  const chartReadyRef = useRef(false);
  const chartConfiguringRef = useRef(false);
  const configureDebounceRef = useRef(null);
  const prevStructureKeyRef = useRef('');
  const suppressDataZoomEventsRef = useRef(0);
  const loadOlderLastMsRef = useRef(0);
  const loadingOlderRef = useRef(false);
  const olderExhaustedRef = useRef({});
  const loadOlderRef = useRef(null);
  const pinnedToLiveRef = useRef(true);
  const liveSeriesCacheRef = useRef({ main: null, volume: null, barCount: 0, chartType: null });
  const renkoBrickSizeRef = useRef(0);
  const dataZoomHandlerLastMsRef = useRef(0);
  const lastConfigureRevisionRef = useRef('');
  const chartHistoryReadyRef = useRef(false);
  const htFetchRef = useRef(null);

  const [displayBarLimit, setDisplayBarLimit] = useState(CHART_DISPLAY_BARS);
  const [historyGateForced, setHistoryGateForced] = useState(false);
  const settings = useSettingsStore(state => state.settings);
  const resolvedTheme = useSettingsStore(state => state.resolvedTheme);
  const [timeframe, setTimeframe] = useState(() => settings.chartLayout?.timeframe || '1m');

  const activeSymbol = useStore(state => state.activeSymbol);
  const terminalMode = useStore(state => state.terminalMode);
  const useNativeHt = isLiveMassiveMode(terminalMode) && isHigherTimeframe(timeframe);
  const chartBufKey = useNativeHt ? candleBufferKey(activeSymbol, timeframe) : activeSymbol;
  const historyRev = useStore(state => state.candleHistoryRevision[chartBufKey] || 0);
  const candleRev = useStore(state => state.candleRevision[chartBufKey] || 0);
  const oneMinRev = useStore(state => state.candleRevision[activeSymbol] || 0);
  const lastCandleTime = useMemo(() => {
    const intervalSecs = chartTimeframeSecs(timeframe);
    const candles = getCandles(activeSymbol, useNativeHt ? timeframe : '1m', intervalSecs);
    return candles.length > 0 ? candles[candles.length - 1].time : 0;
  }, [activeSymbol, candleRev, oneMinRev, timeframe, useNativeHt]);
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
  const agentInsights = useStore(state => state.agentInsights);
  const agentInsight = useMemo(
    () => selectAgentInsight(agentInsights, activeSymbol, timeframe),
    [agentInsights, activeSymbol, timeframe],
  );
  const setBotStrategy = useStore(state => state.setBotStrategy);
  const setBotExecutionMode = useStore(state => state.setBotExecutionMode);
  const setBotTimeframe = useStore(state => state.setBotTimeframe);
  const updateBotConfig = useStore(state => state.updateBotConfig);
  const agentOverlayKey = useMemo(() => {
    if (!agentInsight) return '';
    const lv = agentInsight.levels || {};
    return `${agentInsight.bar_time}|${agentInsight.signal}|${lv.stop_loss_distance}|${lv.take_profit_price}`;
  }, [agentInsight]);
  const handleDeployChartAgent = useCallback(() => {
    setBotStrategy('CHART_AGENT');
    setBotExecutionMode('BAR_CLOSE');
    setBotTimeframe(timeframe);
    updateBotConfig({
      min_confidence: agentInsight?.confidence ?? 0.55,
      use_llm: false,
      allocation: 2000,
      trailing_stop_percent: 2,
      take_profit_percent: 3,
      tp_mode: 'percent',
    });
  }, [agentInsight, setBotStrategy, setBotExecutionMode, setBotTimeframe, timeframe, updateBotConfig]);
  const botOverlayKey = useStore(state => {
    if (!state.selectedBotId || !state.botDetail?.trades) return '';
    return state.botDetail.trades.map(
      (t) => `${t.id}:${t.signal_bar_time ?? ''}:${t.signal_id ?? ''}:${t.side}`,
    ).join(';');
  });
  const backtestOverlay = useStore(state => state.backtestOverlay);
  const backtestOverlayKey = useStore(state => {
    const o = state.backtestOverlay;
    if (!o) return '';
    return `${o.visible ? 1 : 0}:${o.runId ?? ''}:${o.trades?.length ?? 0}:${o.symbol ?? ''}:${o.equityCurve?.length ?? 0}`;
  });
  const chartInteractionMode = useStore(state => state.chartInteractionMode);
  const setChartInteractionMode = useStore(state => state.setChartInteractionMode);

  const zenMode = useSettingsStore(state => state.settings.workspace?.zenMode ?? false);
  const updateChartLayout = useSettingsStore(state => state.updateChartLayout);
  const chartTheme = useMemo(
    () => getChartEchartsTheme(settings, resolvedTheme),
    [settings, resolvedTheme],
  );
  const indicatorTheme = useMemo(
    () => getIndicatorTheme(resolvedTheme),
    [resolvedTheme],
  );
  const indicatorToolbar = useMemo(
    () => getIndicatorToolbarMeta(indicatorTheme),
    [indicatorTheme],
  );

  const prevConfigRef = useRef({ symbol: activeSymbol, timeframe: timeframe });
  const layoutPushSkipRef = useRef(false);

  const chartLayoutFromSettings = settings.chartLayout;

  useEffect(() => {
    setHistoryGateForced(false);
    pinnedToLiveRef.current = true;
    prevStructureKeyRef.current = '';
    lastConfigureRevisionRef.current = '';
    const t = setTimeout(() => setHistoryGateForced(true), CHART_HISTORY_GATE_MS);
    return () => clearTimeout(t);
  }, [activeSymbol]);

  useEffect(() => {
    pinnedToLiveRef.current = true;
    prevStructureKeyRef.current = '';
    lastConfigureRevisionRef.current = '';
  }, [timeframe]);

  useEffect(() => {
    setDisplayBarLimit(CHART_DISPLAY_BARS);
    olderExhaustedRef.current[activeSymbol] = false;
  }, [activeSymbol, timeframe]);
  const [chartType, setChartType] = useState(() => settings.chartLayout?.chartType || 'candle');
  const [active, setActive] = useState(() => ({
    ...DEFAULT_TERMINAL_SETTINGS.chartLayout.activeIndicators,
    ...(settings.chartLayout?.activeIndicators || {}),
  }));

  // Live mirrors of local chart state — let the settings→chart sync compare
  // against the latest values without depending on them (which would revert
  // header-toggle changes before they propagate to settings).
  const timeframeRef = useRef(timeframe);
  const chartTypeRef = useRef(chartType);
  const activeRef = useRef(active);
  timeframeRef.current = timeframe;
  chartTypeRef.current = chartType;
  activeRef.current = active;

  // ── Volume Profile (VPVR) + drawing tools ──
  const [showVolumeProfile, setShowVolumeProfile] = useState(
    () => Boolean(settings.chartLayout?.volumeProfile),
  );
  const {
    drawings,
    activeTool,
    setActiveTool,
    selectedId: selectedDrawingId,
    setSelectedId: setSelectedDrawingId,
    addDrawing,
    removeDrawing,
    clearDrawings,
  } = useChartDrawings(activeSymbol);

  // ── Comparison mode (overlay a second symbol, rebased %) ──
  const [compareSymbol, setCompareSymbol] = useState(null);
  const compareSymbolRef = useRef(compareSymbol);
  compareSymbolRef.current = compareSymbol;
  // Reactive to the *set* of available symbols (not per-tick price changes).
  const compareSymbolsKey = useStore(
    (s) => Object.keys(s.tickerData || {}).sort().join(','),
  );
  const compareOptions = useMemo(
    () => (compareSymbolsKey ? compareSymbolsKey.split(',') : [])
      .filter((s) => s && s !== activeSymbol),
    [compareSymbolsKey, activeSymbol],
  );

  // ── Replay mode (bar-by-bar historical playback) ──
  const [replayActive, setReplayActive] = useState(false);
  const [replayIndex, setReplayIndex] = useState(0);
  const [replayPlaying, setReplayPlaying] = useState(false);
  const [replaySpeed, setReplaySpeed] = useState(1);
  const replayActiveRef = useRef(replayActive);
  replayActiveRef.current = replayActive;

  const showVolumeProfileRef = useRef(showVolumeProfile);
  const drawingsRef = useRef(drawings);
  const activeToolRef = useRef(activeTool);
  const selectedDrawingIdRef = useRef(selectedDrawingId);
  const addDrawingRef = useRef(addDrawing);
  const pendingDrawPointRef = useRef(null);
  const overlayIdsRef = useRef(new Set());
  showVolumeProfileRef.current = showVolumeProfile;
  drawingsRef.current = drawings;
  activeToolRef.current = activeTool;
  selectedDrawingIdRef.current = selectedDrawingId;
  addDrawingRef.current = addDrawing;

  useEffect(() => {
    lastConfigureRevisionRef.current = '';
  }, [activeSymbol, timeframe, active]);


  useEffect(() => { try { localStorage.setItem('terminal_tf', timeframe); } catch {} }, [timeframe]);

  // LIVE_MASSIVE: lazy-fetch native HT history from Massive REST on TF switch
  useEffect(() => {
    if (!useNativeHt || !activeSymbol) return;
    if (hasChartReadyHistory(activeSymbol, CHART_READY_MIN_BARS, timeframe)) return;

    const fetchKey = `${activeSymbol}|${timeframe}`;
    if (htFetchRef.current === fetchKey) return;
    htFetchRef.current = fetchKey;

    fetchCandles(activeSymbol, getStoreActions(), {
      limit: CHART_SNAPSHOT_BARS,
      interval: timeframe,
      timeoutMs: 25000,
    }).catch((err) => {
      console.warn('[ChartWidget] HT candle fetch failed:', err?.message || err);
    }).finally(() => {
      if (htFetchRef.current === fetchKey) htFetchRef.current = null;
    });

    if (!hasChartReadyHistory(activeSymbol, CHART_READY_MIN_BARS, '1m')) {
      fetchCandles(activeSymbol, getStoreActions(), {
        limit: 120,
        interval: '1m',
        timeoutMs: 15000,
      }).catch(() => {});
    }
  }, [activeSymbol, timeframe, useNativeHt, historyRev]);

  useEffect(() => { try { localStorage.setItem('terminal_chart_type', chartType); } catch {} }, [chartType]);
  useEffect(() => { try { localStorage.setItem('terminal_chart_indicators_active', JSON.stringify(active)); } catch {} }, [active]);

  useEffect(() => {
    const cl = chartLayoutFromSettings;
    if (!cl) return;

    let pulled = false;
    if (cl.timeframe && cl.timeframe !== timeframeRef.current) {
      setTimeframe(cl.timeframe);
      pulled = true;
    }
    if (cl.chartType && cl.chartType !== chartTypeRef.current) {
      setChartType(cl.chartType);
      pulled = true;
    }
    if (cl.activeIndicators) {
      const keys = Object.keys(indicatorToolbar);
      const differs = keys.some((k) => Boolean(cl.activeIndicators[k]) !== Boolean(activeRef.current[k]));
      if (differs) {
        setActive((prev) => ({ ...prev, ...cl.activeIndicators }));
        pulled = true;
      }
    }
    if (pulled) layoutPushSkipRef.current = true;
  }, [
    chartLayoutFromSettings?.timeframe,
    chartLayoutFromSettings?.chartType,
    chartLayoutFromSettings?.activeIndicators,
    indicatorToolbar,
  ]);

  useEffect(() => {
    if (layoutPushSkipRef.current) {
      layoutPushSkipRef.current = false;
      return;
    }
    updateChartLayout({ timeframe, chartType, activeIndicators: active });
  }, [timeframe, chartType, active, updateChartLayout]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const id = requestAnimationFrame(() => {
      requestAnimationFrame(() => chart.resize());
    });
    return () => cancelAnimationFrame(id);
  }, [zenMode]);

  useEffect(() => {
    const onReset = (e) => {
      const cl = e.detail?.chartLayout ?? DEFAULT_TERMINAL_SETTINGS.chartLayout;
      layoutPushSkipRef.current = true;
      setTimeframe(cl.timeframe);
      setChartType(cl.chartType);
      setActive({ ...cl.activeIndicators });
      chartReadyRef.current = false;
      try { chartRef.current?.clear(); } catch (_) {}
    };
    window.addEventListener(CHART_LAYOUT_RESET_EVENT, onReset);
    return () => window.removeEventListener(CHART_LAYOUT_RESET_EVENT, onReset);
  }, []);

  useEffect(() => {
    const onCaptureRequest = (e) => {
      const sym = e.detail?.symbol;
      if (sym && sym !== activeSymbol) return;
      const captureTfRaw = (e.detail?.timeframe || '').toLowerCase();
      const captureChartTf = captureTfRaw === '1h' ? '1H' : captureTfRaw === '4h' ? '4H' : null;

      const emitCapture = () => {
        const chart = chartRef.current;
        if (!chart) return;
        try {
          const image = chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#0a0a0a' });
          window.dispatchEvent(new CustomEvent('chart-capture-ready', {
            detail: { symbol: activeSymbol, image, bar_time: e.detail?.bar_time },
          }));
        } catch (_) {}
      };

      if (captureChartTf && captureChartTf !== timeframeRef.current) {
        const prevTf = timeframeRef.current;
        setTimeframe(captureChartTf);
        window.setTimeout(() => {
          emitCapture();
          setTimeframe(prevTf);
        }, 500);
        return;
      }
      emitCapture();
    };
    window.addEventListener('chart-capture-request', onCaptureRequest);
    return () => window.removeEventListener('chart-capture-request', onCaptureRequest);
  }, [activeSymbol]);

  const activeIndicatorKeys = useMemo(
    () => Object.entries(active).filter(([, on]) => on).map(([k]) => k),
    [active]
  );

  const handleIndicatorsChange = useCallback((vals) => {
    setActive(prev => {
      const next = { ...prev };
      for (const k of Object.keys(indicatorToolbar)) next[k] = vals.includes(k);
      return next;
    });
  }, [indicatorToolbar]);

  // Aggregate candles based on timeframe; chart renders a rolling window only
  const aggregatedCandles = useMemo(() => {
    const cfg = TF_CONFIGS.find(t => t.label === timeframe) || TF_CONFIGS[0];
    const limit = displayBarLimit;

    if (useNativeHt) {
      const native = getCandles(activeSymbol, timeframe, cfg.secs);
      if (native.length > 0) {
        return native.length > limit ? native.slice(-limit) : native;
      }
      // Stale-while-revalidate: show bucketed 1m until native HT REST returns
      const raw = getCandles(activeSymbol, '1m', 60);
      if (!raw.length) return [];
      const rawSlice = sliceRawForTimeframe(raw, cfg.secs, limit);
      const fallback = bucketCandles(rawSlice, cfg.secs);
      return fallback.length > limit ? fallback.slice(-limit) : fallback;
    }

    const raw = getCandles(activeSymbol, '1m', 60);
    if (!raw.length) return [];

    const rawSlice = sliceRawForTimeframe(raw, cfg.secs, limit);
    const series = bucketCandles(rawSlice, cfg.secs);
    return series.length > limit ? series.slice(-limit) : series;
  }, [timeframe, activeSymbol, historyRev, displayBarLimit, useNativeHt]);

  // Comparison-symbol candle revision (the active-symbol historyRev won't fire
  // when only the comparison symbol's data arrives).
  const compareRev = useStore((state) => {
    if (!compareSymbol) return 0;
    return (state.candleHistoryRevision[compareSymbol] || 0)
      + (state.candleRevision[compareSymbol] || 0);
  });

  // Comparison-symbol candles aggregated to the active timeframe.
  // Bumped after comparison history is (re)loaded so the memo recomputes even
  // when neither the active-symbol nor compare-symbol live revisions change.
  const [compareLoadRev, setCompareLoadRev] = useState(0);

  const compareCandles = useMemo(() => {
    if (!compareSymbol) return [];
    const cfg = TF_CONFIGS.find(t => t.label === timeframe) || TF_CONFIGS[0];
    return aggregateCandlesForSymbol(compareSymbol, cfg, displayBarLimit, useNativeHt);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compareSymbol, timeframe, historyRev, compareRev, compareLoadRev, displayBarLimit, useNativeHt]);

  // Load comparison-symbol history when selected. The generic history merge
  // rejects an archived snapshot older than a pre-existing live bar (which the
  // compare symbol usually already has from the ticker feed), so replace the
  // buffer directly. Pin it so the LRU cache doesn't evict it (only the active
  // chart symbol is pinned by default).
  useEffect(() => {
    setComparePinnedCandleSymbol(compareSymbol);
    if (!compareSymbol) return undefined;
    const interval = useNativeHt ? timeframe : '1m';
    fetchCandles(compareSymbol, getStoreActions(), {
      limit: CHART_SNAPSHOT_BARS,
      interval,
    }).then((body) => {
      const bars = body?.data?.[compareSymbol];
      if (Array.isArray(bars) && bars.length > 1) {
        setCandleHistory(compareSymbol, bars, interval, chartTimeframeSecs(interval));
        setCompareLoadRev((n) => n + 1);
      }
    }).catch(() => {});
    return () => setComparePinnedCandleSymbol(null);
  }, [compareSymbol, timeframe, useNativeHt]);

  // During replay, only reveal bars up to the replay cursor.
  const effectiveCandles = useMemo(() => {
    if (!replayActive) return aggregatedCandles;
    const end = Math.min(replayIndex, aggregatedCandles.length);
    return aggregatedCandles.slice(0, Math.max(1, end));
  }, [replayActive, replayIndex, aggregatedCandles]);

  // Stable Renko brick size per symbol/timeframe so bricks don't rescale on every tick.
  useEffect(() => {
    if (chartType === 'renko') {
      renkoBrickSizeRef.current = estimateRenkoBrickSize(aggregatedCandles);
    } else {
      renkoBrickSizeRef.current = 0;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartType, activeSymbol, timeframe, aggregatedCandles.length > 0]);

  const nativeHtLoaded = useNativeHt && hasCandleHistory(activeSymbol, timeframe);

  const chartHistoryReady = useMemo(
    () => isChartHistoryReady(
      aggregatedCandles.length,
      historyRev,
      historyGateForced,
      terminalMode,
      useNativeHt,
    ),
    [aggregatedCandles.length, historyRev, historyGateForced, terminalMode, useNativeHt],
  );

  chartHistoryReadyRef.current = chartHistoryReady;

  const configureRevision = useMemo(() => [
    activeSymbol,
    timeframe,
    chartType,
    historyRev,
    displayBarLimit,
    chartHistoryReady ? 1 : 0,
    activeIndicatorKeys.join(','),
    backtestOverlayKey,
    resolvedTheme,
    replayActive ? `replay:${replayIndex}` : 'live',
    compareSymbol ? `cmp:${compareSymbol}:${compareCandles.length}` : 'nocmp',
  ].join('|'), [
    activeSymbol, timeframe, chartType, historyRev, displayBarLimit, chartHistoryReady,
    activeIndicatorKeys, backtestOverlayKey, resolvedTheme, replayActive, replayIndex,
    compareSymbol, compareCandles.length,
  ]);

  const displayBarsSyncKey = useMemo(() => {
    const last = aggregatedCandles[aggregatedCandles.length - 1];
    return [
      activeSymbol,
      timeframe,
      historyRev,
      displayBarLimit,
      useNativeHt ? 1 : 0,
      aggregatedCandles.length,
      last?.time ?? 0,
    ].join('|');
  }, [
    activeSymbol,
    timeframe,
    historyRev,
    displayBarLimit,
    useNativeHt,
    aggregatedCandles.length,
    aggregatedCandles[aggregatedCandles.length - 1]?.time,
  ]);

  // Sync display buffer on structural changes only — live OHLC patches use applyLiveCandleUpdate.
  useEffect(() => {
    const prev = displayBarsRef.current;
    displayBarsRef.current = effectiveCandles.map(c => ({ ...c }));
    candlesRef.current = displayBarsRef.current;
    const next = displayBarsRef.current;
    const barCountChanged = prev.length !== next.length;
    const lastTimeChanged = prev.length > 0 && next.length > 0
      && prev[prev.length - 1].time !== next[next.length - 1].time;
    if (barCountChanged || lastTimeChanged || !liveSeriesCacheRef.current.main) {
      liveSeriesCacheRef.current = { main: null, volume: null, barCount: 0, chartType: null };
    }
  }, [displayBarsSyncKey, effectiveCandles, replayIndex]);

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
    if (!chartRef.current || aggregatedCandles.length === 0 || !chartHistoryReady) {
      chartReadyRef.current = false;
      return;
    }

    chartConfiguringRef.current = true;
    chartReadyRef.current = false;
    if (liveRafRef.current != null) {
      cancelAnimationFrame(liveRafRef.current);
      liveRafRef.current = null;
    }

    const candles = displayBarsRef.current.length > 0
      ? displayBarsRef.current
      : aggregatedCandles;
    const dec = getPriceDecimals(candles[candles.length - 1]?.close);

    const categoryData = buildCategoryAxisData(candles);

    const layoutChanged = prevConfigRef.current.symbol !== activeSymbol
      || prevConfigRef.current.timeframe !== timeframe;

    // Preserve zoom (% window) — skip getOption when symbol/timeframe changed (expensive + stale)
    let zoomStart = null;
    let zoomEnd = null;

    if (!layoutChanged && chartRef.current) {
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
      ({ start: zoomStart, end: zoomEnd } = liveEdgeDataZoomForBars(candles.length, categoryData));
      pinnedToLiveRef.current = true;
    } else {
      pinnedToLiveRef.current = isDataZoomAtLiveEdge(
        { start: zoomStart, end: zoomEnd },
        categoryData,
      );
    }
    prevConfigRef.current = { symbol: activeSymbol, timeframe: timeframe };

    // Heikin-Ashi / Renko render with transformed OHLC on the same category axis;
    // volume and indicators continue to use the raw candles below.
    const mainCandles = applyCandleTransform(candles, chartType, {
      renkoBrickSize: renkoBrickSizeRef.current,
    });
    const candlestickData = mainCandles.map(c => [c.open, c.close, c.low, c.high]);
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
      id: 'price',
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

    const showBacktestEquity = backtestOverlay?.visible
      && symbolsMatch(backtestOverlay.symbol, activeSymbol)
      && backtestOverlay.equityCurve?.length;

    const showCompare = Boolean(compareSymbol) && compareCandles.length > 1;

    const structureKey = `${chartStructureKey(chartType, subPanes, Boolean(showBacktestEquity))}|cmp:${showCompare ? 1 : 0}`;
    const fullReplace = structureKey !== prevStructureKeyRef.current;
    prevStructureKeyRef.current = structureKey;

    yAxes.push({
      id: 'backtest-equity-axis',
      scale: true,
      gridIndex: 0,
      position: 'left',
      show: Boolean(showBacktestEquity),
      splitLine: { show: false },
      axisLine: { show: false },
      axisLabel: {
        show: Boolean(showBacktestEquity),
        color: '#60a5fa',
        fontSize: 9,
        formatter: (val) => (val >= 1000 ? `$${(val / 1000).toFixed(1)}k` : `$${Number(val).toFixed(0)}`),
      },
    });

    // Comparison overlay axis (percent change, left side).
    let compareAxisIndex = -1;
    if (showCompare) {
      compareAxisIndex = yAxes.length;
      yAxes.push({
        id: 'compare-axis',
        scale: true,
        gridIndex: 0,
        position: 'left',
        offset: showBacktestEquity ? 38 : 0,
        splitLine: { show: false },
        axisLine: { show: true, lineStyle: { color: '#f472b6' } },
        axisLabel: {
          color: '#f472b6',
          fontSize: 9,
          formatter: (v) => `${v >= 0 ? '+' : ''}${Number(v).toFixed(1)}%`,
        },
      });
    }

    // Series
    const series = [];

    // Main Candlestick / Line Series
    if (chartType === 'line') {
      const lineData = mainCandles.map(c => c.close);
      for (let i = 0; i < FUTURE_PADDING; i++) lineData.push('-');
      series.push(withSeriesAnimOff({
        id: 'main',
        name: activeSymbol,
        type: 'line',
        data: lineData,
        xAxisIndex: 0,
        yAxisIndex: 0,
        showSymbol: false,
        lineStyle: { color: chartTheme.crosshairLabelBg, width: 2 },
      }));
    } else {
      series.push(withSeriesAnimOff({
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
      }));
    }

    // Comparison overlay — second symbol rebased to percent change.
    if (showCompare) {
      const pct = alignComparisonSeries(candles, compareCandles);
      const compareData = [...pct];
      for (let i = 0; i < FUTURE_PADDING; i++) compareData.push(null);
      series.push(withSeriesAnimOff({
        id: 'compare',
        name: `${compareSymbol} %`,
        type: 'line',
        data: compareData,
        xAxisIndex: 0,
        yAxisIndex: compareAxisIndex,
        showSymbol: false,
        connectNulls: true,
        lineStyle: { color: '#f472b6', width: 1.5, type: 'dashed' },
        z: 3,
      }));
    }

    // Signal markers — scatter layer shares the category x-axis (stable under zoom/pan)
    series.push(withSeriesAnimOff({
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
    }));

    const equityOverlayData = showBacktestEquity
      ? mapBacktestEquityLine(backtestOverlay.equityCurve, candles)
      : [];

    series.push(withSeriesAnimOff({
      id: 'backtest-equity',
      name: 'BT Equity',
      type: 'line',
      data: equityOverlayData,
      xAxisIndex: 0,
      yAxisId: 'backtest-equity-axis',
      showSymbol: false,
      silent: true,
      z: 2,
      lineStyle: { color: '#60a5fa', width: 1.5, type: 'dashed', opacity: 0.85 },
    }));

    // Overlay indicators
    if (active.ema9) {
      series.push(withSeriesAnimOff({
        id: 'ema9',
        name: 'EMA 9', type: 'line', data: mapEmaSeries(candles, 9), xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: emaLineStyle(9, indicatorTheme),
      }));
    }
    if (active.ema21) {
      series.push(withSeriesAnimOff({
        id: 'ema21',
        name: 'EMA 21', type: 'line', data: mapEmaSeries(candles, 21), xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: emaLineStyle(21, indicatorTheme),
      }));
    }
    if (active.ema50) {
      series.push(withSeriesAnimOff({
        id: 'ema50',
        name: 'EMA 50', type: 'line', data: mapEmaSeries(candles, 50), xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false, lineStyle: emaLineStyle(50, indicatorTheme),
      }));
    }
    if (active.bb) {
      const bb = mapBbSeries(candles);
      const { bb: bbTheme } = indicatorTheme;
      series.push(
        withSeriesAnimOff({
          id: 'bb-upper', name: 'BB Upper', type: 'line', data: bb.upper, xAxisIndex: 0, yAxisIndex: 0,
          showSymbol: false,
          lineStyle: { color: bbTheme.outer, width: 1, type: 'dashed', opacity: bbTheme.outerOpacity },
        }),
        withSeriesAnimOff({
          id: 'bb-mid', name: 'BB Mid', type: 'line', data: bb.middle, xAxisIndex: 0, yAxisIndex: 0,
          showSymbol: false,
          lineStyle: { color: bbTheme.basis, width: 1, opacity: bbTheme.basisOpacity },
        }),
        withSeriesAnimOff({
          id: 'bb-lower', name: 'BB Lower', type: 'line', data: bb.lower, xAxisIndex: 0, yAxisIndex: 0,
          showSymbol: false,
          lineStyle: { color: bbTheme.outer, width: 1, type: 'dashed', opacity: bbTheme.outerOpacity },
        }),
      );
    }
    if (active.vwap) {
      series.push(withSeriesAnimOff({
        id: 'vwap',
        name: 'VWAP', type: 'line', data: mapVwapSeries(candles), xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: false,
        connectNulls: false,
        lineStyle: {
          color: indicatorTheme.vwap.line,
          width: indicatorTheme.vwap.width,
          opacity: indicatorTheme.vwap.opacity,
        },
      }));
    }

    // Sub grids series
    if (showVol) {
      const gIdx = paneGridMap.volume;
      series.push(withSeriesAnimOff({
        id: 'volume',
        name: 'Volume',
        type: 'bar',
        xAxisIndex: gIdx,
        yAxisIndex: gIdx,
        barCategoryGap: '30%',
        data: buildVolumeSeriesData(candles, indicatorTheme),
      }));
    }

    if (showRsi) {
      const gIdx = paneGridMap.rsi;
      series.push(withSeriesAnimOff({
        id: 'rsi',
        name: 'RSI', type: 'line', data: mapRsiSeries(candles), xAxisIndex: gIdx, yAxisIndex: gIdx,
        showSymbol: false,
        lineStyle: {
          color: indicatorTheme.rsi.line,
          width: indicatorTheme.rsi.width,
        },
        markLine: rsiMarkLine(indicatorTheme),
      }));
    }

    if (showMacd) {
      const gIdx = paneGridMap.macd;
      const macd = mapMacdSeries(candles, indicatorTheme);
      const { macd: macdTheme } = indicatorTheme;
      series.push(
        withSeriesAnimOff({
          id: 'macd', name: 'MACD', type: 'line', data: macd.macd, xAxisIndex: gIdx, yAxisIndex: gIdx,
          showSymbol: false,
          lineStyle: { color: macdTheme.line, width: macdTheme.lineWidth },
          markLine: macdZeroMarkLine(indicatorTheme),
        }),
        withSeriesAnimOff({
          id: 'macd-signal', name: 'Signal', type: 'line', data: macd.signal, xAxisIndex: gIdx, yAxisIndex: gIdx,
          showSymbol: false,
          lineStyle: { color: macdTheme.signal, width: macdTheme.lineWidth },
        }),
        withSeriesAnimOff({
          id: 'macd-hist',
          name: 'Hist',
          type: 'bar',
          xAxisIndex: gIdx,
          yAxisIndex: gIdx,
          data: macd.hist,
        }),
      );
    }

    if (showAtr) {
      const gIdx = paneGridMap.atr;
      series.push(withSeriesAnimOff({
        id: 'atr',
        name: 'ATR', type: 'line', data: mapAtrSeries(candles), xAxisIndex: gIdx, yAxisIndex: gIdx,
        showSymbol: false,
        lineStyle: {
          color: indicatorTheme.atr.line,
          width: indicatorTheme.atr.width,
          opacity: indicatorTheme.atr.opacity,
        },
      }));
    }

    // Zoom and pan links
    const zoomXIndices = grids.map((_, i) => i);

    const option = {
      backgroundColor: chartTheme.backgroundColor,
      animation: false,
      animationDuration: 0,
      animationDurationUpdate: 0,
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

    suppressDataZoomEventsRef.current += 1;
    try {
      chartRef.current.setOption(
        option,
        fullReplace
          ? { notMerge: true }
          : { replaceMerge: ['grid', 'xAxis', 'yAxis', 'series', 'dataZoom'] },
      );
    } catch (err) {
      console.warn('[ChartWidget] configureChart setOption failed:', err);
    } finally {
      requestAnimationFrame(() => {
        suppressDataZoomEventsRef.current = Math.max(0, suppressDataZoomEventsRef.current - 1);
      });
      chartConfiguringRef.current = false;
    }

    chartLayoutRef.current = { xAxisCount: xAxes.length, showVolume: showVol };
    chartReadyRef.current = true;
    updateLiveSeriesCache(
      liveSeriesCacheRef.current,
      candles,
      chartType,
      active,
      indicatorTheme,
      { forceRebuild: true },
    );

    // Initial legend display
    const lastBar = candles[candles.length - 1];
    updateLegendDOM(lastBar);

    requestAnimationFrame(() => {
      applyOverlayPatchRef.current?.();
      renderChartGraphicsRef.current?.();
    });
  }, [aggregatedCandles, activeSymbol, timeframe, active, chartType, updateLegendDOM, chartTheme, indicatorTheme, backtestOverlay, backtestOverlayKey, chartHistoryReady, compareSymbol, compareCandles]);

  // Lightweight overlay patch — SL/TP lines and trade markers only
  const applyOverlayPatch = useCallback(() => {
    const chart = chartRef.current;
    const bars = candlesRef.current;
    if (!chart || !bars.length || !chartReadyRef.current || chartConfiguringRef.current) return;

    const cfg = TF_CONFIGS.find((t) => t.label === timeframe) || TF_CONFIGS[0];
    const bucketSecs = cfg.secs;
    const dec = getPriceDecimals(bars[bars.length - 1]?.close);
    const overlays = settings.chartLayout?.overlays ?? DEFAULT_TERMINAL_SETTINGS.chartLayout.overlays;
    const markLineData = [
      ...(overlays.positions !== false ? buildMarkLineData(symbolPosition, dec) : []),
      ...(overlays.agentLevels !== false
        ? buildAgentMarkLines(agentInsight, bars[bars.length - 1]?.close, dec)
        : []),
    ];
    const showBotMarkers = overlays.botMarkers !== false
      && selectedBotId
      && botDetail?.bot?.symbol === activeSymbol
      && botDetail?.trades?.length;
    const tradeMarkers = overlays.trades !== false
      ? buildTradeMarkers(
        tradeHistory,
        activeSymbol,
        bars,
        bucketSecs,
        { excludeBotId: showBotMarkers ? selectedBotId : null },
      )
      : [];
    const botMarkers = showBotMarkers
      ? buildBotTradeMarkers(botDetail.trades, bars, bucketSecs)
      : [];
    const showBacktestMarkers = backtestOverlay?.visible
      && symbolsMatch(backtestOverlay.symbol, activeSymbol)
      && backtestOverlay.trades?.length;
    const backtestMarkers = showBacktestMarkers
      ? buildBacktestTradeMarkers(backtestOverlay.trades, bars, bucketSecs)
      : [];
    const scatterData = [...tradeMarkers, ...botMarkers, ...backtestMarkers];

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
      }, { lazyUpdate: true });
    } catch (err) {
      console.warn('[ChartWidget] overlay patch failed:', err);
    }
  }, [activeSymbol, timeframe, symbolPosition, tradeHistory, selectedBotId, botDetail, botOverlayKey, backtestOverlay, backtestOverlayKey, agentInsight, agentOverlayKey, settings.chartLayout?.overlays]);

  // Render the graphic overlay layer (Volume Profile + drawings) in pixel space.
  // Recomputed on configure, zoom/pan, resize, and when drawings/VPVR change.
  const renderChartGraphics = useCallback(() => {
    const chart = chartRef.current;
    if (!chart || !chartReadyRef.current || chartConfiguringRef.current) return;
    const bars = candlesRef.current;

    // Overlay elements are rendered as a FLAT list of ECharts `graphic` elements
    // (not a nested group): ECharts v6 throws internally (updateLeaveTo) when a
    // graphic *group* with children is diffed/removed, which silently breaks the
    // overlay. Each element carries a stable, type-consistent id so stale ones can
    // be removed by id when the set shrinks.
    const setGraphic = (children) => {
      try {
        const nextIds = new Set();
        for (const el of children) if (el && el.id != null) nextIds.add(el.id);
        const removals = [];
        for (const prevId of overlayIdsRef.current) {
          if (!nextIds.has(prevId)) removals.push({ id: prevId, $action: 'remove' });
        }
        chart.setOption({ graphic: { elements: [...children, ...removals] } });
        overlayIdsRef.current = nextIds;
      } catch (err) {
        console.warn('[ChartWidget] graphic overlay failed:', err);
      }
    };

    if (!bars || !bars.length) {
      setGraphic([]);
      return;
    }

    // Category axes interpret numeric convertToPixel input as the ordinal index,
    // so translate stored {time} → fractional bar index before conversion.
    const priceToY = (price) => {
      const px = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [0, price]);
      return px && Number.isFinite(px[1]) ? px[1] : null;
    };
    const convert = (pt) => {
      if (!pt) return null;
      const idx = timeToFractionalIndex(bars, toUnixSeconds(pt.time));
      if (idx == null) return null;
      const px = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [idx, pt.price]);
      return px && Number.isFinite(px[0]) && Number.isFinite(px[1]) ? px : null;
    };

    const width = chart.getWidth();
    const plotLeft = width * 0.03;
    const plotRight = width * 0.95;

    const children = [];

    if (showVolumeProfileRef.current) {
      const profile = computeVolumeProfile(bars, { bins: 24 });
      if (profile.bins.length >= 2) {
        const y0 = priceToY(profile.bins[0].mid);
        const y1 = priceToY(profile.bins[1].mid);
        const binPx = y0 != null && y1 != null ? Math.abs(y0 - y1) : 6;
        children.push(...volumeProfileGraphic(profile, {
          plotRight,
          maxWidthPx: width * 0.16,
          priceToY,
          binPx,
        }));
      }
    }

    children.push(...drawingsToGraphic(drawingsRef.current, convert, {
      left: plotLeft,
      right: plotRight,
      width,
      priceToY,
      selectedId: selectedDrawingIdRef.current,
    }));

    setGraphic(children);
  }, []);

  const renderChartGraphicsRef = useRef(renderChartGraphics);
  renderChartGraphicsRef.current = renderChartGraphics;

  configureChartRef.current = configureChart;
  applyOverlayPatchRef.current = applyOverlayPatch;

  const loadOlderHistory = useCallback(async () => {
    if (loadingOlderRef.current || olderExhaustedRef.current[activeSymbol]) return;

    const cfg = TF_CONFIGS.find((t) => t.label === timeframe) || TF_CONFIGS[0];
    const barSecs = cfg.secs <= 60 ? 60 : cfg.secs;
    const oldest = getOldestBarTime(activeSymbol, useNativeHt ? timeframe : '1m', barSecs);
    if (oldest == null) return;

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
  }, [activeSymbol, timeframe, useNativeHt]);

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

      chart.on('datazoom', (ev) => {
        if (!chartReadyRef.current || suppressDataZoomEventsRef.current > 0) return;

        const batch = ev.batch?.[0] ?? ev;
        const bars = displayBarsRef.current;
        if (bars.length) {
          const categoryLen = bars.length + FUTURE_PADDING;
          if (typeof batch.end === 'number') {
            const endIdx = Math.round((batch.end / 100) * categoryLen);
            pinnedToLiveRef.current = endIdx >= bars.length - 1;
          }
        }

        // Reposition the graphic overlay (VPVR + drawings) on zoom/pan.
        renderChartGraphicsRef.current?.();

        const now = Date.now();
        if (now - dataZoomHandlerLastMsRef.current < DATAZOOM_HANDLER_MIN_MS) return;
        dataZoomHandlerLastMsRef.current = now;

        if (typeof batch.start === 'number' && batch.start <= 2) {
          if (loadingOlderRef.current) return;
          if (now - loadOlderLastMsRef.current < LOAD_OLDER_MIN_INTERVAL_MS) return;
          loadOlderLastMsRef.current = now;
          loadOlderRef.current?.();
        }
      });

      const handleChartClick = (params) => {
        const pointInPixel = [params.offsetX, params.offsetY];
        const inGrid = chart.containPixel({ gridIndex: 0 }, pointInPixel);

        // 1) SL/TP edit mode (existing behavior).
        const mode = useStore.getState().chartInteractionMode;
        if (mode !== 'normal') {
          if (inGrid) {
            const pointInValue = chart.convertFromPixel({ gridIndex: 0 }, pointInPixel);
            const price = pointInValue[1];
            if (price !== null && price > 0) {
              window.dispatchEvent(new CustomEvent('chart-click', { detail: price }));
            }
          }
          return;
        }

        const tool = activeToolRef.current;
        const bars = candlesRef.current;
        if (!inGrid) return;

        // 2) Drawing creation.
        if (tool) {
          const val = chart.convertFromPixel({ xAxisIndex: 0, yAxisIndex: 0 }, pointInPixel);
          const price = val?.[1];
          if (price == null || !Number.isFinite(price)) return;
          const idx = Math.round(val[0]);
          const bar = bars[idx] ?? bars[bars.length - 1];
          const time = bar ? toUnixSeconds(bar.time) : null;
          if (time == null) return;

          if (tool === 'hline') {
            addDrawingRef.current(createDrawing('hline', [{ price }]));
            setActiveTool(null);
            return;
          }
          if (!pendingDrawPointRef.current) {
            pendingDrawPointRef.current = { time, price };
          } else {
            const p1 = pendingDrawPointRef.current;
            pendingDrawPointRef.current = null;
            addDrawingRef.current(createDrawing(tool, [p1, { time, price }]));
            setActiveTool(null);
          }
          return;
        }

        // 3) Selection (when no tool active): hit-test existing drawings.
        const priceToY = (p) => {
          const px = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [0, p]);
          return px && Number.isFinite(px[1]) ? px[1] : null;
        };
        const convert = (pt) => {
          const idx = timeToFractionalIndex(bars, toUnixSeconds(pt.time));
          if (idx == null) return null;
          const px = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [idx, pt.price]);
          return px && Number.isFinite(px[0]) ? px : null;
        };
        const hit = hitTestDrawings(
          params.offsetX, params.offsetY, drawingsRef.current, convert, { priceToY },
        );
        setSelectedDrawingId(hit);
      };

      // zrender's synthetic 'click' is suppressed on a live-updating chart (the
      // hovered element changes between mousedown and mouseup), so detect clicks
      // ourselves from a mousedown/mouseup pair with negligible movement. This
      // also distinguishes a click from a pan-drag (dataZoom inside).
      let downPt = null;
      chart.getZr().on('mousedown', (p) => {
        downPt = { x: p.offsetX, y: p.offsetY, t: Date.now() };
      });
      chart.getZr().on('mouseup', (p) => {
        const start = downPt;
        downPt = null;
        if (!start) return;
        const moved = Math.hypot(p.offsetX - start.x, p.offsetY - start.y);
        if (moved <= 5 && Date.now() - start.t < 700) handleChartClick(p);
      });

      if (import.meta.env.DEV) {
        el.__chartInstance = chart;
      }

      requestAnimationFrame(() => {
        if (chartHistoryReadyRef.current) {
          lastConfigureRevisionRef.current = '';
          configureChartRef.current();
        }
      });
      return true;
    };

    const ro = new ResizeObserver(() => {
      if (chart) {
        chart.resize();
        renderChartGraphicsRef.current?.();
        return;
      }
      mountChart();
    });
    ro.observe(el);
    mountChart();

    return () => {
      disposed = true;
      ro.disconnect();
      if (configureDebounceRef.current) {
        clearTimeout(configureDebounceRef.current);
        configureDebounceRef.current = null;
      }
      chartConfiguringRef.current = false;
      lastConfigureRevisionRef.current = '';
      chart?.dispose();
      chartRef.current = null;
      chartReadyRef.current = false;
      if (el.__chartInstance) delete el.__chartInstance;
    };
  }, [updateLegendDOM, chartTheme.echartsTheme, resolvedTheme]);

  // Full rebuild when structure/history/indicators change (debounced — coalesces timeframe toggles)
  useEffect(() => {
    if (configureDebounceRef.current) clearTimeout(configureDebounceRef.current);
    configureDebounceRef.current = setTimeout(() => {
      configureDebounceRef.current = null;
      if (configureRevision === lastConfigureRevisionRef.current) return;
      lastConfigureRevisionRef.current = configureRevision;
      configureChartRef.current();
    }, CONFIGURE_DEBOUNCE_MS);
    return () => {
      if (configureDebounceRef.current) {
        clearTimeout(configureDebounceRef.current);
        configureDebounceRef.current = null;
      }
    };
  }, [configureRevision]);

  // Lightweight overlay patch — trades, positions, and after full rebuild
  useEffect(() => {
    if (!chartReadyRef.current) return;
    applyOverlayPatchRef.current?.();
  }, [positionOverlayKey, tradeOverlayKey, botOverlayKey, backtestOverlayKey]);

  // Re-render the graphic overlay (Volume Profile + drawings) on state changes.
  useEffect(() => {
    if (!chartReadyRef.current) return;
    renderChartGraphicsRef.current?.();
  }, [drawings, selectedDrawingId, showVolumeProfile]);

  // Persist the Volume Profile toggle to settings.
  useEffect(() => {
    updateChartLayout({ volumeProfile: showVolumeProfile });
  }, [showVolumeProfile, updateChartLayout]);

  // Replay playback timer — advances the cursor one bar at a time.
  useEffect(() => {
    if (!replayActive || !replayPlaying) return undefined;
    const total = aggregatedCandles.length;
    const interval = Math.max(80, 700 / replaySpeed);
    const id = setInterval(() => {
      setReplayIndex((i) => {
        const ni = i + 1;
        if (ni >= total) {
          setReplayPlaying(false);
          return total;
        }
        return ni;
      });
    }, interval);
    return () => clearInterval(id);
  }, [replayActive, replayPlaying, replaySpeed, aggregatedCandles.length]);

  const enterReplay = useCallback(() => {
    const total = aggregatedCandles.length;
    if (total < 5) return;
    setReplayActive(true);
    setReplayPlaying(false);
    setReplayIndex(Math.max(2, Math.floor(total / 2)));
  }, [aggregatedCandles.length]);

  const exitReplay = useCallback(() => {
    setReplayActive(false);
    setReplayPlaying(false);
  }, []);

  // Leaving the symbol/timeframe abandons any active replay (data changed).
  useEffect(() => {
    setReplayActive(false);
    setReplayPlaying(false);
    setCompareSymbol((cur) => (cur === activeSymbol ? null : cur));
  }, [activeSymbol, timeframe]);

  useEffect(() => {
    const onOverlayChanged = () => applyOverlayPatchRef.current?.();
    window.addEventListener(BACKTEST_OVERLAY_EVENT, onOverlayChanged);
    return () => window.removeEventListener(BACKTEST_OVERLAY_EVENT, onOverlayChanged);
  }, []);

  useEffect(() => {
    const onFocusBar = (e) => {
      const { time, symbol: sym } = e.detail ?? {};
      if (time == null || !symbolsMatch(sym, activeSymbol)) return;
      const chart = chartRef.current;
      if (!chart || !chartReadyRef.current) return;
      const bars = displayBarsRef.current;
      if (!bars.length) return;
      const target = toUnixSeconds(time);
      let idx = bars.findIndex((b) => toUnixSeconds(b.time) === target);
      if (idx < 0) {
        idx = bars.findIndex((b) => Math.abs(toUnixSeconds(b.time) - target) < 120);
      }
      if (idx < 0) return;
      const total = buildCategoryAxisData(bars).length;
      const catIdx = FUTURE_PADDING + idx;
      const half = 25;
      const start = Math.max(0, ((catIdx - half) / total) * 100);
      const end = Math.min(100, ((catIdx + half) / total) * 100);
      try {
        suppressDataZoomEventsRef.current += 1;
        chart.setOption({
          dataZoom: buildDataZoomOption(start, end),
        });
        requestAnimationFrame(() => {
          suppressDataZoomEventsRef.current = Math.max(0, suppressDataZoomEventsRef.current - 1);
        });
        pinnedToLiveRef.current = false;
      } catch (err) {
        console.warn('[ChartWidget] backtest focus zoom failed:', err);
      }
    };
    window.addEventListener('backtest-focus-bar', onFocusBar);
    return () => window.removeEventListener('backtest-focus-bar', onFocusBar);
  }, [activeSymbol]);

  const applyLiveCandleUpdate = useCallback(() => {
    const chart = chartRef.current;
    if (!chart || !chartReadyRef.current || chartConfiguringRef.current) return;
    // Replay mode freezes live updates so the cursor controls what's visible.
    if (replayActiveRef.current) return;

    const cfg = TF_CONFIGS.find(t => t.label === timeframe) || TF_CONFIGS[0];
    let aggregatedLive;

    if (useNativeHt) {
      if (!patchHtFormingBar(activeSymbol, timeframe, cfg.secs)) {
        const px = useStore.getState().tickerData[activeSymbol]?.price;
        if (px != null) {
          patchHtFormingBarFromPrice(activeSymbol, timeframe, cfg.secs, px);
        }
      }
      const htSeries = getCandles(activeSymbol, timeframe, cfg.secs);
      aggregatedLive = htSeries[htSeries.length - 1];
      if (!aggregatedLive) return;
    } else {
      const raw = getCandles(activeSymbol, '1m', 60);
      if (!raw.length) return;
      aggregatedLive = timeframe === '1m'
        ? raw[raw.length - 1]
        : aggregateBucket(raw, cfg);
      if (!aggregatedLive) return;
    }

    const bars = displayBarsRef.current;
    if (!bars.length) return;

    const last = bars[bars.length - 1];
    let isNewBar = false;
    if (last && last.time === aggregatedLive.time) {
      bars[bars.length - 1] = aggregatedLive;
    } else if (!last || aggregatedLive.time > last.time) {
      isNewBar = true;
      bars.push({ ...aggregatedLive });
      if (bars.length > displayBarLimit) {
        bars.shift();
      }
    } else {
      return;
    }

    candlesRef.current = bars;
    const cache = liveSeriesCacheRef.current;

    // Heikin-Ashi / Renko depend on prior bars, so incremental OHLC patching
    // would desync the forming bar. Rebuild the full option for correctness.
    if (chartTypeRef.current === 'heikin' || chartTypeRef.current === 'renko') {
      configureChartRef.current();
      updateLegendDOM(aggregatedLive);
      return;
    }

    try {
      const patch = {};

      if (isNewBar) {
        const categoryData = buildCategoryAxisData(bars);
        const { xAxisCount } = chartLayoutRef.current;
        patch.xAxis = Array.from({ length: xAxisCount }, (_, i) => ({
          id: `x-${i}`,
          gridIndex: i,
          data: categoryData,
        }));
        patch.series = buildNewBarSeriesPatches(bars, chartType, active, indicatorTheme, cache);
        if (pinnedToLiveRef.current) {
          const { start, end } = liveEdgeDataZoomForBars(bars.length, categoryData);
          patch.dataZoom = buildDataZoomOption(start, end);
          suppressDataZoomEventsRef.current += 1;
        }
      } else {
        updateLiveSeriesCache(cache, bars, chartType, active, indicatorTheme);
        patch.series = buildLightLiveSeriesPatchesFromCache(cache, chartType, active);
      }

      // Merge by series id only — never replaceMerge (drops indicator series not in patch)
      chart.setOption(patch, { lazyUpdate: false });
      if (isNewBar && suppressDataZoomEventsRef.current > 0) {
        requestAnimationFrame(() => {
          suppressDataZoomEventsRef.current = Math.max(0, suppressDataZoomEventsRef.current - 1);
        });
      }
      updateLegendDOM(aggregatedLive);
      if (isNewBar) {
        applyOverlayPatchRef.current?.();
      }
    } catch (err) {
      console.warn('[ChartWidget] live candle update failed:', err);
    }
  }, [activeSymbol, timeframe, chartType, updateLegendDOM, active, displayBarLimit, indicatorTheme, terminalMode, useNativeHt]);

  const pumpLiveCandleUpdate = useCallback(() => {
    const now = performance.now();
    if (now - liveLastPaintMs.current < LIVE_MIN_INTERVAL_MS) {
      if (liveRafRef.current == null) {
        liveRafRef.current = requestAnimationFrame(() => {
          liveRafRef.current = null;
          pumpLiveCandleUpdate();
        });
      }
      return;
    }
    liveLastPaintMs.current = now;
    applyLiveCandleUpdate();
  }, [applyLiveCandleUpdate]);

  // Immediate forming-bar paint on every WS price (Massive) — does not wait for React ticker flush.
  useEffect(() => {
    const symbol = activeSymbol;
    return onLivePrice((sym, _price) => {
      if (sym !== symbol) return;
      if (liveRafRef.current != null) return;
      liveRafRef.current = requestAnimationFrame(() => {
        liveRafRef.current = null;
        pumpLiveCandleUpdate();
      });
    });
  }, [activeSymbol, pumpLiveCandleUpdate]);

  useEffect(() => {
    const symbol = activeSymbol;
    const bufKey = chartBufKey;
    const unsubscribe = useStore.subscribe(
      (state) => `${state.candleRevision[symbol] || 0}:${state.candleRevision[bufKey] || 0}`,
      () => {
        if (liveRafRef.current != null) return;
        liveRafRef.current = requestAnimationFrame(() => {
          liveRafRef.current = null;
          pumpLiveCandleUpdate();
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
  }, [activeSymbol, chartBufKey, pumpLiveCandleUpdate]);

  // Ticker state flush (~60 Hz batched) — keeps header/watchlist in sync.
  useEffect(() => {
    const symbol = activeSymbol;
    let lastPrice;
    return useStore.subscribe(
      (state) => state.tickerData[symbol]?.price,
      (price) => {
        if (price == null || price === lastPrice) return;
        lastPrice = price;
        if (liveRafRef.current != null) return;
        liveRafRef.current = requestAnimationFrame(() => {
          liveRafRef.current = null;
          pumpLiveCandleUpdate();
        });
      },
    );
  }, [activeSymbol, pumpLiveCandleUpdate]);

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

  // Drawing-tool keyboard shortcuts: ESC cancels, Delete/Backspace removes selection.
  useEffect(() => {
    const onKey = (e) => {
      const tag = document.activeElement?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      if (e.key === 'Escape') {
        if (pendingDrawPointRef.current || activeToolRef.current) {
          pendingDrawPointRef.current = null;
          setActiveTool(null);
        } else if (selectedDrawingIdRef.current) {
          setSelectedDrawingId(null);
        }
      } else if ((e.key === 'Delete' || e.key === 'Backspace') && selectedDrawingIdRef.current) {
        removeDrawing(selectedDrawingIdRef.current);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setActiveTool, setSelectedDrawingId, removeDrawing]);

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
              <ToggleGroupItem value="heikin" size="sm" className="px-2 text-[0.68rem] font-bold" title="Heikin-Ashi">
                <CandlestickChart data-icon="inline-start" />
                HA
              </ToggleGroupItem>
              <ToggleGroupItem value="renko" size="sm" className="px-2 text-[0.68rem] font-bold" title="Renko (time-aligned)">
                <Grid3x3 data-icon="inline-start" />
                Renko
              </ToggleGroupItem>
              <ToggleGroupItem value="line" size="sm" className="px-2 text-[0.68rem] font-bold">
                <TrendingUp data-icon="inline-start" />
                Line
              </ToggleGroupItem>
            </ToggleGroup>
            <WidgetToolbarDivider />
            <ToggleGroup
              type="single"
              value={activeTool || ''}
              onValueChange={(v) => { pendingDrawPointRef.current = null; setActiveTool(v || null); }}
              spacing={0}
            >
              <ToggleGroupItem value="trendline" size="sm" className="px-1.5" title="Trendline (2 clicks)">
                <Spline size={13} />
              </ToggleGroupItem>
              <ToggleGroupItem value="hline" size="sm" className="px-1.5" title="Horizontal level (1 click)">
                <Minus size={13} />
              </ToggleGroupItem>
              <ToggleGroupItem value="fib" size="sm" className="px-1.5" title="Fibonacci retracement (2 clicks)">
                <AlignJustify size={13} />
              </ToggleGroupItem>
              <ToggleGroupItem value="rectangle" size="sm" className="px-1.5" title="Rectangle (2 clicks)">
                <Square size={13} />
              </ToggleGroupItem>
            </ToggleGroup>
            <Button
              variant={showVolumeProfile ? 'secondary' : 'ghost'}
              size="sm"
              className="px-1.5"
              title="Volume Profile (VPVR)"
              onClick={() => setShowVolumeProfile((v) => !v)}
            >
              <BarChart2 size={13} />
            </Button>
            <Button
              variant={replayActive ? 'secondary' : 'ghost'}
              size="sm"
              className="px-1.5"
              title="Replay mode (bar-by-bar)"
              onClick={() => (replayActive ? exitReplay() : enterReplay())}
            >
              <History size={13} />
            </Button>
            {compareOptions.length > 0 && (
              <Select
                value={compareSymbol || '__none__'}
                onValueChange={(v) => setCompareSymbol(v === '__none__' ? null : v)}
              >
                <SelectTrigger
                  className={cn(
                    'h-6 w-auto gap-1 border-0 px-1.5 text-[0.62rem] font-semibold',
                    compareSymbol && 'text-[#f472b6]',
                  )}
                  aria-label="Compare symbol"
                  title="Compare with another symbol (rebased %)"
                >
                  <GitCompareArrows size={13} />
                  <SelectValue placeholder="Compare" />
                </SelectTrigger>
                <SelectContent position="popper" className="max-h-64">
                  <SelectItem value="__none__" className="text-xs">Compare: off</SelectItem>
                  {compareOptions.map((sym) => (
                    <SelectItem key={sym} value={sym} className="text-xs">{sym}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            {(drawings.length > 0 || selectedDrawingId) && (
              <Button
                variant="ghost"
                size="sm"
                className="px-1.5 text-muted-foreground hover:text-destructive"
                title={selectedDrawingId ? 'Delete selected drawing' : 'Clear all drawings'}
                onClick={() => (selectedDrawingId ? removeDrawing(selectedDrawingId) : clearDrawings())}
              >
                <Trash2 size={13} />
              </Button>
            )}
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
              {Object.entries(indicatorToolbar).map(([key, ind]) => (
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
      data-tour="chart"
      icon={AreaChart}
      title={activeSymbol}
      headerRight={
        <div className="relative z-20 flex min-w-0 items-center gap-[var(--icon-gap-loose)]">
          <ChartHeaderPrice symbol={activeSymbol} />
          <ChartAnalystBadge symbol={activeSymbol} timeframe={timeframe} onDeployAgent={handleDeployChartAgent} />
          <Button
            variant={zenMode ? 'secondary' : 'ghost'}
            size="icon-sm"
            className="shrink-0"
            onPointerDown={(e) => {
              e.preventDefault();
              e.stopPropagation();
            }}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              window.dispatchEvent(new CustomEvent('chart-zen-toggle'));
            }}
            title={zenMode ? 'Restore layout (F)' : 'Maximize chart (F)'}
          >
            {zenMode ? <Minimize2 size={14} aria-hidden /> : <Maximize2 size={14} aria-hidden />}
            <span className="sr-only">{zenMode ? 'Restore layout' : 'Maximize chart'}</span>
          </Button>
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

      {activeTool && (
        <Badge className="icon-label pointer-events-none absolute top-2 left-1/2 z-[100] -translate-x-1/2 border-primary/40 bg-primary/90 px-3 py-1 text-[0.68rem] font-bold text-primary-foreground shadow-[0_0_15px_var(--color-accent-bg)]">
          {activeTool === 'hline'
            ? 'Click to place a horizontal level'
            : `Click ${pendingDrawPointRef.current ? 'end' : 'start'} point for ${activeTool}`}
          <span className="font-normal opacity-80">(ESC to cancel)</span>
        </Badge>
      )}

      {replayActive && (
        <div className="absolute bottom-3 left-1/2 z-[100] flex -translate-x-1/2 items-center gap-1 rounded-md border border-border/60 bg-background/95 px-2 py-1 shadow-lg backdrop-blur">
          <Button variant="ghost" size="icon-sm" title="Restart" onClick={() => { setReplayPlaying(false); setReplayIndex(2); }}>
            <RotateCcw size={13} />
          </Button>
          <Button variant="ghost" size="icon-sm" title="Step back" onClick={() => { setReplayPlaying(false); setReplayIndex((i) => Math.max(2, i - 1)); }}>
            <SkipBack size={13} />
          </Button>
          <Button variant="ghost" size="icon-sm" title={replayPlaying ? 'Pause' : 'Play'} onClick={() => setReplayPlaying((p) => !p)}>
            {replayPlaying ? <Pause size={13} /> : <Play size={13} />}
          </Button>
          <Button variant="ghost" size="icon-sm" title="Step forward" onClick={() => { setReplayPlaying(false); setReplayIndex((i) => Math.min(aggregatedCandles.length, i + 1)); }}>
            <SkipForward size={13} />
          </Button>
          <span className="px-1 font-mono text-[10px] text-muted-foreground tabular-nums">
            {Math.min(replayIndex, aggregatedCandles.length)}/{aggregatedCandles.length}
          </span>
          <ToggleGroup type="single" value={String(replaySpeed)} onValueChange={(v) => v && setReplaySpeed(Number(v))} spacing={0}>
            {[1, 2, 4].map((s) => (
              <ToggleGroupItem key={s} value={String(s)} size="sm" className="px-1.5 text-[0.6rem] font-bold">
                {s}x
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          <Button variant="ghost" size="icon-sm" title="Exit replay" onClick={exitReplay}>
            <X size={13} />
          </Button>
        </div>
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
        {!chartHistoryReady && aggregatedCandles.length === 0 && (
          <div className="pointer-events-none absolute inset-0 z-[5] flex items-center justify-center bg-background/40">
            <span className="text-xs text-muted-foreground">
              {terminalMode === 'LIVE_MASSIVE'
                ? 'Loading Massive chart history…'
                : 'Loading chart history…'}
            </span>
          </div>
        )}
        {useNativeHt && !nativeHtLoaded && aggregatedCandles.length > 0 && (
          <div className="pointer-events-none absolute right-2 top-2 z-[5] rounded bg-background/70 px-2 py-0.5 text-[10px] text-muted-foreground">
            Syncing {timeframe}…
          </div>
        )}
      </div>
    </WidgetShell>
  );
}
