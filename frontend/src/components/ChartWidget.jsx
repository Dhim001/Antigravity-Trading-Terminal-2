/**
 * ChartWidget.jsx — Professional Trading Chart (TradingView-style)
 *
 * Fixes applied (Changes 1–6):
 *  1. Removed shiftVisibleRangeOnNewBar + rightBarStaysOnScroll → eliminates rightward drift
 *  2. isProgrammaticRef guard → breaks the scrollToRealTime feedback loop
 *  3. setVisibleLogicalRange deferred to rAF + from clamped ≥ 0 + autoScale reset → fixes TF-switch scaling
 *  4. Indicator setData skipped on intra-bar live ticks → no per-tick flicker
 *  5. lastBarTimeRef + isAtRightRef reset on every full reload → clean state handoff
 *  6a. lastValueVisible + priceLineVisible + dynamic priceFormat per asset → TV-style right-axis chip
 *  6b. tickMarkFormatter → TV-style time labels (09:30 / 21 May / Jun / 2024)
 *  6c. OHLCV legend overlay top-left → TV-style status line
 *  6d. Dynamic time-axis visibility on lowest visible pane
 */
import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { createChart, CandlestickSeries, LineSeries, HistogramSeries } from 'lightweight-charts';
import { useStore } from '../store/useStore';
import {
  calcSMA, calcEMA, calcBollingerBands, calcRSI, calcMACD, calcVWAP, calcATR, generateSignal
} from '../utils/indicators';
import { AreaChart, TrendingUp } from 'lucide-react';

// ─── Timeframe configs ────────────────────────────────────────────────────────
const TF_CONFIGS = [
  { label: '1m',  secs: 60    },
  { label: '5m',  secs: 300   },
  { label: '15m', secs: 900   },
  { label: '1H',  secs: 3600  },
  { label: '4H',  secs: 14400 },
  { label: '1D',  secs: 86400 },
];

// Aggregate 1-min candles into higher-TF buckets (handles gaps correctly)
function aggregateCandles(raw, bucketSecs) {
  if (!raw || raw.length === 0) return [];
  if (bucketSecs <= 60) return raw;
  const buckets = new Map();
  for (const c of raw) {
    const t = Math.floor(c.time / bucketSecs) * bucketSecs;
    if (!buckets.has(t)) {
      buckets.set(t, { time: t, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume || 0 });
    } else {
      const b = buckets.get(t);
      b.high   = Math.max(b.high, c.high);
      b.low    = Math.min(b.low,  c.low);
      b.close  = c.close;
      b.volume = b.volume + (c.volume || 0);
    }
  }
  return Array.from(buckets.values()).sort((a, b) => a.time - b.time);
}

// ─── Dynamic price precision (Change 6a) ─────────────────────────────────────
function getPriceFormat(price) {
  if (!price || price <= 0) return { precision: 2, minMove: 0.01 };
  if (price < 0.0001)  return { precision: 8, minMove: 0.00000001 };
  if (price < 0.001)   return { precision: 6, minMove: 0.000001   };
  if (price < 0.1)     return { precision: 5, minMove: 0.00001    };
  if (price < 1)       return { precision: 4, minMove: 0.0001     };
  if (price < 10)      return { precision: 3, minMove: 0.001      };
  return                      { precision: 2, minMove: 0.01       };
}

// ─── TradingView-style time axis formatter (Change 6b) ────────────────────────
// tickMarkType: 0=Year 1=Month 2=DayOfMonth 3=Time 4=TimeWithSeconds
const pad = (n) => String(n).padStart(2, '0');
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function tvTickFormatter(unixSeconds, tickMarkType) {
  const d = new Date(unixSeconds * 1000);
  switch (tickMarkType) {
    case 0:  return String(d.getUTCFullYear());
    case 1:  return MONTHS[d.getUTCMonth()];
    case 2:  return `${pad(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]}`;
    case 3:  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
    default: return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  }
}

// ─── Indicator definitions ────────────────────────────────────────────────────
const INDICATORS = [
  { key: 'ema9',   label: 'EMA 9',   color: '#f59e0b' },
  { key: 'ema21',  label: 'EMA 21',  color: '#8b5cf6' },
  { key: 'ema50',  label: 'EMA 50',  color: '#06b6d4' },
  { key: 'sma200', label: 'SMA 200', color: '#f97316' },
  { key: 'bb',     label: 'BB 20',   color: '#6366f1' },
  { key: 'vwap',   label: 'VWAP',    color: '#ec4899' },
  { key: 'volume', label: 'Volume',  color: '#00b0ff' },
  { key: 'rsi',    label: 'RSI 14',  color: '#fbbf24' },
  { key: 'macd',   label: 'MACD',    color: '#34d399' },
  { key: 'atr',    label: 'ATR 14',  color: '#94a3b8' },
];

const SIGNAL_STYLES = {
  'STRONG BUY':  { bg: 'rgba(16,185,129,0.2)',  border: '#10b981', color: '#10b981', dot: '#10b981' },
  'BUY':         { bg: 'rgba(16,185,129,0.1)',  border: '#6ee7b7', color: '#6ee7b7', dot: '#6ee7b7' },
  'NEUTRAL':     { bg: 'rgba(148,163,184,0.1)', border: '#94a3b8', color: '#94a3b8', dot: '#94a3b8' },
  'SELL':        { bg: 'rgba(239,68,68,0.1)',   border: '#fca5a5', color: '#fca5a5', dot: '#fca5a5' },
  'STRONG SELL': { bg: 'rgba(239,68,68,0.2)',   border: '#ef4444', color: '#ef4444', dot: '#ef4444' },
};

// Bars of right-side breathing room after the latest candle
const RIGHT_BARS = 30;
// Default visible bar count on full reload
const VISIBLE_BARS = 120;

// ─── Chart factory ────────────────────────────────────────────────────────────
function makeChart(container, showTimeAxis = false) {
  return createChart(container, {
    width:  container.clientWidth  || 800,
    height: container.clientHeight || 400,
    layout: {
      background: { type: 'solid', color: '#080d14' },
      textColor:  '#9ca3af',
      fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
      fontSize:   11,
    },
    grid: {
      vertLines: { color: 'rgba(255,255,255,0.04)' },
      horzLines: { color: 'rgba(255,255,255,0.04)' },
    },
    rightPriceScale: {
      borderColor:    'rgba(255,255,255,0.08)',
      visible:        true,
      scaleMargins:   { top: 0.08, bottom: 0.08 },
      minimumWidth:   72,
      entireTextOnly: true,
      autoScale:      true,
    },
    leftPriceScale: { visible: false },
    timeScale: {
      borderColor:      'rgba(255,255,255,0.08)',
      timeVisible:      true,
      secondsVisible:   false,
      visible:          showTimeAxis,
      fixLeftEdge:      false,
      fixRightEdge:     false,
      shiftVisibleRangeOnNewBar: true, // Native right-edge tracking for live ticks
      rightOffset:      RIGHT_BARS,    // Native right-side breathing room
      tickMarkFormatter: tvTickFormatter, // Change 6b
    },
    crosshair: {
      mode: 1,
      vertLine: { color: 'rgba(100,140,220,0.6)', width: 1, style: 2, labelBackgroundColor: '#1d4ed8' },
      horzLine: { color: 'rgba(100,140,220,0.6)', width: 1, style: 2, labelBackgroundColor: '#1d4ed8' },
    },
    kineticScroll: { touch: true, mouse: false },
    handleScroll:  { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale:   { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
  });
}

// ─── Sync visible time range across all panes ─────────────────────────────────
function syncCharts(chartList) {
  let isSyncing = false;
  chartList.forEach((src, si) => {
    src.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (isSyncing || !range) return;
      isSyncing = true;
      chartList.forEach((dst, di) => {
        if (di !== si) try { dst.timeScale().setVisibleLogicalRange(range); } catch (_) {}
      });
      isSyncing = false;
    });
  });
}

// ─── Sync crosshair cursor across panes ──────────────────────────────────────
function syncCrosshairs(chartList, seriesList) {
  let isSyncing = false;
  chartList.forEach((src, si) => {
    src.subscribeCrosshairMove(param => {
      if (isSyncing) return;
      isSyncing = true;
      try {
        chartList.forEach((dst, di) => {
          if (di !== si) {
            if (!param.time || !param.point) {
              try { dst.setCrosshairPosition(NaN, null, seriesList[di]); } catch (_) {}
            } else {
              try { dst.setCrosshairPosition(null, param.time, seriesList[di]); } catch (_) {}
            }
          }
        });
      } finally { isSyncing = false; }
    });
  });
}

// ─── Update which pane shows the time axis (lowest visible) ──────────────────
// Change 6d
function updateTimeAxisVisibility(charts, active) {
  const order = [
    { chart: charts.atr,  visible: active.atr  },
    { chart: charts.macd, visible: active.macd },
    { chart: charts.rsi,  visible: active.rsi  },
    { chart: charts.main, visible: true         },
  ];
  const bottom = order.find(p => p.visible) ?? order[3];
  Object.values(charts).forEach(c => {
    if (!c) return;
    try { c.applyOptions({ timeScale: { visible: c === bottom.chart } }); } catch (_) {}
  });
}

// ─── Volume bar colour helper ─────────────────────────────────────────────────
const volColor = (c) => c.close >= c.open ? 'rgba(16,185,129,0.35)' : 'rgba(239,68,68,0.35)';

// ─── Format a price value for the OHLCV legend (Change 6c) ───────────────────
function fmtPrice(val, fmt) {
  if (val == null) return '—';
  return val.toFixed(fmt?.precision ?? 2);
}
function fmtVol(v) {
  if (!v) return '—';
  if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B';
  if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return v.toFixed(2);
}

const EMPTY_ARRAY = [];

// ─── Component ───────────────────────────────────────────────────────────────
export default function ChartWidget() {
  const mainRef = useRef(null);
  const rsiRef  = useRef(null);
  const macdRef = useRef(null);
  const atrRef  = useRef(null);

  const charts = useRef({});
  const series = useRef({});

  // Change 1+2+3+4+5: all stability refs
  const activeKeyRef      = useRef('');      // composite key — change forces full setData
  const activeRef         = useRef({});      // mirrors `active` state without stale closure
  const chartTypeRef      = useRef('candle');
  const isAtRightRef      = useRef(true);    // true = user is at the right edge
  const isProgrammaticRef = useRef(false);   // Change 2: suppresses range subscription on our own scrolls
  const lastBarTimeRef    = useRef(0);       // Change 4+5: tracks last completed bar timestamp
  const candlesLengthRef  = useRef(0);
  const resizeRafRef      = useRef(null);
  const priceFormatRef    = useRef({ precision: 2, minMove: 0.01 }); // Change 6a

  // Position price lines
  const entryLineRef = useRef(null);
  const slLineRef    = useRef(null);
  const tpLineRef    = useRef(null);

  const activeSymbol = useStore(state => state.activeSymbol);
  const symbolCandles = useStore(state => state.candleData[activeSymbol] || EMPTY_ARRAY);
  const symbolTicker = useStore(state => state.tickerData[activeSymbol]);
  const symbolPosition = useStore(state => state.positions[activeSymbol]);
  const tradeHistory = useStore(state => state.tradeHistory);
  const botConfig = useStore(state => state.botConfig);

  // ── State ────────────────────────────────────────────────────────────────
  const [active, setActive] = useState(() => {
    try {
      const s = localStorage.getItem('terminal_chart_indicators_active');
      if (s) return JSON.parse(s);
    } catch (_) {}
    return { ema9: true, ema21: true, ema50: false, sma200: false, bb: true, vwap: false, rsi: true, macd: true, atr: false, volume: true };
  });

  const [signal, setSignal]           = useState({ signal: 'NEUTRAL', score: 0, reasons: [] });
  const [showReasons, setShowReasons] = useState(false);
  const [indicatorValues, setIndicatorValues] = useState({});
  const [timeframe, setTimeframe]     = useState(() => { try { return localStorage.getItem('terminal_tf') || '1m'; } catch { return '1m'; } });
  const [chartType, setChartType]     = useState('candle');
  const [hoveredBar, setHoveredBar]   = useState(null); // Change 6c: OHLCV legend
  const [isLiveBtn, setIsLiveBtn]     = useState(false); // show ▶▶ Live button

  // Keep refs in sync
  activeRef.current    = active;
  chartTypeRef.current = chartType;

  // Persist preferences
  useEffect(() => { try { localStorage.setItem('terminal_tf', timeframe); } catch {} }, [timeframe]);
  useEffect(() => { try { localStorage.setItem('terminal_chart_indicators_active', JSON.stringify(active)); } catch {} }, [active]);

  const toggle = useCallback((key) => setActive(prev => ({ ...prev, [key]: !prev[key] })), []);

  // ── Aggregated candle data ────────────────────────────────────────────────
  const aggregatedCandles = useMemo(() => {
    const raw = symbolCandles;
    if (!raw || raw.length === 0) return [];
    const cfg = TF_CONFIGS.find(t => t.label === timeframe) || TF_CONFIGS[0];
    return aggregateCandles(raw, cfg.secs);
  }, [symbolCandles, timeframe]);

  // ── Centralised resize (debounced via rAF) ────────────────────────────────
  const doResize = useCallback(() => {
    if (resizeRafRef.current) cancelAnimationFrame(resizeRafRef.current);
    resizeRafRef.current = requestAnimationFrame(() => {
      const c = charts.current;
      if (!c.main) return;
      const w = mainRef.current?.clientWidth;
      if (!w || w < 10) return;
      const mh = mainRef.current?.clientHeight || 400;
      try { c.main.resize(w, mh, true); } catch (_) {}
      if (c.rsi  && rsiRef.current)  try { c.rsi.resize(w,  rsiRef.current.clientHeight  || 130, true); } catch (_) {}
      if (c.macd && macdRef.current) try { c.macd.resize(w, macdRef.current.clientHeight || 130, true); } catch (_) {}
      if (c.atr  && atrRef.current)  try { c.atr.resize(w,  atrRef.current.clientHeight  || 130, true); } catch (_) {}
    });
  }, []);

  // ── Chart Init ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!mainRef.current || !rsiRef.current || !macdRef.current || !atrRef.current) return;

    // Change 6d: all panes start with timeAxis hidden; updateTimeAxisVisibility picks the right one
    const mainChart = makeChart(mainRef.current,  false);
    const rsiChart  = makeChart(rsiRef.current,   false);
    const macdChart = makeChart(macdRef.current,  false);
    const atrChart  = makeChart(atrRef.current,   false);

    // Main series
    const candleSeries = mainChart.addSeries(CandlestickSeries, {
      upColor: '#10b981', downColor: '#ef4444',
      borderUpColor: '#10b981', borderDownColor: '#ef4444',
      wickUpColor: '#10b981', wickDownColor: '#ef4444',
      lastValueVisible: true,   // Change 6a: floating price chip on right axis
      priceLineVisible: true,   // Change 6a: dashed last-price line
      priceLineStyle:   2,
      priceLineWidth:   1,
      priceLineColor:   '#3b82f6',
    });
    chartTypeRef.current = 'candle';

    // Overlay series helper
    const mkLine = (chart, color, w = 1, style = 0) =>
      chart.addSeries(LineSeries, { color, lineWidth: w, lineStyle: style, priceLineVisible: false, lastValueVisible: false });

    const ema9Line    = mkLine(mainChart, '#f59e0b');
    const ema21Line   = mkLine(mainChart, '#8b5cf6');
    const ema50Line   = mkLine(mainChart, '#06b6d4');
    const sma200Line  = mkLine(mainChart, '#f97316');
    const bbUpperLine = mkLine(mainChart, '#6366f1', 1, 2);
    const bbMidLine   = mkLine(mainChart, '#6366f160', 1, 3);
    const bbLowerLine = mkLine(mainChart, '#6366f1', 1, 2);
    const vwapLine    = mkLine(mainChart, '#ec4899', 2, 1);

    const volumeSeries = mainChart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' }, priceScaleId: 'volume',
    });
    mainChart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, visible: false });

    // RSI pane
    const rsiLine = rsiChart.addSeries(LineSeries, {
      color: '#fbbf24', lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
      autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
    });
    const rsi70 = mkLine(rsiChart, 'rgba(239,68,68,0.4)',   1, 3);
    const rsi50 = mkLine(rsiChart, 'rgba(148,163,184,0.3)', 1, 3);
    const rsi30 = mkLine(rsiChart, 'rgba(16,185,129,0.4)',  1, 3);

    // MACD pane
    const macdLine = macdChart.addSeries(LineSeries, { color: '#34d399', lineWidth: 2, priceLineVisible: false, lastValueVisible: true });
    const macdSig  = macdChart.addSeries(LineSeries, { color: '#f87171', lineWidth: 2, priceLineVisible: false, lastValueVisible: true });
    const macdHist = macdChart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false });
    const macdZero = mkLine(macdChart, 'rgba(148,163,184,0.3)', 1, 3);

    // ATR pane
    const atrLine = atrChart.addSeries(LineSeries, { color: '#94a3b8', lineWidth: 2, priceLineVisible: false, lastValueVisible: true });

    charts.current = { main: mainChart, rsi: rsiChart, macd: macdChart, atr: atrChart };
    series.current = {
      candle: candleSeries,
      ema9: ema9Line, ema21: ema21Line, ema50: ema50Line, sma200: sma200Line,
      bbUpper: bbUpperLine, bbMid: bbMidLine, bbLower: bbLowerLine, vwap: vwapLine,
      rsi: rsiLine, rsi70, rsi50, rsi30,
      macdLine, macdSig, macdHist, macdZero,
      atr: atrLine, volume: volumeSeries,
    };

    // Cross-pane sync
    syncCharts([mainChart, rsiChart, macdChart, atrChart]);
    syncCrosshairs(
      [mainChart, rsiChart, macdChart, atrChart],
      [candleSeries, rsiLine, macdLine, atrLine]
    );

    mainChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (isProgrammaticRef.current || !range) return;
      const dataLength = candlesLengthRef.current;
      const atRight = range.to >= (dataLength - 1 - 2); // 2 bars buffer
      isAtRightRef.current = atRight;
      setIsLiveBtn(!atRight);
    });

    // Change 6c: OHLCV crosshair subscription on main chart
    mainChart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.point) {
        setHoveredBar(null);
        return;
      }
      try {
        const data = param.seriesData.get(candleSeries);
        if (data) setHoveredBar(data);
      } catch (_) {}
    });

    // ResizeObserver
    const ro = new ResizeObserver(doResize);
    [mainRef, rsiRef, macdRef, atrRef].forEach(r => { if (r.current) ro.observe(r.current); });
    window.addEventListener('resize', doResize);

    // Tab/focus visibility fix
    const onVisibility = () => { if (!document.hidden) doResize(); };
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('focus', doResize);

    doResize();

    return () => {
      window.removeEventListener('resize', doResize);
      window.removeEventListener('focus', doResize);
      document.removeEventListener('visibilitychange', onVisibility);
      ro.disconnect();
      if (resizeRafRef.current) cancelAnimationFrame(resizeRafRef.current);
      [mainChart, rsiChart, macdChart, atrChart].forEach(c => { try { c.remove(); } catch (_) {} });
      charts.current  = {};
      series.current  = {};
      activeKeyRef.current   = '';
      lastBarTimeRef.current = 0;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Re-trigger resize + time axis update when sub-panes show/hide ─────────
  useEffect(() => {
    const id = setTimeout(() => {
      doResize();
      // Change 6d: update which pane shows the time axis
      updateTimeAxisVisibility(charts.current, activeRef.current);
    }, 60);
    return () => clearTimeout(id);
  }, [active.rsi, active.macd, active.atr, doResize]);

  // ── Programmatic scroll helper ────────────────────────────────────────────
  const programmaticScroll = useCallback((fn) => {
    isProgrammaticRef.current = true;
    try { fn(); } catch (_) {}
    requestAnimationFrame(() => { isProgrammaticRef.current = false; });
  }, []);

  // ── Main render ───────────────────────────────────────────────────────────
  const renderAll = useCallback((candles, compositeKey) => {
    if (!candles || candles.length === 0) return; // Allow rendering with just 1 candle
    if (!series.current.candle || !charts.current.main) return;

    candlesLengthRef.current = candles.length;
    const s        = series.current;
    const cur      = activeRef.current;
    const reqType  = chartTypeRef.current;
    const prevKey  = activeKeyRef.current;
    const isFullReload = prevKey !== compositeKey;
    const prevSym  = prevKey ? prevKey.split('::')[0] : '';
    const curSym   = compositeKey.split('::')[0];
    const isSymbolChange = prevSym !== curSym;

    let preservedBars = null;
    let preservedRightTime = null;

    // ── Change 5: Reset state on full reload ──────────────────────────────
    if (isFullReload) {
      lastBarTimeRef.current = 0;
      if (isSymbolChange) {
        isAtRightRef.current = true;
        setIsLiveBtn(false);
      } else {
        if (charts.current.main) {
          try {
            const timeRange = charts.current.main.timeScale().getVisibleTimeRange();
            const logicalRange = charts.current.main.timeScale().getVisibleLogicalRange();
            if (timeRange && logicalRange) {
              preservedRightTime = timeRange.to;
              preservedBars = logicalRange.to - logicalRange.from;
            }
          } catch (_) {}
        }
      }
    }

    // ── Series type swap (candle ↔ line) ──────────────────────────────────
    if (isFullReload && s.candle._seriesType !== reqType) {
      try { charts.current.main.removeSeries(s.candle); } catch (_) {}
      let newS;
      if (reqType === 'line') {
        newS = charts.current.main.addSeries(LineSeries, {
          color: '#3b82f6', lineWidth: 2,
          lastValueVisible: true, priceLineVisible: true,
          priceLineStyle: 2, priceLineWidth: 1, priceLineColor: '#3b82f6',
        });
      } else {
        newS = charts.current.main.addSeries(CandlestickSeries, {
          upColor: '#10b981', downColor: '#ef4444',
          borderUpColor: '#10b981', borderDownColor: '#ef4444',
          wickUpColor: '#10b981', wickDownColor: '#ef4444',
          lastValueVisible: true, priceLineVisible: true,
          priceLineStyle: 2, priceLineWidth: 1, priceLineColor: '#3b82f6',
        });
      }
      newS._seriesType = reqType;
      s.candle = newS;
      series.current.candle = newS;
    }

    const volumeData = candles.map(c => ({ time: c.time, value: c.volume || 0, color: volColor(c) }));

    // ── Change 6a: Dynamic price format per asset ─────────────────────────
    const lastClose = candles[candles.length - 1]?.close;
    const fmt = getPriceFormat(lastClose);
    priceFormatRef.current = fmt;

    // ── Full reload path ──────────────────────────────────────────────────
    if (isFullReload) {
      const chartData = reqType === 'line'
        ? candles.map(c => ({ time: c.time, value: c.close }))
        : candles;
      try { s.candle.setData(chartData); } catch (_) {}
      try { s.volume.setData(volumeData); } catch (_) {}

      // Apply dynamic price format to the series (Change 6a)
      try { s.candle.applyOptions({ priceFormat: { type: 'price', ...fmt } }); } catch (_) {}

      activeKeyRef.current = compositeKey;

      // Change 3: Force native layout via barSpacing to prevent left-corner clamping!
      // If dataset is sparse, center it dynamically using rightOffset.
      // If historical panning, use setVisibleLogicalRange to anchor exactly.
      requestAnimationFrame(() => {
        programmaticScroll(() => {
          const n = candles.length;
          let chartWidth = 800;
          try { chartWidth = charts.current.main.timeScale().width() || mainRef.current?.clientWidth || 800; } catch (_) {}
          const targetBarSpacing = chartWidth / VISIBLE_BARS;

          if (n < VISIBLE_BARS) {
            // Sparse timeframe (e.g. 1D with only 5 days of data). Center the candles gracefully!
            const pad = (VISIBLE_BARS - n) / 2;
            charts.current.main.timeScale().applyOptions({ barSpacing: targetBarSpacing, rightOffset: pad });
            try { charts.current.main.timeScale().scrollToRealTime(); } catch (_) {}
          } else {
            // Full timeframe.
            if (isSymbolChange || !preservedRightTime || isAtRightRef.current) {
              charts.current.main.timeScale().applyOptions({ barSpacing: targetBarSpacing, rightOffset: RIGHT_BARS });
              try { charts.current.main.timeScale().scrollToRealTime(); } catch (_) {}
            } else {
              // Historical panning anchored to exact time!
              let newRightIndex = n - 1;
              for (let i = n - 1; i >= 0; i--) {
                if (candles[i].time <= preservedRightTime) {
                  newRightIndex = i;
                  break;
                }
              }
              const to = newRightIndex;
              const from = to - VISIBLE_BARS;
              try { charts.current.main.timeScale().setVisibleLogicalRange({ from, to }); } catch (_) {}
            }
          }
          
          // Force price-scale autoscale reset on all active charts
          Object.values(charts.current).forEach(c => {
            if (c) {
              try { c.priceScale('right').applyOptions({ autoScale: true }); } catch (_) {}
            }
          });
        });
      });

    } else {
      // ── Change 4: Live tick path ──────────────────────────────────────
      const last     = candles[candles.length - 1];
      const isNewBar = last.time !== lastBarTimeRef.current;

      try {
        if (reqType === 'line') {
          s.candle.update({ time: last.time, value: last.close });
        } else {
          s.candle.update(last);
        }
        s.volume.update({ time: last.time, value: last.volume || 0, color: volColor(last) });
      } catch (_) {}

      // Change 6a: update price-line colour to match last candle direction
      const plColor = last.close >= last.open ? '#10b981' : '#ef4444';
      try { s.candle.applyOptions({ priceLineColor: plColor }); } catch (_) {}

      // Native shiftVisibleRangeOnNewBar tracks right edge smoothly without drifting!

      // Change 4: only run full indicator setData when a new bar closes
      if (!isNewBar) {
        // Intra-bar tick: skip expensive indicator setData — just update visibility
        const vis = (key, refs) => refs.forEach(r => { try { r?.applyOptions({ visible: cur[key] }); } catch (_) {} });
        vis('ema9', [s.ema9]); vis('ema21', [s.ema21]); vis('ema50', [s.ema50]);
        vis('sma200', [s.sma200]); vis('bb', [s.bbUpper, s.bbMid, s.bbLower]);
        vis('vwap', [s.vwap]); vis('volume', [s.volume]);
        vis('rsi',  [s.rsi, s.rsi70, s.rsi50, s.rsi30]);
        vis('macd', [s.macdLine, s.macdSig, s.macdHist, s.macdZero]);
        vis('atr',  [s.atr]);
        return; // early exit — no indicator recalculation
      }
      lastBarTimeRef.current = last.time;
    }

    // ── Full indicator recalculation (on full reload OR new bar close) ────
    const setIfData = (ser, data) => { if (data?.length) try { ser.setData(data); } catch (_) {} };

    const ema9d   = calcEMA(candles, 9);
    const ema21d  = calcEMA(candles, 21);
    const ema50d  = calcEMA(candles, 50);
    const sma200d = calcSMA(candles, 200);
    const bbData  = calcBollingerBands(candles, 20, 2);
    const vwapD   = calcVWAP(candles);

    setIfData(s.ema9,    ema9d);
    setIfData(s.ema21,   ema21d);
    setIfData(s.ema50,   ema50d);
    setIfData(s.sma200,  sma200d);
    setIfData(s.bbUpper, bbData.upper);
    setIfData(s.bbMid,   bbData.middle);
    setIfData(s.bbLower, bbData.lower);
    setIfData(s.vwap,    vwapD);

    const vis = (key, refs) => refs.forEach(r => { try { r?.applyOptions({ visible: cur[key] }); } catch (_) {} });
    vis('ema9',   [s.ema9]);   vis('ema21', [s.ema21]); vis('ema50', [s.ema50]);
    vis('sma200', [s.sma200]); vis('bb',    [s.bbUpper, s.bbMid, s.bbLower]);
    vis('vwap',   [s.vwap]);   vis('volume',[s.volume]);

    const rsiData = calcRSI(candles, 14);
    if (rsiData.length > 0) {
      const refLine = (v) => rsiData.map(p => ({ time: p.time, value: v }));
      setIfData(s.rsi,   rsiData);
      setIfData(s.rsi70, refLine(70));
      setIfData(s.rsi50, refLine(50));
      setIfData(s.rsi30, refLine(30));
    }
    vis('rsi', [s.rsi, s.rsi70, s.rsi50, s.rsi30]);

    const macdData = calcMACD(candles, 12, 26, 9);
    if (macdData.macdLine.length > 0) {
      setIfData(s.macdLine, macdData.macdLine);
      setIfData(s.macdSig,  macdData.signalLine);
      setIfData(s.macdHist, macdData.histogram);
      setIfData(s.macdZero, macdData.macdLine.map(p => ({ time: p.time, value: 0 })));
    }
    vis('macd', [s.macdLine, s.macdSig, s.macdHist, s.macdZero]);

    const atrData = calcATR(candles, 14);
    setIfData(s.atr, atrData);
    vis('atr', [s.atr]);

    setSignal(generateSignal(candles));
    setIndicatorValues({
      ema9:    ema9d.at(-1)?.value?.toFixed(fmt.precision),
      ema21:   ema21d.at(-1)?.value?.toFixed(fmt.precision),
      ema50:   ema50d.at(-1)?.value?.toFixed(fmt.precision),
      sma200:  sma200d.at(-1)?.value?.toFixed(fmt.precision),
      bbUpper: bbData.upper.at(-1)?.value?.toFixed(fmt.precision),
      bbLower: bbData.lower.at(-1)?.value?.toFixed(fmt.precision),
      rsi:     rsiData.at(-1)?.value?.toFixed(1),
      macd:    macdData.macdLine.at(-1)?.value?.toFixed(4),
      macdSig: macdData.signalLine.at(-1)?.value?.toFixed(4),
      vwap:    vwapD.at(-1)?.value?.toFixed(fmt.precision),
      atr:     atrData.at(-1)?.value?.toFixed(4),
      volume:  fmtVol(candles.at(-1)?.volume),
    });
  }, []); // no React deps — reads everything through refs

  // ── Trigger render on data / symbol / TF / type / indicator changes ───────
  useEffect(() => {
    if (!aggregatedCandles || aggregatedCandles.length === 0) return;
    const key = `${activeSymbol}::${timeframe}::${chartType}`;
    renderAll(aggregatedCandles, key);
  }, [aggregatedCandles, activeSymbol, timeframe, chartType, active, renderAll]);

  // ── Position lines + execution markers ───────────────────────────────────
  useEffect(() => {
    const cs = series.current.candle;
    if (!cs) return;
    [entryLineRef, slLineRef, tpLineRef].forEach(ref => {
      if (ref.current) { try { cs.removePriceLine(ref.current); } catch (_) {} ref.current = null; }
    });
    const pos = symbolPosition;
    if (pos && pos.size !== 0) {
      const isLong = pos.size > 0;
      entryLineRef.current = cs.createPriceLine({
        price: pos.avg_price, color: '#3b82f6', lineWidth: 2, lineStyle: 0,
        axisLabelVisible: true,
        title: `ENTRY ${isLong ? '▲ LONG' : '▼ SHORT'} (${Math.abs(pos.size).toFixed(4)})`,
      });
      if (pos.stop_loss_price > 0) {
        slLineRef.current = cs.createPriceLine({
          price: pos.stop_loss_price, color: '#ef4444', lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title: 'SL',
        });
      }
      if (pos.take_profit_price > 0) {
        tpLineRef.current = cs.createPriceLine({
          price: pos.take_profit_price, color: '#10b981', lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title: 'TP',
        });
      }
    }
    const markers = tradeHistory
      .filter(t => t.symbol === activeSymbol && t.status === 'FILLED')
      .map(t => ({
        time:     Math.floor(new Date(t.timestamp).getTime() / 1000),
        position: t.side === 'BUY' ? 'belowBar' : 'aboveBar',
        color:    t.side === 'BUY' ? '#10b981'  : '#ef4444',
        shape:    t.side === 'BUY' ? 'arrowUp'  : 'arrowDown',
        text:     `${t.side} ${(t.filled_quantity ?? t.quantity)?.toFixed(4)} @ ${(t.average_fill_price || t.price)?.toLocaleString()}`,
        size: 1.2,
      }))
      .sort((a, b) => a.time - b.time);
    try { cs.setMarkers(markers); } catch (_) {}
  }, [activeSymbol, symbolPosition, tradeHistory, botConfig]);

  // ─── Render ───────────────────────────────────────────────────────────────
  const ticker   = symbolTicker;
  const sigStyle = SIGNAL_STYLES[signal.signal] || SIGNAL_STYLES.NEUTRAL;
  const isStrong = signal.signal.startsWith('STRONG');
  const showRsi  = active.rsi;
  const showMacd = active.macd;
  const showAtr  = active.atr;
  const subPaneH = 130;
  const fmt      = priceFormatRef.current;

  // Change 6c: bar to display in OHLCV legend (hovered or last)
  const lastCandle = aggregatedCandles?.at(-1) ?? null;
  const legendBar  = hoveredBar ?? lastCandle;
  const legendBull = legendBar ? legendBar.close >= legendBar.open : true;

  const pDec = (sym, price) =>
    sym?.includes('XRP') || sym?.includes('ADA') || sym?.includes('DOGE') || (price != null && price < 2) ? 4 : 2;

  return (
    <div className="widget-card" style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

      {/* ── Row 1: Symbol + Price + Signal ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '9px 14px', borderBottom: '1px solid var(--border-color)', background: '#080d14', flexShrink: 0, gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
          <AreaChart size={15} className="logo-icon" style={{ flexShrink: 0 }} />
          <span style={{ fontWeight: 800, color: '#fff', fontSize: 'var(--fs-md)', letterSpacing: '0.5px', flexShrink: 0 }}>
            {activeSymbol}
          </span>

          {ticker && (() => {
            const dec = pDec(activeSymbol, ticker.price);
            return (
              <div style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: 'var(--fs-sm)', minWidth: 0, overflow: 'hidden' }}>
                <span className="num-mono" style={{ fontSize: 'var(--fs-lg)', fontWeight: 800, color: ticker.change_24h >= 0 ? 'var(--color-up)' : 'var(--color-down)', flexShrink: 0 }}>
                  {ticker.price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
                </span>
                <span className={`num-mono ${ticker.change_24h >= 0 ? 'text-up' : 'text-down'}`} style={{ fontWeight: 700, flexShrink: 0 }}>
                  {ticker.change_24h >= 0 ? '+' : ''}{Number(ticker.change_24h).toFixed(2)}%
                </span>
                <span style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-xs)', whiteSpace: 'nowrap' }}>
                  H:<span className="num-mono"> {ticker.high_24h?.toFixed(dec)}</span>
                  {' '}L:<span className="num-mono"> {ticker.low_24h?.toFixed(dec)}</span>
                  {' '}V:<span className="num-mono"> {ticker.volume_24h ? fmtVol(ticker.volume_24h) : '—'}</span>
                </span>
              </div>
            );
          })()}
        </div>

        {/* Signal badge */}
        <div style={{ position: 'relative', flexShrink: 0 }}>
          <button onClick={() => setShowReasons(p => !p)} style={{
            display: 'flex', alignItems: 'center', gap: 7, padding: '4px 12px',
            borderRadius: 20, cursor: 'pointer',
            background: sigStyle.bg, border: `1px solid ${sigStyle.border}`,
            color: sigStyle.color, fontWeight: 700, fontSize: 'var(--fs-xs)',
            letterSpacing: '0.5px', fontFamily: 'var(--font-sans)',
          }}>
            <span style={{
              width: 6, height: 6, borderRadius: '50%', background: sigStyle.dot,
              boxShadow: isStrong ? `0 0 8px ${sigStyle.dot}` : 'none',
              animation: isStrong ? 'pulse-glow 1.5s ease-in-out infinite' : 'none',
            }} />
            {signal.signal}
            <span style={{ fontSize: 'var(--fs-2xs)', opacity: 0.7 }}>
              ({signal.score > 0 ? '+' : ''}{signal.score})
            </span>
          </button>
          {showReasons && signal.reasons.length > 0 && (
            <div style={{
              position: 'absolute', right: 0, top: 'calc(100% + 6px)', zIndex: 300,
              background: '#101827', border: `1px solid ${sigStyle.border}`,
              borderRadius: 8, padding: '10px 14px', minWidth: 230,
              boxShadow: '0 16px 48px rgba(0,0,0,0.75)', fontSize: 'var(--fs-xs)',
            }}>
              <div style={{ color: 'var(--text-muted)', marginBottom: 6, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.6px', fontSize: 'var(--fs-2xs)' }}>Signal Analysis</div>
              {signal.reasons.map((r, i) => (
                <div key={i} style={{ color: sigStyle.color, marginBottom: 3, display: 'flex', gap: 7 }}>
                  <span style={{ opacity: 0.4 }}>•</span><span>{r}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── Row 2: Timeframe + Chart Type + Live button ── */}
      <div style={{ display: 'flex', alignItems: 'center', padding: '0 12px', background: 'rgba(6,10,18,0.98)', borderBottom: '1px solid var(--border-color)', flexShrink: 0, height: 33 }}>
        <div className="timeframe-tabs">
          {TF_CONFIGS.map(tf => (
            <button key={tf.label} className={`tf-btn${timeframe === tf.label ? ' active' : ''}`} onClick={() => setTimeframe(tf.label)}>
              {tf.label}
            </button>
          ))}
        </div>
        <div style={{ width: 1, height: 14, background: 'rgba(255,255,255,0.08)', margin: '0 8px', flexShrink: 0 }} />
        {[{ key: 'candle', label: 'Candle', Icon: AreaChart }, { key: 'line', label: 'Line', Icon: TrendingUp }].map(({ key, label, Icon }) => (
          <button key={key} onClick={() => setChartType(key)} style={{
            display: 'flex', alignItems: 'center', gap: 4, padding: '3px 8px',
            borderRadius: 'var(--r-sm)', border: 'none', cursor: 'pointer',
            fontFamily: 'var(--font-sans)', fontSize: 'var(--fs-2xs)', fontWeight: 700,
            background: chartType === key ? 'rgba(37,99,235,0.25)' : 'transparent',
            color: chartType === key ? '#60a5fa' : 'var(--text-muted)',
            transition: 'all 0.15s',
          }}>
            <Icon size={11} />{label}
          </button>
        ))}

        {/* ▶▶ Live button — appears when user has panned left */}
        {isLiveBtn && (
          <button onClick={() => {
            isAtRightRef.current = true;
            setIsLiveBtn(false);
            programmaticScroll(() => {
              try { charts.current.main?.timeScale().scrollToRealTime(); } catch (_) {}
            });
          }} style={{
            marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 4,
            padding: '3px 9px', borderRadius: 'var(--r-sm)',
            border: '1px solid rgba(37,99,235,0.5)', cursor: 'pointer',
            fontFamily: 'var(--font-sans)', fontSize: 'var(--fs-2xs)', fontWeight: 700,
            background: 'rgba(37,99,235,0.15)', color: '#60a5fa',
            animation: 'pulse-glow 2s ease-in-out infinite',
          }}>
            ▶▶ Live
          </button>
        )}
      </div>

      {/* ── Row 3: Indicator Toggles ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '4px 12px', flexWrap: 'wrap', background: 'rgba(8,13,20,0.95)', borderBottom: '1px solid var(--border-color)', flexShrink: 0 }}>
        {INDICATORS.map(ind => (
          <button key={ind.key} onClick={() => toggle(ind.key)} style={{
            display: 'flex', alignItems: 'center', gap: 4, padding: '2px 8px',
            borderRadius: 4, cursor: 'pointer', fontSize: 'var(--fs-2xs)', fontWeight: 600,
            whiteSpace: 'nowrap', fontFamily: 'var(--font-sans)',
            background: active[ind.key] ? `${ind.color}18` : 'rgba(255,255,255,0.02)',
            border: `1px solid ${active[ind.key] ? ind.color + '99' : 'rgba(255,255,255,0.07)'}`,
            color: active[ind.key] ? ind.color : 'var(--text-muted)',
            transition: 'all 0.15s',
          }}>
            <span style={{ width: 5, height: 5, borderRadius: '50%', background: active[ind.key] ? ind.color : 'rgba(255,255,255,0.2)', flexShrink: 0 }} />
            {ind.label}
            {indicatorValues[ind.key] && active[ind.key] && (
              <span className="num-mono" style={{ opacity: 0.75, fontSize: 'var(--fs-2xs)' }}> {indicatorValues[ind.key]}</span>
            )}
          </button>
        ))}
        {/* Quick chip readouts */}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 10, fontSize: 'var(--fs-2xs)' }}>
          {active.rsi  && indicatorValues.rsi  && <span style={{ color: parseFloat(indicatorValues.rsi) > 70 ? 'var(--color-down)' : parseFloat(indicatorValues.rsi) < 30 ? 'var(--color-up)' : '#fbbf24' }}>RSI <span className="num-mono">{indicatorValues.rsi}</span></span>}
          {active.macd && indicatorValues.macd  && <span style={{ color: '#34d399' }}>MACD <span className="num-mono">{indicatorValues.macd}</span></span>}
          {active.vwap && indicatorValues.vwap  && <span style={{ color: '#ec4899' }}>VWAP <span className="num-mono">{indicatorValues.vwap}</span></span>}
          {active.atr  && indicatorValues.atr   && <span style={{ color: '#94a3b8' }}>ATR  <span className="num-mono">{indicatorValues.atr}</span></span>}
        </div>
      </div>

      {/* ── Chart Panes ── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>

        {/* Main chart */}
        <div ref={mainRef} style={{ flex: 1, minHeight: 0, position: 'relative' }}>

          {/* ── Change 6c: OHLCV Legend (top-left, TradingView-style) ── */}
          {legendBar && (
            <div style={{
              position: 'absolute', top: 7, left: 10, zIndex: 10,
              display: 'flex', alignItems: 'center', gap: 12,
              pointerEvents: 'none', userSelect: 'none',
              fontSize: 11, fontFamily: 'var(--font-mono)',
            }}>
              {[
                ['O', legendBar.open],
                ['H', legendBar.high],
                ['L', legendBar.low],
                ['C', legendBar.close],
              ].map(([label, val]) => (
                <span key={label} style={{ display: 'flex', gap: 3 }}>
                  <span style={{ color: 'rgba(148,163,184,0.7)', fontWeight: 400 }}>{label}</span>
                  <span style={{
                    color: legendBull ? '#10b981' : '#ef4444',
                    fontWeight: 700,
                  }}>
                    {fmtPrice(val, fmt)}
                  </span>
                </span>
              ))}
              <span style={{ display: 'flex', gap: 3 }}>
                <span style={{ color: 'rgba(148,163,184,0.7)', fontWeight: 400 }}>V</span>
                <span style={{ color: '#60a5fa', fontWeight: 700 }}>{fmtVol(legendBar.volume)}</span>
              </span>
              {/* Candle delta % */}
              {legendBar.open > 0 && (
                <span style={{
                  color: legendBull ? '#10b981' : '#ef4444',
                  fontWeight: 700,
                  fontSize: 10,
                  opacity: 0.9,
                }}>
                  {legendBull ? '+' : ''}{(((legendBar.close - legendBar.open) / legendBar.open) * 100).toFixed(2)}%
                </span>
              )}
            </div>
          )}
        </div>

        {/* RSI */}
        <div style={{ flexShrink: 0, position: 'relative', borderTop: showRsi ? '1px solid var(--border-color)' : 'none', height: showRsi ? `${subPaneH}px` : 0, overflow: 'hidden', transition: 'height 0.15s ease' }}>
          <span className="chart-pane-label" style={{ color: '#fbbf24' }}>RSI(14)</span>
          <div ref={rsiRef} style={{ height: `${subPaneH}px` }} />
        </div>

        {/* MACD */}
        <div style={{ flexShrink: 0, position: 'relative', borderTop: showMacd ? '1px solid var(--border-color)' : 'none', height: showMacd ? `${subPaneH}px` : 0, overflow: 'hidden', transition: 'height 0.15s ease' }}>
          <span className="chart-pane-label" style={{ color: '#34d399' }}>MACD(12,26,9)</span>
          <div ref={macdRef} style={{ height: `${subPaneH}px` }} />
        </div>

        {/* ATR */}
        <div style={{ flexShrink: 0, position: 'relative', borderTop: showAtr ? '1px solid var(--border-color)' : 'none', height: showAtr ? `${subPaneH}px` : 0, overflow: 'hidden', transition: 'height 0.15s ease' }}>
          <span className="chart-pane-label" style={{ color: '#94a3b8' }}>ATR(14)</span>
          <div ref={atrRef} style={{ height: `${subPaneH}px` }} />
        </div>
      </div>
    </div>
  );
}
