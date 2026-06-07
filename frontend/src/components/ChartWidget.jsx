import React, { useEffect, useRef, useState, useCallback } from 'react';
import {
  createChart, CandlestickSeries, LineSeries, HistogramSeries
} from 'lightweight-charts';
import { useStore } from '../store/useStore';
import {
  calcSMA, calcEMA, calcBollingerBands, calcRSI, calcMACD, calcVWAP, calcATR, generateSignal
} from '../utils/indicators';
import { AreaChart } from 'lucide-react';

// ─── Indicator Definitions ────────────────────────────────────────────────────
const INDICATORS = [
  { key: 'ema9',   label: 'EMA 9',   color: '#f59e0b', pane: 'main' },
  { key: 'ema21',  label: 'EMA 21',  color: '#8b5cf6', pane: 'main' },
  { key: 'ema50',  label: 'EMA 50',  color: '#06b6d4', pane: 'main' },
  { key: 'sma200', label: 'SMA 200', color: '#f97316', pane: 'main' },
  { key: 'bb',     label: 'BB 20',   color: '#6366f1', pane: 'main' },
  { key: 'vwap',   label: 'VWAP',    color: '#ec4899', pane: 'main' },
  { key: 'rsi',    label: 'RSI 14',  color: '#fbbf24', pane: 'rsi'  },
  { key: 'macd',   label: 'MACD',    color: '#34d399', pane: 'macd' },
  { key: 'atr',    label: 'ATR 14',  color: '#94a3b8', pane: 'atr'  },
];

const SIGNAL_STYLES = {
  'STRONG BUY':  { bg: 'rgba(16,185,129,0.2)',  border: '#10b981', color: '#10b981', dot: '#10b981' },
  'BUY':         { bg: 'rgba(16,185,129,0.1)',  border: '#6ee7b7', color: '#6ee7b7', dot: '#6ee7b7' },
  'NEUTRAL':     { bg: 'rgba(148,163,184,0.1)', border: '#94a3b8', color: '#94a3b8', dot: '#94a3b8' },
  'SELL':        { bg: 'rgba(239,68,68,0.1)',   border: '#fca5a5', color: '#fca5a5', dot: '#fca5a5' },
  'STRONG SELL': { bg: 'rgba(239,68,68,0.2)',   border: '#ef4444', color: '#ef4444', dot: '#ef4444' },
};

// ─── Chart creation helper ─────────────────────────────────────────────────
function makeChart(container, height, showTimeAxis = false) {
  return createChart(container, {
    width: container.clientWidth,
    height,
    layout: {
      background: { type: 'solid', color: '#0b0f19' },
      textColor: '#9ca3af',
      fontFamily: 'Inter, sans-serif',
    },
    grid: {
      vertLines: { color: 'rgba(255,255,255,0.03)' },
      horzLines: { color: 'rgba(255,255,255,0.03)' },
    },
    rightPriceScale: {
      borderColor: 'rgba(255,255,255,0.08)',
      visible: true,
      scaleMargins: { top: 0.1, bottom: 0.1 },
      minimumWidth: 80,
    },
    leftPriceScale: { visible: false },
    timeScale: {
      borderColor: 'rgba(255,255,255,0.08)',
      timeVisible: true,
      secondsVisible: false,
      visible: showTimeAxis,
    },
    crosshair: {
      vertLine: { color: 'rgba(59,130,246,0.5)', width: 1, style: 3, labelBackgroundColor: '#2563eb' },
      horzLine: { color: 'rgba(59,130,246,0.5)', width: 1, style: 3, labelBackgroundColor: '#2563eb' },
    },
    handleScroll: true,
    handleScale: true,
  });
}

// ─── Sync crosshair time axis across panes ───────────────────────────────────
function syncCharts(charts) {
  charts.forEach((src, si) => {
    src.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (!range) return;
      charts.forEach((dst, di) => {
        if (di !== si) dst.timeScale().setVisibleLogicalRange(range);
      });
    });
  });
}

// ─── Component ───────────────────────────────────────────────────────────────
export default function ChartWidget() {
  const mainRef = useRef(null);
  const rsiRef  = useRef(null);
  const macdRef = useRef(null);
  const atrRef  = useRef(null);

  const charts  = useRef({});  // { main, rsi, macd, atr }
  const series  = useRef({});  // all series instances

  const activeSymbolRef = useRef('');
  const activeRef       = useRef({});  // always up-to-date active state

  // Price line references
  const entryLineRef = useRef(null);
  const slLineRef    = useRef(null);
  const tpLineRef    = useRef(null);

  const {
    activeSymbol, candleData, tickerData,
    positions, tradeHistory, botConfig
  } = useStore();

  const [active, setActive] = useState(() => {
    try {
      const saved = localStorage.getItem('terminal_chart_indicators_active');
      if (saved) {
        return JSON.parse(saved);
      }
    } catch (_) {}
    return {
      ema9: true, ema21: true, ema50: false,
      sma200: false, bb: true, vwap: false,
      rsi: true, macd: true, atr: false,
    };
  });
  const [signal, setSignal]             = useState({ signal: 'NEUTRAL', score: 0, reasons: [] });
  const [showReasons, setShowReasons]   = useState(false);
  const [indicatorValues, setIndicatorValues] = useState({});

  // keep ref in sync
  activeRef.current = active;

  useEffect(() => {
    try {
      localStorage.setItem('terminal_chart_indicators_active', JSON.stringify(active));
    } catch (_) {}
  }, [active]);

  const toggle = (key) => setActive(prev => ({ ...prev, [key]: !prev[key] }));

  // ─── Chart Init ───────────────────────────────────────────────────────────
  useEffect(() => {
    if (!mainRef.current || !rsiRef.current || !macdRef.current || !atrRef.current) return;

    const mainH = 420;
    const subH  = 130;

    const mainChart = makeChart(mainRef.current,  mainH, false);
    const rsiChart  = makeChart(rsiRef.current,   subH,  false);
    const macdChart = makeChart(macdRef.current,  subH,  false);
    const atrChart  = makeChart(atrRef.current,   subH,  true);

    // Main pane series
    const candleSeries = mainChart.addSeries(CandlestickSeries, {
      upColor: '#10b981', downColor: '#ef4444',
      borderUpColor: '#10b981', borderDownColor: '#ef4444',
      wickUpColor: '#10b981', wickDownColor: '#ef4444',
    });
    const ema9Line    = mainChart.addSeries(LineSeries, { color: '#f59e0b', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const ema21Line   = mainChart.addSeries(LineSeries, { color: '#8b5cf6', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const ema50Line   = mainChart.addSeries(LineSeries, { color: '#06b6d4', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const sma200Line  = mainChart.addSeries(LineSeries, { color: '#f97316', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const bbUpperLine = mainChart.addSeries(LineSeries, { color: '#6366f1', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
    const bbMidLine   = mainChart.addSeries(LineSeries, { color: '#6366f160', lineWidth: 1, lineStyle: 3, priceLineVisible: false, lastValueVisible: false });
    const bbLowerLine = mainChart.addSeries(LineSeries, { color: '#6366f1', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
    const vwapLine    = mainChart.addSeries(LineSeries, { color: '#ec4899', lineWidth: 2, lineStyle: 1, priceLineVisible: false, lastValueVisible: false });

    // RSI pane
    rsiChart.applyOptions({ rightPriceScale: { autoScale: false } });
    const rsiLine = rsiChart.addSeries(LineSeries, { color: '#fbbf24', lineWidth: 2, priceLineVisible: false, lastValueVisible: true });
    const rsi70   = rsiChart.addSeries(LineSeries, { color: 'rgba(239,68,68,0.4)',   lineWidth: 1, lineStyle: 3, priceLineVisible: false, lastValueVisible: false });
    const rsi50   = rsiChart.addSeries(LineSeries, { color: 'rgba(148,163,184,0.3)', lineWidth: 1, lineStyle: 3, priceLineVisible: false, lastValueVisible: false });
    const rsi30   = rsiChart.addSeries(LineSeries, { color: 'rgba(16,185,129,0.4)',  lineWidth: 1, lineStyle: 3, priceLineVisible: false, lastValueVisible: false });

    // MACD pane
    const macdLine = macdChart.addSeries(LineSeries,      { color: '#34d399', lineWidth: 2, priceLineVisible: false, lastValueVisible: true });
    const macdSig  = macdChart.addSeries(LineSeries,      { color: '#f87171', lineWidth: 2, priceLineVisible: false, lastValueVisible: true });
    const macdHist = macdChart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false });
    const macdZero = macdChart.addSeries(LineSeries,      { color: 'rgba(148,163,184,0.3)', lineWidth: 1, lineStyle: 3, priceLineVisible: false, lastValueVisible: false });

    // ATR pane
    const atrLine = atrChart.addSeries(LineSeries, { color: '#94a3b8', lineWidth: 2, priceLineVisible: false, lastValueVisible: true });

    charts.current = { main: mainChart, rsi: rsiChart, macd: macdChart, atr: atrChart };
    series.current = {
      candle: candleSeries,
      ema9: ema9Line, ema21: ema21Line, ema50: ema50Line, sma200: sma200Line,
      bbUpper: bbUpperLine, bbMid: bbMidLine, bbLower: bbLowerLine,
      vwap: vwapLine,
      rsi: rsiLine, rsi70, rsi50, rsi30,
      macdLine, macdSig, macdHist, macdZero,
      atr: atrLine,
    };

    syncCharts([mainChart, rsiChart, macdChart, atrChart]);

    // Resize — wrap in rAF to avoid ResizeObserver loop warnings
    const handleResize = () => {
      const w = mainRef.current?.clientWidth;
      if (!w) return;
      mainChart.resize(w, mainRef.current.clientHeight  || 420);
      rsiChart.resize(w,  rsiRef.current.clientHeight   || 130);
      macdChart.resize(w, macdRef.current.clientHeight  || 130);
      atrChart.resize(w,  atrRef.current.clientHeight   || 130);
    };
    const ro = new ResizeObserver(() => requestAnimationFrame(handleResize));
    if (mainRef.current) ro.observe(mainRef.current);
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      ro.disconnect();
      [mainChart, rsiChart, macdChart, atrChart].forEach(c => { try { c.remove(); } catch (_) {} });
      charts.current = {};
      series.current = {};
    };
  }, []);

  // ─── Render data + indicators ─────────────────────────────────────────────
  const renderAll = useCallback((candles, sym) => {
    if (!candles || candles.length < 2 || !series.current.candle) return;
    const s = series.current;
    const cur = activeRef.current; // always fresh, no stale closure

    // 1. Candlestick
    const isSymbolChange = activeSymbolRef.current !== sym;
    if (isSymbolChange) {
      s.candle.setData(candles);
      activeSymbolRef.current = sym;
      charts.current.main?.timeScale().fitContent();
    } else {
      s.candle.update(candles[candles.length - 1]);
    }

    // 2. Overlay indicators (always setData for correctness at this tick frequency)
    const ema9d   = calcEMA(candles, 9);
    const ema21d  = calcEMA(candles, 21);
    const ema50d  = calcEMA(candles, 50);
    const sma200d = calcSMA(candles, 200);
    const bbData  = calcBollingerBands(candles, 20, 2);
    const vwapD   = calcVWAP(candles);

    if (ema9d.length)    s.ema9.setData(ema9d);
    if (ema21d.length)   s.ema21.setData(ema21d);
    if (ema50d.length)   s.ema50.setData(ema50d);
    if (sma200d.length)  s.sma200.setData(sma200d);
    if (bbData.upper.length) {
      s.bbUpper.setData(bbData.upper);
      s.bbMid.setData(bbData.middle);
      s.bbLower.setData(bbData.lower);
    }
    if (vwapD.length) s.vwap.setData(vwapD);

    // Visibility
    const vis = (key, refs) => refs.forEach(r => r?.applyOptions({ visible: cur[key] }));
    vis('ema9',   [s.ema9]);
    vis('ema21',  [s.ema21]);
    vis('ema50',  [s.ema50]);
    vis('sma200', [s.sma200]);
    vis('bb',     [s.bbUpper, s.bbMid, s.bbLower]);
    vis('vwap',   [s.vwap]);

    // 3. RSI
    const rsiData = calcRSI(candles, 14);
    if (rsiData.length > 0) {
      s.rsi.setData(rsiData);
      const refPoints = (v) => rsiData.map(p => ({ time: p.time, value: v }));
      s.rsi70.setData(refPoints(70));
      s.rsi50.setData(refPoints(50));
      s.rsi30.setData(refPoints(30));
    }
    vis('rsi', [s.rsi, s.rsi70, s.rsi50, s.rsi30]);

    // 4. MACD
    const macdData = calcMACD(candles, 12, 26, 9);
    if (macdData.macdLine.length > 0) {
      s.macdLine.setData(macdData.macdLine);
      s.macdSig.setData(macdData.signalLine);
      s.macdHist.setData(macdData.histogram);
      s.macdZero.setData(macdData.macdLine.map(p => ({ time: p.time, value: 0 })));
    }
    vis('macd', [s.macdLine, s.macdSig, s.macdHist, s.macdZero]);

    // 5. ATR
    const atrData = calcATR(candles, 14);
    if (atrData.length > 0) s.atr.setData(atrData);
    vis('atr', [s.atr]);

    // 6. Signal engine
    setSignal(generateSignal(candles));

    // 7. Live value chips
    setIndicatorValues({
      ema9:    ema9d[ema9d.length - 1]?.value?.toFixed(2),
      ema21:   ema21d[ema21d.length - 1]?.value?.toFixed(2),
      ema50:   ema50d[ema50d.length - 1]?.value?.toFixed(2),
      sma200:  sma200d[sma200d.length - 1]?.value?.toFixed(2),
      bbUpper: bbData.upper[bbData.upper.length - 1]?.value?.toFixed(2),
      bbLower: bbData.lower[bbData.lower.length - 1]?.value?.toFixed(2),
      rsi:     rsiData[rsiData.length - 1]?.value?.toFixed(1),
      macd:    macdData.macdLine[macdData.macdLine.length - 1]?.value?.toFixed(4),
      macdSig: macdData.signalLine[macdData.signalLine.length - 1]?.value?.toFixed(4),
      vwap:    vwapD[vwapD.length - 1]?.value?.toFixed(2),
      atr:     atrData[atrData.length - 1]?.value?.toFixed(4),
    });
  }, []); // no deps — uses refs for fresh values

  useEffect(() => {
    const candles = candleData[activeSymbol];
    if (!candles || candles.length === 0) return;
    renderAll(candles, activeSymbol);
  }, [candleData, activeSymbol, active, renderAll]);

  // ─── Live Positions, SL/TP & Execution Markers Overlays ──────────────────
  useEffect(() => {
    const candleSeries = series.current.candle;
    if (!candleSeries) return;

    // 1. Remove old price lines if they exist
    if (entryLineRef.current) {
      try { candleSeries.removePriceLine(entryLineRef.current); } catch (_) {}
      entryLineRef.current = null;
    }
    if (slLineRef.current) {
      try { candleSeries.removePriceLine(slLineRef.current); } catch (_) {}
      slLineRef.current = null;
    }
    if (tpLineRef.current) {
      try { candleSeries.removePriceLine(tpLineRef.current); } catch (_) {}
      tpLineRef.current = null;
    }

    // 2. Add current active position levels
    const pos = positions[activeSymbol];
    if (pos && pos.size !== 0) {
      const isLong = pos.size > 0;
      const entryPrice = pos.avg_price;

      // Position entry line
      entryLineRef.current = candleSeries.createPriceLine({
        price: entryPrice,
        color: '#3b82f6', // Blue
        lineWidth: 2,
        lineStyle: 1, // Solid
        axisLabelVisible: true,
        title: `ENTRY: ${isLong ? 'LONG' : 'SHORT'} (${Math.abs(pos.size).toFixed(3)})`,
      });

      // Stop Loss level line
      if (botConfig?.stopLossPercent > 0) {
        const slPrice = isLong 
          ? entryPrice * (1 - botConfig.stopLossPercent / 100)
          : entryPrice * (1 + botConfig.stopLossPercent / 100);
        
        slLineRef.current = candleSeries.createPriceLine({
          price: slPrice,
          color: '#ef4444', // Red
          lineWidth: 1.5,
          lineStyle: 2, // Dashed
          axisLabelVisible: true,
          title: `SL (${botConfig.stopLossPercent}%)`,
        });
      }

      // Take Profit level line
      if (botConfig?.takeProfitPercent > 0) {
        const tpPrice = isLong 
          ? entryPrice * (1 + botConfig.takeProfitPercent / 100)
          : entryPrice * (1 - botConfig.takeProfitPercent / 100);
        
        tpLineRef.current = candleSeries.createPriceLine({
          price: tpPrice,
          color: '#10b981', // Green
          lineWidth: 1.5,
          lineStyle: 2, // Dashed
          axisLabelVisible: true,
          title: `TP (${botConfig.takeProfitPercent}%)`,
        });
      }
    }

    // 3. Render historical trade execution markers
    const symbolTrades = tradeHistory.filter(
      t => t.symbol === activeSymbol && t.status === 'FILLED'
    );

    const markers = symbolTrades.map(t => {
      const isBuy = t.side === 'BUY';
      const qty = t.filled_quantity ?? t.quantity;
      const price = t.average_fill_price || t.price;
      const timestamp = new Date(t.timestamp).getTime() / 1000;

      return {
        time: timestamp,
        position: isBuy ? 'belowBar' : 'aboveBar',
        color: isBuy ? '#10b981' : '#ef4444',
        shape: isBuy ? 'arrowUp' : 'arrowDown',
        text: `${t.side} ${qty} @ $${price.toLocaleString()}`,
        size: 1.2,
      };
    });

    // Sort markers chronologically (required by Lightweight Charts)
    markers.sort((a, b) => a.time - b.time);
    
    try {
      candleSeries.setMarkers(markers);
    } catch (err) {
      console.warn("Failed to set chart markers:", err);
    }

  }, [activeSymbol, positions, tradeHistory, botConfig, candleData]);

  // ─── Render ───────────────────────────────────────────────────────────────
  const ticker    = tickerData[activeSymbol];
  const sigStyle  = SIGNAL_STYLES[signal.signal] || SIGNAL_STYLES.NEUTRAL;
  const isStrong  = signal.signal.startsWith('STRONG');
  const showRsi   = active.rsi;
  const showMacd  = active.macd;
  const showAtr   = active.atr;
  const subPaneH  = 130;

  return (
    <div className="widget-card" style={{ borderBottom: '1px solid var(--border-color)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

      {/* ── Header Row 1: Symbol + Price + Signal ─── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 16px', borderBottom: '1px solid var(--border-color)', background: '#080d14', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <AreaChart size={16} className="logo-icon" />
          <span style={{ fontWeight: '700', color: '#fff', fontSize: '0.9rem', letterSpacing: '0.5px' }}>
            {activeSymbol}
          </span>
          {ticker && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '0.82rem' }}>
              <span className="num-mono" style={{ fontSize: '0.95rem', fontWeight: '700', color: ticker.change_24h >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                {ticker.price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
              </span>
              <span className={`num-mono ${ticker.change_24h >= 0 ? 'text-up' : 'text-down'}`}>
                {ticker.change_24h >= 0 ? '+' : ''}{ticker.change_24h}%
              </span>
              <span style={{ color: 'var(--text-muted)', fontSize: '0.73rem' }}>
                H:<span className="num-mono"> {ticker.high_24h?.toFixed(2)}</span>
              </span>
              <span style={{ color: 'var(--text-muted)', fontSize: '0.73rem' }}>
                L:<span className="num-mono"> {ticker.low_24h?.toFixed(2)}</span>
              </span>
            </div>
          )}
        </div>

        {/* Signal badge */}
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => setShowReasons(p => !p)}
            style={{
              display: 'flex', alignItems: 'center', gap: '7px',
              padding: '5px 12px', borderRadius: '20px', cursor: 'pointer',
              background: sigStyle.bg, border: `1px solid ${sigStyle.border}`,
              color: sigStyle.color, fontWeight: '700', fontSize: '0.78rem',
              letterSpacing: '0.5px',
            }}
          >
            <span style={{
              width: '7px', height: '7px', borderRadius: '50%',
              background: sigStyle.dot, flexShrink: 0,
              boxShadow: isStrong ? `0 0 8px ${sigStyle.dot}` : 'none',
              animation: isStrong ? 'pulse-glow 1.5s ease-in-out infinite' : 'none',
            }} />
            {signal.signal}
            <span style={{ fontSize: '0.7rem', opacity: 0.7 }}>
              ({signal.score > 0 ? '+' : ''}{signal.score})
            </span>
          </button>

          {/* Reasons popover */}
          {showReasons && signal.reasons.length > 0 && (
            <div style={{
              position: 'absolute', right: 0, top: 'calc(100% + 6px)', zIndex: 200,
              background: '#101827', border: `1px solid ${sigStyle.border}`,
              borderRadius: '8px', padding: '12px 16px', minWidth: '240px',
              boxShadow: '0 12px 40px rgba(0,0,0,0.6)', fontSize: '0.78rem',
            }}>
              <div style={{ color: 'var(--text-muted)', marginBottom: '8px', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.6px', fontSize: '0.7rem' }}>
                Signal Analysis
              </div>
              {signal.reasons.map((r, i) => (
                <div key={i} style={{ color: sigStyle.color, marginBottom: '5px', display: 'flex', gap: '8px' }}>
                  <span style={{ opacity: 0.6 }}>•</span><span>{r}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── Header Row 2: Indicator Toggles ─── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '4px', padding: '7px 14px', flexWrap: 'wrap', background: 'rgba(8,13,20,0.95)', borderBottom: '1px solid var(--border-color)', flexShrink: 0 }}>
        {INDICATORS.map(ind => (
          <button
            key={ind.key}
            onClick={() => toggle(ind.key)}
            style={{
              display: 'flex', alignItems: 'center', gap: '5px',
              padding: '3px 10px', borderRadius: '4px', cursor: 'pointer',
              fontSize: '0.72rem', fontWeight: '600', whiteSpace: 'nowrap',
              background: active[ind.key] ? `${ind.color}20` : 'rgba(255,255,255,0.03)',
              border: `1px solid ${active[ind.key] ? ind.color : 'rgba(255,255,255,0.1)'}`,
              color: active[ind.key] ? ind.color : 'var(--text-muted)',
              transition: 'all 0.15s',
              fontFamily: 'var(--font-sans)',
            }}
          >
            <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: active[ind.key] ? ind.color : 'var(--text-muted)', flexShrink: 0 }} />
            {ind.label}
            {indicatorValues[ind.key] && active[ind.key] && (
              <span style={{ opacity: 0.75, fontFamily: 'var(--font-mono)' }}>{indicatorValues[ind.key]}</span>
            )}
          </button>
        ))}

        {/* Live value quick-read chips */}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: '10px', fontSize: '0.72rem', fontFamily: 'var(--font-sans)' }}>
          {active.rsi && indicatorValues.rsi && (
            <span style={{ color: parseFloat(indicatorValues.rsi) > 70 ? 'var(--color-down)' : parseFloat(indicatorValues.rsi) < 30 ? 'var(--color-up)' : '#fbbf24' }}>
              RSI <span className="num-mono">{indicatorValues.rsi}</span>
            </span>
          )}
          {active.macd && indicatorValues.macd && (
            <span style={{ color: '#34d399' }}>
              MACD <span className="num-mono">{indicatorValues.macd}</span>
            </span>
          )}
          {active.vwap && indicatorValues.vwap && (
            <span style={{ color: '#ec4899' }}>
              VWAP <span className="num-mono">{indicatorValues.vwap}</span>
            </span>
          )}
          {active.atr && indicatorValues.atr && (
            <span style={{ color: '#94a3b8' }}>
              ATR <span className="num-mono">{indicatorValues.atr}</span>
            </span>
          )}
        </div>
      </div>

      {/* ── Chart Panes ─── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>

        {/* Main candlestick pane — takes all remaining space */}
        <div ref={mainRef} style={{ flex: 1, minHeight: 0 }} />

        {/* RSI sub-pane */}
        {showRsi && (
          <div style={{ flexShrink: 0, position: 'relative', borderTop: '1px solid var(--border-color)' }}>
            <span className="chart-pane-label" style={{ color: '#fbbf24' }}>RSI(14)</span>
            <div ref={rsiRef} style={{ height: `${subPaneH}px` }} />
          </div>
        )}

        {/* MACD sub-pane */}
        {showMacd && (
          <div style={{ flexShrink: 0, position: 'relative', borderTop: '1px solid var(--border-color)' }}>
            <span className="chart-pane-label" style={{ color: '#34d399' }}>MACD(12,26,9)</span>
            <div ref={macdRef} style={{ height: `${subPaneH}px` }} />
          </div>
        )}

        {/* ATR sub-pane */}
        {showAtr && (
          <div style={{ flexShrink: 0, position: 'relative', borderTop: '1px solid var(--border-color)' }}>
            <span className="chart-pane-label" style={{ color: '#94a3b8' }}>ATR(14)</span>
            <div ref={atrRef} style={{ height: `${subPaneH}px` }} />
          </div>
        )}

        {/* Hidden DOM nodes keep refs alive when panes are toggled off */}
        {!showRsi  && <div ref={rsiRef}  style={{ height: 0, overflow: 'hidden' }} />}
        {!showMacd && <div ref={macdRef} style={{ height: 0, overflow: 'hidden' }} />}
        {!showAtr  && <div ref={atrRef}  style={{ height: 0, overflow: 'hidden' }} />}
      </div>
    </div>
  );
}
