import React, { useState, useEffect } from 'react';
import { useStore } from '../store/useStore';
import { sendWebSocketAction } from '../services/websocket';
import { PlusCircle, Info, ShieldAlert, CheckCircle } from 'lucide-react';

export default function OrderEntryWidget() {
  const { activeSymbol, tickerData, balances, positions, orderResult } = useStore();
  
  const [side, setSide] = useState('BUY'); // BUY or SELL
  const [orderType, setOrderType] = useState('LIMIT'); // LIMIT or MARKET
  const [price, setPrice] = useState('');
  const [quantity, setQuantity] = useState('');
  const [errorMsg, setErrorMsg] = useState(null);

  const ticker = tickerData[activeSymbol];
  
  // Set default price based on current ticker price when activeSymbol changes
  useEffect(() => {
    if (ticker) {
      setPrice(ticker.price.toString());
    } else {
      setPrice('');
    }
  }, [activeSymbol, ticker === undefined]);

  // Asset helper details
  const getAssetDetails = () => {
    const sym = activeSymbol || 'BTCUSDT';
    const isCrypto = sym.includes("USDT");
    const base = isCrypto ? sym.replace("USDT", "") : sym;
    const quote = isCrypto ? "USDT" : "USD";
    return { base, quote };
  };

  const { base, quote } = getAssetDetails();

  // Balances
  const quoteBalance = balances[quote]?.balance || 0;
  const quoteLocked = balances[quote]?.locked || 0;
  const quoteAvailable = quoteBalance - quoteLocked;

  const basePosition = positions[activeSymbol]?.size || 0;

  // Estimated Order Value
  const orderPrice = orderType === 'LIMIT' ? parseFloat(price) : (ticker?.price || 0);
  const qtyVal = parseFloat(quantity) || 0;
  const estCost = orderPrice * qtyVal;

  const handlePlaceOrder = (e) => {
    e.preventDefault();
    setErrorMsg(null);

    const qty = parseFloat(quantity);
    if (isNaN(qty) || qty <= 0) {
      setErrorMsg("Quantity must be greater than 0");
      return;
    }

    let limitPrice = null;
    if (orderType === 'LIMIT') {
      limitPrice = parseFloat(price);
      if (isNaN(limitPrice) || limitPrice <= 0) {
        setErrorMsg("Price must be greater than 0");
        return;
      }
    }

    // Client-side pre-trade risk check
    const value = (limitPrice || ticker?.price || 0) * qty;
    if (value > 50000.0) {
      setErrorMsg("Order value exceeds maximum risk limit of $50,000");
      return;
    }

    if (side === 'BUY') {
      if (value > quoteAvailable) {
        setErrorMsg(`Insufficient funds. Available: ${quoteAvailable.toFixed(2)} ${quote}`);
        return;
      }
    } else {
      if (qty > basePosition) {
        setErrorMsg(`Insufficient holdings. Owned: ${basePosition} ${base}`);
        return;
      }
    }

    // Submit order
    const success = sendWebSocketAction("place_order", {
      symbol: activeSymbol,
      type: orderType,
      side,
      price: limitPrice,
      quantity: qty
    });

    if (success) {
      setQuantity('');
    } else {
      setErrorMsg("Order dispatch failed. WebSocket is disconnected.");
    }
  };

  return (
    <div className="widget-card" style={{ borderLeft: '1px solid var(--border-color)', borderBottom: '1px solid var(--border-color)' }}>
      <div className="widget-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <PlusCircle size={15} className="logo-icon" />
          <span className="widget-title">Order Entry Ticket</span>
        </div>
      </div>
      
      <div className="widget-content" style={{ padding: '16px' }}>
        {/* BUY / SELL Tabs */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginBottom: '16px' }}>
          <button 
            onClick={() => setSide('BUY')}
            className="terminal-btn"
            style={{ 
              backgroundColor: side === 'BUY' ? 'var(--color-up-bg)' : 'rgba(255, 255, 255, 0.02)',
              border: side === 'BUY' ? '1px solid var(--color-up)' : '1px solid var(--border-color)',
              color: side === 'BUY' ? 'var(--color-up)' : 'var(--text-secondary)'
            }}
          >
            BUY
          </button>
          <button 
            onClick={() => setSide('SELL')}
            className="terminal-btn"
            style={{ 
              backgroundColor: side === 'SELL' ? 'var(--color-down-bg)' : 'rgba(255, 255, 255, 0.02)',
              border: side === 'SELL' ? '1px solid var(--color-down)' : '1px solid var(--border-color)',
              color: side === 'SELL' ? 'var(--color-down)' : 'var(--text-secondary)'
            }}
          >
            SELL
          </button>
        </div>

        {/* LIMIT / MARKET Selector */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginBottom: '16px', background: 'var(--bg-tertiary)', borderRadius: '6px', padding: '2px' }}>
          <button 
            onClick={() => setOrderType('LIMIT')}
            style={{ 
              padding: '6px', 
              background: orderType === 'LIMIT' ? 'rgba(255, 255, 255, 0.08)' : 'transparent',
              border: 'none',
              borderRadius: '4px',
              color: orderType === 'LIMIT' ? 'white' : 'var(--text-muted)',
              fontSize: '0.8rem',
              fontWeight: '600',
              cursor: 'pointer'
            }}
          >
            LIMIT
          </button>
          <button 
            onClick={() => setOrderType('MARKET')}
            style={{ 
              padding: '6px', 
              background: orderType === 'MARKET' ? 'rgba(255, 255, 255, 0.08)' : 'transparent',
              border: 'none',
              borderRadius: '4px',
              color: orderType === 'MARKET' ? 'white' : 'var(--text-muted)',
              fontSize: '0.8rem',
              fontWeight: '600',
              cursor: 'pointer'
            }}
          >
            MARKET
          </button>
        </div>

        {/* Form Inputs */}
        <form onSubmit={handlePlaceOrder}>
          {orderType === 'LIMIT' && (
            <div className="terminal-input-group">
              <label className="terminal-label">Limit Price</label>
              <div className="terminal-input-wrapper">
                <input 
                  type="number" 
                  step="any"
                  value={price}
                  onChange={(e) => setPrice(e.target.value)}
                  className="terminal-input"
                  required
                />
                <span className="terminal-input-suffix">{quote}</span>
              </div>
            </div>
          )}

          <div className="terminal-input-group">
            <label className="terminal-label">Quantity</label>
            <div className="terminal-input-wrapper">
              <input 
                type="number" 
                step="any"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                placeholder="0.00"
                className="terminal-input"
                required
              />
              <span className="terminal-input-suffix">{base}</span>
            </div>
          </div>

          {/* Balance Context */}
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '14px' }}>
            <span>Available {side === 'BUY' ? quote : base}:</span>
            <span className="num-mono" style={{ fontWeight: '600', color: '#fff' }}>
              {side === 'BUY' 
                ? `${quoteAvailable.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${quote}`
                : `${basePosition.toLocaleString(undefined, { minimumFractionDigits: 4 })} ${base}`
              }
            </span>
          </div>

          {/* Order Cost Estimate */}
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '20px' }}>
            <span>Est. Order Value:</span>
            <span className="num-mono" style={{ fontWeight: '600', color: '#fff' }}>
              {estCost.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} {quote}
            </span>
          </div>

          <button 
            type="submit" 
            className={`terminal-btn ${side === 'BUY' ? 'btn-buy' : 'btn-sell'}`}
          >
            Place {side} {orderType} Order
          </button>
        </form>

        {/* Error Notification */}
        {errorMsg && (
          <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', padding: '10px', background: 'rgba(239, 68, 68, 0.1)', border: '1px solid var(--color-down)', borderRadius: '6px', marginTop: '14px', fontSize: '0.75rem' }}>
            <ShieldAlert size={16} style={{ color: 'var(--color-down)', flexShrink: 0 }} />
            <span style={{ color: 'var(--color-down)' }}>{errorMsg}</span>
          </div>
        )}

        {/* Order Result Banner (from WS server) */}
        {orderResult && (
          <div style={{ 
            display: 'flex', 
            gap: '8px', 
            alignItems: 'flex-start', 
            padding: '10px', 
            background: orderResult.status === 'success' ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)', 
            border: orderResult.status === 'success' ? '1px solid var(--color-up)' : '1px solid var(--color-down)', 
            borderRadius: '6px', 
            marginTop: '14px', 
            fontSize: '0.75rem' 
          }}>
            {orderResult.status === 'success' ? (
              <CheckCircle size={16} style={{ color: 'var(--color-up)', flexShrink: 0 }} />
            ) : (
              <ShieldAlert size={16} style={{ color: 'var(--color-down)', flexShrink: 0 }} />
            )}
            <span style={{ color: orderResult.status === 'success' ? 'var(--color-up)' : 'var(--color-down)' }}>
              {orderResult.message}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
