/**
 * Pure chart data helpers extracted from ChartWidget (Chart 8 refactor).
 */
import {
  calcSMA, calcEMA, calcBollingerBands, calcRSI, calcMACD, calcATR, buildVwapSeriesValues,
} from '../../utils/indicators';
import { volumeBarEntry, macdHistogramColor } from '../../settings/indicatorThemes';
import {
  getCandles, toUnixSeconds, CHART_READY_MIN_BARS,
} from '../../services/candleBuffer';
import { parseTradeTimestamp, parseSignalBarTime } from '@/lib/botAttribution';
import {
  CHART_DISPLAY_BARS_DEFAULT,
  CHART_DISPLAY_MAX_BARS,
} from '../../services/memoryBudget';

export const TF_CONFIGS = [
  { label: '1m',  secs: 60    },
  { label: '5m',  secs: 300   },
  { label: '15m', secs: 900   },
  { label: '1H',  secs: 3600  },
  { label: '4H',  secs: 14400 },
  { label: '1D',  secs: 86400 },
];

/** Default visible bars; grows when user scrolls into archived history */
export const CHART_DISPLAY_BARS = CHART_DISPLAY_BARS_DEFAULT;
export const CHART_DISPLAY_MAX = CHART_DISPLAY_MAX_BARS;
export const ARCHIVE_LOAD_CHUNK = 1000;
export const ARCHIVE_1M_RETENTION_SEC = 90 * 86400;
export const FUTURE_PADDING = 15;
/** Wait for bulk history before first paint (avoids 1-bar → full-history jump). */
export const CHART_HISTORY_MIN_BARS = 3;
export const CHART_HISTORY_CACHED_BARS = 20;
export const MASSIVE_CHART_MIN_BARS = 50;
export const CHART_HISTORY_GATE_MS = 4000;
export const LOAD_OLDER_MIN_INTERVAL_MS = 2000;
export const CONFIGURE_DEBOUNCE_MS = 80;
export const CHART_VISIBLE_BARS = 50;

export function sliceRawForTimeframe(raw, intervalSecs, displayLimit) {
  if (!raw.length) return raw;
  const barsPerBucket = Math.max(1, Math.ceil(intervalSecs / 60));
  const tail = Math.min(raw.length, (displayLimit + 4) * barsPerBucket + 32);
  return tail < raw.length ? raw.slice(-tail) : raw;
}

export function chartStructureKey(chartType, subPanes, showBacktestEquity) {
  return `${chartType}|${subPanes.join(',')}|${showBacktestEquity ? 1 : 0}`;
}

export const SERIES_ANIM_OFF = {
  animation: false,
  animationDuration: 0,
  animationDurationUpdate: 0,
};

/** True when the ECharts instance is missing or has been disposed. */
export function isChartDisposed(chart) {
  return !chart || (typeof chart.isDisposed === 'function' && chart.isDisposed());
}

export function isChartHistoryReady(barCount, historyRev, gateForced, terminalMode, useNativeHt = false) {
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

export function withSeriesAnimOff(series) {
  return { ...series, ...SERIES_ANIM_OFF };
}

const pad = (n) => String(n).padStart(2, '0');

export function formatTimeLabel(timeSec) {
  const d = new Date(timeSec * 1000);
  return `${pad(d.getUTCMonth() + 1)}/${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

export function buildCategoryAxisData(bars) {
  const keys = bars.map((c) => toUnixSeconds(c.time));
  for (let i = 0; i < FUTURE_PADDING; i++) keys.push(`__pad_${i}__`);
  return keys;
}

export function indexOfCategoryKey(categoryData, key) {
  if (key == null || !categoryData.length) return -1;
  const sec = toUnixSeconds(key);
  for (let i = 0; i < categoryData.length; i++) {
    const k = categoryData[i];
    if (k === key) return i;
    if (sec != null && toUnixSeconds(k) === sec) return i;
  }
  return -1;
}

export function lastRealCategoryIndex(categoryData) {
  return Math.max(0, categoryData.length - FUTURE_PADDING - 1);
}

export function defaultDataZoomPercent(candleCount, totalCategoryCount, visibleBars = 50) {
  const liveGap = Math.min(5, FUTURE_PADDING);
  const endPct = Math.min(100, ((candleCount + liveGap) / totalCategoryCount) * 100);
  const startPct = Math.max(0, ((candleCount - visibleBars) / totalCategoryCount) * 100);
  return { start: startPct, end: endPct };
}

export function dataZoomEndIndex(dz, categoryData) {
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
export function isDataZoomAtLiveEdge(dz, categoryData) {
  if (!dz || !categoryData.length) return true;
  const realEnd = lastRealCategoryIndex(categoryData);
  const endIdx = dataZoomEndIndex(dz, categoryData);
  if (endIdx < 0) return true;
  return endIdx >= realEnd - 1;
}

export function liveEdgeDataZoomForBars(barCount, categoryData, visibleBars = CHART_VISIBLE_BARS) {
  return defaultDataZoomPercent(barCount, categoryData.length, visibleBars);
}

export function buildDataZoomOption(start, end, xAxisIndex = null) {
  const s = Number.isFinite(start) ? Math.max(0, Math.min(100, start)) : 0;
  const e = Number.isFinite(end) ? Math.max(0, Math.min(100, end)) : 100;
  const axis = xAxisIndex != null ? { xAxisIndex } : {};
  return [
    { id: 'dz-inside', type: 'inside', ...axis, start: s, end: e },
    { id: 'dz-slider', type: 'slider', ...axis, start: s, end: e },
  ];
}

/** True when the chart has initialized dataZoom with numeric start/end windows. */
export function hasValidDataZoom(chart) {
  if (!chart || isChartDisposed(chart)) return false;
  try {
    const opt = chart.getOption?.();
    const dzList = normalizeEchartsList(opt?.dataZoom);
    return dzList.length > 0 && dzList.every(
      (dz) => typeof dz?.start === 'number' && typeof dz?.end === 'number',
    );
  } catch {
    return false;
  }
}

export function preserveDataZoomPercent(prevCategoryData, nextCategoryData, prevDz, candleCount, nextCandleCount) {
  if (prevDz?.start == null || prevDz?.end == null) return null;

  const wasAtEnd = isDataZoomAtLiveEdge(prevDz, prevCategoryData);
  const diff = nextCategoryData.length - prevCategoryData.length;

  if (diff !== 0 && wasAtEnd) {
    return liveEdgeDataZoomForBars(nextCandleCount, nextCategoryData);
  }

  return { start: prevDz.start, end: prevDz.end };
}

/** TOS-style: buys below bar, sells above; exits at fill price. */
export function markerYForTrade(candle, side, { isExit = false, fillPrice } = {}) {
  if (isExit) return fillPrice ?? candle?.close;
  if (side === 'BUY') return candle?.low ?? fillPrice;
  if (side === 'SELL') return candle?.high ?? fillPrice;
  return fillPrice ?? candle?.close;
}

export function categoryAxisLabelFormatter(val) {
  if (val == null || val === '' || String(val).startsWith('__pad_')) return '';
  const sec = toUnixSeconds(val);
  return sec == null ? '' : formatTimeLabel(sec);
}

/** Shared x-axis category config — unix keys with human-readable labels. */
export function categoryXAxisOpts(categoryData, gridIndex, { showLabels = true, chartTheme } = {}) {
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

export function buildMainSeriesData(bars, chartType) {
  const data = chartType === 'line'
    ? bars.map(c => c.close)
    : bars.map(c => [c.open, c.close, c.low, c.high]);
  for (let i = 0; i < FUTURE_PADDING; i++) data.push('-');
  return data;
}

export function buildVolumeSeriesData(bars, indicatorTheme) {
  const data = bars.map(c => volumeSeriesEntry(c, indicatorTheme));
  for (let i = 0; i < FUTURE_PADDING; i++) data.push(null);
  return data;
}

export function normalizeEchartsList(value) {
  if (value == null) return [];
  return Array.isArray(value) ? value : [value];
}

export function aggregateBucket(raw, cfg) {
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

export function barMatches(a, b) {
  if (!a || !b) return false;
  return a.time === b.time
    && a.open === b.open
    && a.high === b.high
    && a.low === b.low
    && a.close === b.close
    && (a.volume || 0) === (b.volume || 0);
}

export function bucketCandles(raw, intervalSecs) {
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

export function volumeSeriesEntry(bar, indicatorTheme) {
  return volumeBarEntry(bar, indicatorTheme);
}

/** Aggregate a symbol's candles to the active timeframe (shared by primary + comparison). */
export function aggregateCandlesForSymbol(symbol, cfg, limit, useNativeHt) {
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

export const MACD_WARMUP = 33;
export const RSI_WARMUP = 14;
export const ATR_WARMUP = 14;
export const BB_WARMUP = 19;

export function padIndicatorValues(data) {
  const out = [...data];
  for (let i = 0; i < FUTURE_PADDING; i++) out.push(null);
  return out;
}

export function mapEmaSeries(candles, period) {
  const ema = calcEMA(candles, period);
  const offset = period - 1;
  return padIndicatorValues(candles.map((_, i) => (i >= offset ? ema[i - offset]?.value : null)));
}

export function mapRsiSeries(candles) {
  const rsi = calcRSI(candles, 14);
  return padIndicatorValues(candles.map((_, i) => (i >= RSI_WARMUP ? rsi[i - RSI_WARMUP]?.value : null)));
}

export function mapAtrSeries(candles) {
  const atr = calcATR(candles, 14);
  return padIndicatorValues(candles.map((_, i) => (i >= ATR_WARMUP ? atr[i - ATR_WARMUP]?.value : null)));
}

export function mapMacdSeries(candles, indicatorTheme) {
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

export function mapBbSeries(candles) {
  const bb = calcBollingerBands(candles, 20, 2);
  const mapper = (bbList) => padIndicatorValues(
    candles.map((_, i) => (i >= BB_WARMUP ? bbList[i - BB_WARMUP]?.value : null)),
  );
  return { upper: mapper(bb.upper), middle: mapper(bb.middle), lower: mapper(bb.lower) };
}

export function mapVwapSeries(candles) {
  return padIndicatorValues(buildVwapSeriesValues(candles));
}

/** Fast live patch: price (+ volume) only — indicators update on new bar / full rebuild. */
export function updateLiveSeriesCache(cache, bars, chartType, active, indicatorTheme, { forceRebuild = false } = {}) {
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

export function buildLightLiveSeriesPatchesFromCache(cache, chartType, active) {
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
export function buildNewBarSeriesPatches(bars, chartType, active, indicatorTheme, cache) {
  updateLiveSeriesCache(cache, bars, chartType, active, indicatorTheme, { forceRebuild: true });
  const liveIds = new Set(['main', 'volume']);
  const live = buildLightLiveSeriesPatchesFromCache(cache, chartType, active);
  const indicators = buildIndicatorSeriesPatches(bars, active, indicatorTheme)
    .filter((p) => !liveIds.has(p.id));
  return [...live, ...indicators];
}

/** Series patches for live candle updates — keeps sub-panes in sync with price/volume. */
export function buildIndicatorSeriesPatches(bars, active, indicatorTheme) {
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

export function buildAgentMarkLines(insight, lastClose, dec) {
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

export function formatVol(v) {
  if (!v) return '—';
  if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B';
  if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return v.toFixed(2);
}

export function getPriceDecimals(price) {
  if (!price || price <= 0) return 2;
  if (price < 0.0001)  return 8;
  if (price < 0.001)   return 6;
  if (price < 0.1)     return 5;
  if (price < 1)       return 4;
  if (price < 10)      return 3;
  return 2;
}

export function buildMarkLineData(symbolPosition, dec) {
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
  // SL/TP rendered as draggable graphic overlay (slTpOverlay.js)
  return markLineData;
}

/** Map a bar open-time (unix sec) to the displayed candle index. */
export function findBarIndexForBarTime(candles, barTimeSec, bucketSecs = 60) {
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
export function resolveBotMarkerAnchor(trade, candles, bucketSecs) {
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
export function findBarIndexForTrade(candles, timestamp, bucketSecs = 60) {
  const d = parseTradeTimestamp(timestamp);
  const tsSec = d ? Math.floor(d.getTime() / 1000) : null;
  if (tsSec == null) return -1;
  return findBarIndexForBarTime(candles, tsSec, bucketSecs);
}

export function toSignalScatterPoint(candles, barIndex, yPrice, { value, symbol, symbolSize, itemStyle }) {
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

export function tradeMarkerPoint(candles, timestamp, price, bucketSecs, marker) {
  const idx = findBarIndexForTrade(candles, timestamp, bucketSecs);
  if (idx < 0) return null;
  const candle = candles[idx];
  const side = marker.side ?? (String(marker.value).startsWith('BUY') ? 'BUY' : 'SELL');
  const yPrice = markerYForTrade(candle, side, { fillPrice: price });
  return toSignalScatterPoint(candles, idx, yPrice, marker);
}

export function buildBotTradeMarkers(botTrades, candles, bucketSecs) {
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

export function buildBacktestTradeMarkers(backtestTrades, candles, bucketSecs) {
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

export function mapBacktestEquityLine(equityCurve, candles) {
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
  return padIndicatorValues(data);
}

export function buildTradeMarkers(tradeHistory, activeSymbol, candles, bucketSecs, { excludeBotId } = {}) {
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
