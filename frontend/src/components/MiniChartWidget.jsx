/**
 * MiniChartWidget.jsx
 * A compact, self-contained candlestick chart panel used inside the multi-chart grid.
 * Each panel independently tracks its own symbol, renders live candles + EMA overlays,
 * and can be "focused" to set the global active symbol.
 */
import React, { useEffect, useRef, useState, useCallback } from 'react';
import { createChart, CandlestickSeries, LineSeries } from 'lightweight-charts';
import { useStore } from '../store/useStore';
import { calcEMA } from '../utils/indicators';
import { ChevronDown, Maximize2 } from 'lucide-react';

const SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'AAPL', 'TSLA', 'MSFT'];

const SYMBOL_COLORS = {
  BTCUSDT: '#f59e0b',
  ETHUSDT: '#8b5cf6',
  AAPL:    '#34d399',
  TSLA:    '#f87171',
  MSFT:    '#06b6d4',
};

export default function MiniChartWidget({ defaultSymbol = 'BTCUSDT', isFocused = false, onFocus }) {
  const containerRef  = useRef(null);
  const chartRef      = useRef(null);
  const candleRef     = useRef(null);
  const ema9Ref       = useRef(null);
  const ema21Ref      = useRef(null);
  const prevSymbolRef = useRef('');

  const [symbol, setSymbol]         = useState(defaultSymbol);
  const [dropdownOpen, setDropdown] = useState(false);

  const { candleData, tickerData, priceDirections, setActiveSymbol } = useStore();

  // ── Chart initialisation ────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      width:  containerRef.current.clientWidth,
      height: containerRef.current.clientHeight || 200,
      layout: {
        background: { type: 'solid', color: '#0b0f19' },
        textColor: '#9ca3af',
        fontFamily: 'Inter, sans-serif',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.02)' },
        horzLines: { color: 'rgba(255,255,255,0.02)' },
      },
      rightPriceScale: {
        borderColor: 'rgba(255,255,255,0.06)',
        visible: true,
        scaleMargins: { top: 0.08, bottom: 0.08 },
      },
      timeScale: {
        borderColor: 'rgba(255,255,255,0.06)',
        timeVisible: true,
        secondsVisible: false,
        visible: true,
      },
      crosshair: {
        vertLine: { color: 'rgba(100,140,255,0.4)', width: 1, style: 3, labelBackgroundColor: '#2563eb' },
        horzLine: { color: 'rgba(100,140,255,0.4)', width: 1, style: 3, labelBackgroundColor: '#2563eb' },
      },
      handleScroll: true,
      handleScale: true,
    });

    const candle = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981', downColor: '#ef4444',
      borderUpColor: '#10b981', borderDownColor: '#ef4444',
      wickUpColor: '#10b981', wickDownColor: '#ef4444',
    });

    const ema9  = chart.addSeries(LineSeries, { color: '#f59e0b', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const ema21 = chart.addSeries(LineSeries, { color: '#8b5cf6', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });

    chartRef.current  = chart;
    candleRef.current = candle;
    ema9Ref.current   = ema9;
    ema21Ref.current  = ema21;

    // Responsive resize
    const ro = new ResizeObserver(() => requestAnimationFrame(() => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.resize(
          containerRef.current.clientWidth,
          containerRef.current.clientHeight
        );
      }
    }));
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      try { chart.remove(); } catch (_) {}
      chartRef.current  = null;
      candleRef.current = null;
      ema9Ref.current   = null;
      ema21Ref.current  = null;
    };
  }, []);

  // ── Data rendering ──────────────────────────────────────────────────────
  const renderData = useCallback((sym) => {
    const candles = candleData[sym];
    if (!candles || candles.length === 0 || !candleRef.current) return;

    // Full reload on symbol change
    if (prevSymbolRef.current !== sym) {
      candleRef.current.setData(candles);
      const e9  = calcEMA(candles, 9);
      const e21 = calcEMA(candles, 21);
      if (e9.length)  ema9Ref.current?.setData(e9);
      if (e21.length) ema21Ref.current?.setData(e21);
      chartRef.current?.timeScale().fitContent();
      prevSymbolRef.current = sym;
    } else {
      // Incremental tick update
      const last = candles[candles.length - 1];
      try { candleRef.current.update(last); } catch (_) {}
      const e9  = calcEMA(candles, 9);
      const e21 = calcEMA(candles, 21);
      if (e9.length)  try { ema9Ref.current?.update(e9[e9.length - 1]);   } catch (_) {}
      if (e21.length) try { ema21Ref.current?.update(e21[e21.length - 1]); } catch (_) {}
    }
  }, [candleData]);

  useEffect(() => {
    renderData(symbol);
  }, [candleData, symbol, renderData]);

  // ── Reset chart on symbol change ────────────────────────────────────────
  useEffect(() => {
    prevSymbolRef.current = ''; // force full reload
  }, [symbol]);

  // ── Ticker data ─────────────────────────────────────────────────────────
  const ticker    = tickerData[symbol];
  const direction = priceDirections[symbol];
  const accentCol = SYMBOL_COLORS[symbol] || '#6366f1';

  const handleFocusClick = () => {
    setActiveSymbol(symbol);
    if (onFocus) onFocus(symbol);
  };

  return (
    <div
      style={{
        display: 'flex', flexDirection: 'column',
        background: '#0b0f19',
        border: isFocused
          ? `1.5px solid ${accentCol}`
          : '1px solid rgba(255,255,255,0.06)',
        borderRadius: '6px',
        overflow: 'hidden',
        transition: 'border-color 0.2s',
        boxShadow: isFocused ? `0 0 12px ${accentCol}30` : 'none',
        cursor: 'pointer',
        position: 'relative',
      }}
      onClick={handleFocusClick}
    >
      {/* ── Mini chart header ── */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '6px 10px', background: '#080d14', flexShrink: 0,
        borderBottom: '1px solid rgba(255,255,255,0.05)',
      }}
        onClick={e => e.stopPropagation()}
      >
        {/* Symbol selector */}
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => setDropdown(p => !p)}
            style={{
              display: 'flex', alignItems: 'center', gap: '5px',
              background: 'transparent', border: 'none', cursor: 'pointer',
              color: accentCol, fontWeight: '700', fontSize: '0.8rem',
              fontFamily: 'var(--font-sans)',
            }}
          >
            <span style={{
              width: '8px', height: '8px', borderRadius: '50%',
              background: accentCol, boxShadow: `0 0 6px ${accentCol}`,
            }} />
            {symbol}
            <ChevronDown size={12} style={{ opacity: 0.7 }} />
          </button>

          {/* Dropdown */}
          {dropdownOpen && (
            <div style={{
              position: 'absolute', top: 'calc(100% + 4px)', left: 0, zIndex: 200,
              background: '#101827', border: '1px solid rgba(255,255,255,0.12)',
              borderRadius: '6px', minWidth: '130px',
              boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
            }}>
              {SYMBOLS.map(s => (
                <button
                  key={s}
                  onClick={() => { setSymbol(s); setDropdown(false); }}
                  style={{
                    display: 'flex', alignItems: 'center', gap: '8px',
                    width: '100%', padding: '7px 12px', background: 'transparent',
                    border: 'none', cursor: 'pointer', textAlign: 'left',
                    color: s === symbol ? SYMBOL_COLORS[s] : 'var(--text-secondary)',
                    fontSize: '0.8rem', fontWeight: s === symbol ? '700' : '400',
                    fontFamily: 'var(--font-sans)',
                    borderBottom: '1px solid rgba(255,255,255,0.04)',
                  }}
                >
                  <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: SYMBOL_COLORS[s], flexShrink: 0 }} />
                  {s}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Price + change */}
        {ticker ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '0.75rem' }}>
            <span
              className="num-mono"
              style={{
                fontWeight: '700', fontSize: '0.82rem',
                color: direction === 'up' ? 'var(--color-up)' : direction === 'down' ? 'var(--color-down)' : '#fff',
                transition: 'color 0.3s',
              }}
            >
              {ticker.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
            <span
              className="num-mono"
              style={{ color: ticker.change_24h >= 0 ? 'var(--color-up)' : 'var(--color-down)', fontSize: '0.72rem' }}
            >
              {ticker.change_24h >= 0 ? '+' : ''}{ticker.change_24h}%
            </span>
          </div>
        ) : (
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>Loading…</span>
        )}

        {/* Expand / focus icon */}
        <button
          onClick={handleFocusClick}
          title="Set as active symbol"
          style={{
            background: 'transparent', border: 'none', cursor: 'pointer',
            color: 'var(--text-muted)', padding: '2px',
            display: 'flex', alignItems: 'center',
          }}
        >
          <Maximize2 size={12} />
        </button>
      </div>

      {/* ── Chart area ── */}
      <div ref={containerRef} style={{ flex: 1, minHeight: 0 }} />
    </div>
  );
}
