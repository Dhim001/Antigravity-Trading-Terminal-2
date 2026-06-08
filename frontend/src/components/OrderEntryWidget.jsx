/**
 * OrderEntryWidget.jsx
 * Enhanced order ticket with:
 *  - SL/TP inline inputs (absolute price or % offset)
 *  - Quick quantity % buttons (25 / 50 / 75 / 100%)
 *  - Risk/Reward ratio display
 *  - Keyboard shortcuts: B = focus BUY, S = focus SELL, Enter = submit
 *  - Toast-style animated result feedback
 */
import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { useStore } from '../store/useStore';
import { sendWebSocketAction } from '../services/websocket';
import { PlusCircle, ShieldAlert, CheckCircle, Target, TrendingDown, TrendingUp } from 'lucide-react';

const fmtDec = (n, dec) => n?.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });

export default function OrderEntryWidget() {
  const { activeSymbol, tickerData, balances, positions, orderResult } = useStore();

  const [side,      setSide]      = useState('BUY');
  const [orderType, setOrderType] = useState('LIMIT');
  const [price,     setPrice]     = useState('');
  const [quantity,  setQuantity]  = useState('');
  const [slPrice,   setSlPrice]   = useState('');
  const [tpPrice,   setTpPrice]   = useState('');
  const [slMode,    setSlMode]     = useState('%');  // '%' or '$'
  const [tpMode,    setTpMode]     = useState('%');  // '%' or '$'
  const [errorMsg,  setErrorMsg]  = useState(null);
  const [showSLTP,  setShowSLTP]  = useState(false);
  const buyBtnRef  = useRef(null);
  const sellBtnRef = useRef(null);
  const formRef    = useRef(null);

  const ticker = tickerData[activeSymbol];

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e) => {
      const tag = document.activeElement?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.key === 'b' || e.key === 'B') { setSide('BUY');  buyBtnRef.current?.focus(); }
      if (e.key === 's' || e.key === 'S') { setSide('SELL'); sellBtnRef.current?.focus(); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  // Sync price on symbol change
  useEffect(() => {
    if (ticker) setPrice(ticker.price.toString());
    else setPrice('');
    setSlPrice(''); setTpPrice(''); setQuantity('');
    setErrorMsg(null);
  }, [activeSymbol, ticker === undefined]);

  const isCrypto = activeSymbol.includes('USDT');
  const base  = isCrypto ? activeSymbol.replace('USDT', '') : activeSymbol;
  const quote = isCrypto ? 'USDT' : 'USD';

  const quoteBalance   = balances[quote]?.balance  ?? 0;
  const quoteLocked    = balances[quote]?.locked    ?? 0;
  const quoteAvailable = quoteBalance - quoteLocked;
  const basePosition   = positions[activeSymbol]?.size ?? 0;

  const orderPrice = orderType === 'LIMIT' ? parseFloat(price) || 0 : (ticker?.price ?? 0);
  const qty        = parseFloat(quantity) || 0;
  const estCost    = orderPrice * qty;

  // Compute absolute SL/TP prices from mode + value
  const computeSlAbs = () => {
    if (!slPrice) return null;
    if (slMode === '$') return parseFloat(slPrice);
    const pct = parseFloat(slPrice);
    if (!pct) return null;
    return side === 'BUY' ? orderPrice * (1 - pct / 100) : orderPrice * (1 + pct / 100);
  };
  const computeTpAbs = () => {
    if (!tpPrice) return null;
    if (tpMode === '$') return parseFloat(tpPrice);
    const pct = parseFloat(tpPrice);
    if (!pct) return null;
    return side === 'BUY' ? orderPrice * (1 + pct / 100) : orderPrice * (1 - pct / 100);
  };

  const slAbs = computeSlAbs();
  const tpAbs = computeTpAbs();

  // Risk/Reward ratio
  const rrRatio = useMemo(() => {
    if (!slAbs || !tpAbs || !orderPrice) return null;
    const risk   = Math.abs(orderPrice - slAbs);
    const reward = Math.abs(tpAbs - orderPrice);
    if (risk === 0) return null;
    return (reward / risk).toFixed(2);
  }, [slAbs, tpAbs, orderPrice]);

  // Quick quantity fill
  const fillQty = (pct) => {
    if (!orderPrice) return;
    if (side === 'BUY') {
      const budget = quoteAvailable * (pct / 100);
      setQuantity((budget / orderPrice).toFixed(6));
    } else {
      setQuantity((Math.abs(basePosition) * (pct / 100)).toFixed(6));
    }
  };

  const handlePlaceOrder = (e) => {
    e.preventDefault();
    setErrorMsg(null);
    const q = parseFloat(quantity);
    if (isNaN(q) || q <= 0) { setErrorMsg('Quantity must be > 0'); return; }
    let lp = null;
    if (orderType === 'LIMIT') {
      lp = parseFloat(price);
      if (isNaN(lp) || lp <= 0) { setErrorMsg('Price must be > 0'); return; }
    }
    const val = (lp || ticker?.price || 0) * q;
    if (val > 50000) { setErrorMsg('Order value exceeds $50,000 risk limit'); return; }
    if (side === 'BUY' && val > quoteAvailable) { setErrorMsg(`Insufficient funds. Available: ${quoteAvailable.toFixed(2)} ${quote}`); return; }
    if (side === 'SELL' && q > basePosition) { setErrorMsg(`Insufficient holdings. Owned: ${basePosition} ${base}`); return; }

    const payload = { symbol: activeSymbol, type: orderType, side, price: lp, quantity: q };
    if (showSLTP) {
      if (slAbs) payload.stop_loss_price = parseFloat(slAbs.toFixed(8));
      if (tpAbs) payload.take_profit_price = parseFloat(tpAbs.toFixed(8));
    }
    const ok = sendWebSocketAction('place_order', payload);
    if (ok) { setQuantity(''); setSlPrice(''); setTpPrice(''); }
    else setErrorMsg('Order dispatch failed — WebSocket disconnected.');
  };

  const priceDec  = ticker ? ((activeSymbol.includes('XRP') || activeSymbol.includes('ADA') || activeSymbol.includes('DOGE') || ticker.price < 2) ? 4 : 2) : 2;
  const isBuy = side === 'BUY';

  return (
    <div className="widget-card" style={{ borderBottom: '1px solid var(--border-color)' }}>
      {/* Header */}
      <div className="widget-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <PlusCircle size={13} className="logo-icon" />
          <span className="widget-title">Order Entry</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 'var(--fs-2xs)', fontWeight: 700, color: isBuy ? 'var(--color-up)' : 'var(--color-down)', letterSpacing: '0.5px' }}>
            {activeSymbol}
          </span>
          {ticker && (
            <span className="num-mono" style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, color: ticker.change_24h >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
              {fmtDec(ticker.price, priceDec)}
            </span>
          )}
        </div>
      </div>

      <div className="widget-content" style={{ padding: 'var(--sp-3) var(--sp-3) var(--sp-2)' }}>
        {/* BUY / SELL tabs */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 10 }}>
          <button ref={buyBtnRef} onClick={() => setSide('BUY')} className="terminal-btn" style={{
            background: isBuy ? 'rgba(16,185,129,0.18)' : 'rgba(255,255,255,0.02)',
            border: `1px solid ${isBuy ? 'rgba(16,185,129,0.5)' : 'var(--border-color)'}`,
            color: isBuy ? 'var(--color-up)' : 'var(--text-muted)',
            boxShadow: isBuy ? '0 0 12px rgba(16,185,129,0.12)' : 'none',
            padding: '8px', fontSize: 'var(--fs-sm)', fontWeight: 800, letterSpacing: '1px',
          }}>
            <TrendingUp size={13} /> BUY <span style={{ fontSize: 'var(--fs-2xs)', opacity: 0.6, fontWeight: 500 }}>[B]</span>
          </button>
          <button ref={sellBtnRef} onClick={() => setSide('SELL')} className="terminal-btn" style={{
            background: !isBuy ? 'rgba(239,68,68,0.18)' : 'rgba(255,255,255,0.02)',
            border: `1px solid ${!isBuy ? 'rgba(239,68,68,0.5)' : 'var(--border-color)'}`,
            color: !isBuy ? 'var(--color-down)' : 'var(--text-muted)',
            boxShadow: !isBuy ? '0 0 12px rgba(239,68,68,0.12)' : 'none',
            padding: '8px', fontSize: 'var(--fs-sm)', fontWeight: 800, letterSpacing: '1px',
          }}>
            <TrendingDown size={13} /> SELL <span style={{ fontSize: 'var(--fs-2xs)', opacity: 0.6, fontWeight: 500 }}>[S]</span>
          </button>
        </div>

        {/* LIMIT / MARKET */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4, marginBottom: 10, background: 'rgba(255,255,255,0.03)', borderRadius: 'var(--r-md)', padding: 2 }}>
          {['LIMIT', 'MARKET'].map(t => (
            <button key={t} onClick={() => setOrderType(t)} style={{
              padding: '5px', background: orderType === t ? 'rgba(255,255,255,0.08)' : 'transparent',
              border: 'none', borderRadius: 'var(--r-sm)', color: orderType === t ? '#fff' : 'var(--text-muted)',
              fontSize: 'var(--fs-xs)', fontWeight: 700, cursor: 'pointer', fontFamily: 'var(--font-sans)', letterSpacing: '0.3px',
            }}>{t}</button>
          ))}
        </div>

        <form ref={formRef} onSubmit={handlePlaceOrder}>
          {/* Limit Price */}
          {orderType === 'LIMIT' && (
            <div className="terminal-input-group">
              <label className="terminal-label">Limit Price</label>
              <div className="terminal-input-wrapper">
                <input type="number" step="any" value={price} onChange={e => setPrice(e.target.value)} className="terminal-input" style={{ paddingRight: 46, fontSize: 'var(--fs-sm)' }} required />
                <span className="terminal-input-suffix" style={{ fontSize: 'var(--fs-2xs)' }}>{quote}</span>
              </div>
            </div>
          )}

          {/* Quantity */}
          <div className="terminal-input-group">
            <label className="terminal-label">Quantity</label>
            <div className="terminal-input-wrapper">
              <input type="number" step="any" value={quantity} onChange={e => setQuantity(e.target.value)} placeholder="0.00" className="terminal-input" style={{ paddingRight: 46, fontSize: 'var(--fs-sm)' }} required />
              <span className="terminal-input-suffix" style={{ fontSize: 'var(--fs-2xs)' }}>{base}</span>
            </div>
          </div>

          {/* Quick qty buttons */}
          <div className="qty-quick-btns" style={{ marginBottom: 10 }}>
            {[25, 50, 75, 100].map(pct => (
              <button key={pct} type="button" className="qty-quick-btn" onClick={() => fillQty(pct)}>{pct}%</button>
            ))}
          </div>

          {/* Balance context */}
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--fs-xs)', color: 'var(--text-secondary)', marginBottom: 6 }}>
            <span>Available {isBuy ? quote : base}:</span>
            <span className="num-mono" style={{ fontWeight: 700, color: '#fff' }}>
              {isBuy ? `${quoteAvailable.toFixed(2)} ${quote}` : `${Math.abs(basePosition).toFixed(4)} ${base}`}
            </span>
          </div>

          {/* Order value */}
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--fs-xs)', color: 'var(--text-secondary)', marginBottom: 10 }}>
            <span>Est. Order Value:</span>
            <span className="num-mono" style={{ fontWeight: 700, color: estCost > quoteAvailable && isBuy ? 'var(--color-down)' : '#fff' }}>
              {estCost.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} {quote}
            </span>
          </div>

          {/* SL/TP toggle */}
          <button type="button" onClick={() => setShowSLTP(s => !s)} style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%',
            background: showSLTP ? 'rgba(37,99,235,0.08)' : 'rgba(255,255,255,0.02)',
            border: `1px solid ${showSLTP ? 'rgba(37,99,235,0.3)' : 'var(--border-color)'}`,
            borderRadius: 'var(--r-md)', padding: '6px 10px', cursor: 'pointer', marginBottom: 8,
            color: showSLTP ? '#93c5fd' : 'var(--text-muted)', fontSize: 'var(--fs-xs)', fontFamily: 'var(--font-sans)', fontWeight: 600,
          }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><Target size={11} /> Stop Loss / Take Profit</span>
            <span style={{ fontSize: 'var(--fs-2xs)', opacity: 0.7 }}>{showSLTP ? '▲ Hide' : '▼ Show'}</span>
          </button>

          {showSLTP && (
            <div style={{ background: 'rgba(255,255,255,0.015)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 'var(--r-md)', padding: '10px', marginBottom: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
              {/* Stop Loss */}
              <div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                  <label className="terminal-label" style={{ margin: 0, color: 'var(--color-down)' }}>Stop Loss</label>
                  <div style={{ display: 'flex', gap: 2 }}>
                    {['%', '$'].map(m => (
                      <button key={m} type="button" onClick={() => { setSlMode(m); setSlPrice(''); }} style={{
                        padding: '1px 7px', borderRadius: 'var(--r-sm)', border: '1px solid', cursor: 'pointer',
                        fontSize: 'var(--fs-2xs)', fontWeight: 700, fontFamily: 'var(--font-sans)',
                        background: slMode === m ? 'rgba(239,68,68,0.15)' : 'transparent',
                        borderColor: slMode === m ? 'rgba(239,68,68,0.4)' : 'rgba(255,255,255,0.1)',
                        color: slMode === m ? '#f87171' : 'var(--text-muted)',
                      }}>{m}</button>
                    ))}
                  </div>
                </div>
                <div className="terminal-input-wrapper">
                  <input type="number" step="any" value={slPrice} onChange={e => setSlPrice(e.target.value)} placeholder={slMode === '%' ? '1.5' : orderPrice ? orderPrice.toFixed(priceDec) : '0'} className="terminal-input" style={{ paddingRight: 40, fontSize: 'var(--fs-xs)', borderColor: 'rgba(239,68,68,0.25)' }} />
                  <span className="terminal-input-suffix" style={{ fontSize: 'var(--fs-2xs)', color: '#f87171' }}>{slMode === '%' ? '%' : quote}</span>
                </div>
                {slAbs && <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--color-down)', marginTop: 3, fontFamily: 'var(--font-mono)' }}>→ ${slAbs.toFixed(priceDec)}</div>}
              </div>

              {/* Take Profit */}
              <div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                  <label className="terminal-label" style={{ margin: 0, color: 'var(--color-up)' }}>Take Profit</label>
                  <div style={{ display: 'flex', gap: 2 }}>
                    {['%', '$'].map(m => (
                      <button key={m} type="button" onClick={() => { setTpMode(m); setTpPrice(''); }} style={{
                        padding: '1px 7px', borderRadius: 'var(--r-sm)', border: '1px solid', cursor: 'pointer',
                        fontSize: 'var(--fs-2xs)', fontWeight: 700, fontFamily: 'var(--font-sans)',
                        background: tpMode === m ? 'rgba(16,185,129,0.15)' : 'transparent',
                        borderColor: tpMode === m ? 'rgba(16,185,129,0.4)' : 'rgba(255,255,255,0.1)',
                        color: tpMode === m ? '#6ee7b7' : 'var(--text-muted)',
                      }}>{m}</button>
                    ))}
                  </div>
                </div>
                <div className="terminal-input-wrapper">
                  <input type="number" step="any" value={tpPrice} onChange={e => setTpPrice(e.target.value)} placeholder={tpMode === '%' ? '3.0' : orderPrice ? orderPrice.toFixed(priceDec) : '0'} className="terminal-input" style={{ paddingRight: 40, fontSize: 'var(--fs-xs)', borderColor: 'rgba(16,185,129,0.25)' }} />
                  <span className="terminal-input-suffix" style={{ fontSize: 'var(--fs-2xs)', color: '#6ee7b7' }}>{tpMode === '%' ? '%' : quote}</span>
                </div>
                {tpAbs && <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--color-up)', marginTop: 3, fontFamily: 'var(--font-mono)' }}>→ ${tpAbs.toFixed(priceDec)}</div>}
              </div>

              {/* R:R display */}
              {rrRatio && (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '6px', borderRadius: 'var(--r-sm)', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.07)' }}>
                  <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-secondary)' }}>Risk / Reward:&nbsp;</span>
                  <span className="num-mono" style={{ fontWeight: 800, color: parseFloat(rrRatio) >= 2 ? 'var(--color-up)' : parseFloat(rrRatio) >= 1 ? '#fbbf24' : 'var(--color-down)' }}>
                    1 : {rrRatio}
                  </span>
                </div>
              )}
            </div>
          )}

          {/* Submit button */}
          <button type="submit" className={`terminal-btn ${isBuy ? 'btn-buy' : 'btn-sell'}`} style={{ fontWeight: 800, letterSpacing: '0.8px', boxShadow: isBuy ? '0 0 16px rgba(16,185,129,0.12)' : '0 0 16px rgba(239,68,68,0.12)' }}>
            {isBuy ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
            Place {side} {orderType}
          </button>
        </form>

        {/* Error */}
        {errorMsg && (
          <div style={{ display: 'flex', gap: 7, alignItems: 'flex-start', padding: '9px 10px', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 'var(--r-md)', marginTop: 8, fontSize: 'var(--fs-xs)' }}>
            <ShieldAlert size={13} style={{ color: 'var(--color-down)', flexShrink: 0, marginTop: 1 }} />
            <span style={{ color: '#f87171' }}>{errorMsg}</span>
          </div>
        )}

        {/* Order result */}
        {orderResult && (
          <div style={{
            display: 'flex', gap: 7, alignItems: 'flex-start', padding: '9px 10px',
            background: orderResult.status === 'success' ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)',
            border: `1px solid ${orderResult.status === 'success' ? 'rgba(16,185,129,0.3)' : 'rgba(239,68,68,0.3)'}`,
            borderRadius: 'var(--r-md)', marginTop: 8, fontSize: 'var(--fs-xs)',
            animation: 'slideUp 0.2s ease',
          }}>
            {orderResult.status === 'success'
              ? <CheckCircle size={13} style={{ color: 'var(--color-up)', flexShrink: 0, marginTop: 1 }} />
              : <ShieldAlert  size={13} style={{ color: 'var(--color-down)', flexShrink: 0, marginTop: 1 }} />
            }
            <span style={{ color: orderResult.status === 'success' ? '#6ee7b7' : '#f87171' }}>
              {orderResult.message}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
