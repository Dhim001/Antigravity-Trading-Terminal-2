import React from 'react';
import { useStore } from '../store/useStore';
import { AlignLeft } from 'lucide-react';

export default function OrderBookWidget() {
  const { activeSymbol, orderBooks, tickerData } = useStore();

  const ob = orderBooks[activeSymbol];
  const ticker = tickerData[activeSymbol];

  if (!ob || !ob.bids || !ob.asks) {
    return (
      <div className="widget-card" style={{ borderLeft: '1px solid var(--border-color)' }}>
        <div className="widget-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <AlignLeft size={15} className="logo-icon" />
            <span className="widget-title">Order Book</span>
          </div>
        </div>
        <div className="widget-content" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>
          Loading Order Book...
        </div>
      </div>
    );
  }

  const bids = ob.bids;
  const asks = ob.asks; // asks from server: [ [price, qty], ... ] index 0 is closest to mid price

  // Process cumulative sizes for depth visualization
  let cumAsk = 0;
  const processedAsks = asks.map(([price, qty]) => {
    cumAsk += qty;
    return { price, qty, cumulative: cumAsk };
  });

  let cumBid = 0;
  const processedBids = bids.map(([price, qty]) => {
    cumBid += qty;
    return { price, qty, cumulative: cumBid };
  });

  const maxCumulative = Math.max(cumAsk, cumBid) || 1.0;
  const priceDecimals = (activeSymbol.includes("XRP") || activeSymbol.includes("ADA") || activeSymbol.includes("DOGE") || (ticker && ticker.price < 2.0)) ? 4 : 2;
  const qtyDecimals = (activeSymbol.includes("USDT") || activeSymbol.includes("USD")) ? 4 : 2;

  // Spread calculation
  const bestBid = bids[0] ? bids[0][0] : 0;
  const bestAsk = asks[0] ? asks[0][0] : 0;
  const spread = bestAsk - bestBid;
  const spreadPct = bestAsk > 0 ? (spread / bestAsk) * 100 : 0.0;

  // We reverse processedAsks to display the highest price on top, so the lowest ask is closest to the center
  const displayAsks = [...processedAsks].reverse();

  return (
    <div className="widget-card" style={{ borderLeft: '1px solid var(--border-color)', height: '100%' }}>
      <div className="widget-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <AlignLeft size={15} className="logo-icon" />
          <span className="widget-title">Level 2 Order Book</span>
        </div>
      </div>
      
      <div className="widget-content" style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
        {/* Table Headers */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', padding: '8px 16px', fontSize: '0.75rem', color: 'var(--text-muted)', borderBottom: '1px solid var(--border-color)' }}>
          <div>Price ({activeSymbol.includes("USDT") ? "USDT" : "USD"})</div>
          <div style={{ textAlign: 'right' }}>Size</div>
          <div style={{ textAlign: 'right' }}>Total</div>
        </div>

        {/* Asks (Sells) */}
        <div style={{ flex: '1', overflowY: 'hidden', display: 'flex', flexDirection: 'column', justifyContent: 'end' }}>
          {displayAsks.map((ask, idx) => {
            const pct = (ask.cumulative / maxCumulative) * 100;
            return (
              <div 
                key={`ask-${idx}`}
                style={{ 
                  display: 'grid', 
                  gridTemplateColumns: '1fr 1fr 1fr', 
                  padding: '4px 16px', 
                  fontSize: '0.8rem', 
                  position: 'relative',
                  background: `linear-gradient(to left, rgba(239, 68, 68, 0.06) ${pct}%, transparent ${pct}%)`
                }}
              >
                <div className="num-mono text-down">{ask.price.toFixed(priceDecimals)}</div>
                <div className="num-mono" style={{ textAlign: 'right' }}>{ask.qty.toFixed(qtyDecimals)}</div>
                <div className="num-mono" style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{ask.cumulative.toFixed(qtyDecimals)}</div>
              </div>
            );
          })}
        </div>

        {/* Center Mid-Price and Spread row */}
        <div style={{ 
          display: 'flex', 
          justifyContent: 'space-between', 
          alignItems: 'center',
          padding: '8px 16px', 
          background: 'rgba(255, 255, 255, 0.01)', 
          borderTop: '1px solid var(--border-color)', 
          borderBottom: '1px solid var(--border-color)',
          fontSize: '0.85rem'
        }}>
          <div className="num-mono" style={{ fontWeight: '700', fontSize: '1rem', color: ticker?.change_24h >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
            {ticker ? ticker.price.toFixed(priceDecimals) : '0.00'}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            Spread: <span className="num-mono">{spread.toFixed(priceDecimals)}</span> ({spreadPct.toFixed(2)}%)
          </div>
        </div>

        {/* Bids (Buys) */}
        <div style={{ flex: '1', overflowY: 'hidden', display: 'flex', flexDirection: 'column', justifyContent: 'start' }}>
          {processedBids.map((bid, idx) => {
            const pct = (bid.cumulative / maxCumulative) * 100;
            return (
              <div 
                key={`bid-${idx}`}
                style={{ 
                  display: 'grid', 
                  gridTemplateColumns: '1fr 1fr 1fr', 
                  padding: '4px 16px', 
                  fontSize: '0.8rem', 
                  position: 'relative',
                  background: `linear-gradient(to left, rgba(16, 185, 129, 0.06) ${pct}%, transparent ${pct}%)`
                }}
              >
                <div className="num-mono text-up">{bid.price.toFixed(priceDecimals)}</div>
                <div className="num-mono" style={{ textAlign: 'right' }}>{bid.qty.toFixed(qtyDecimals)}</div>
                <div className="num-mono" style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{bid.cumulative.toFixed(qtyDecimals)}</div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
