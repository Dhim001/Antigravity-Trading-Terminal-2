import React, { useMemo } from 'react';
import { useStore } from '../store/useStore';
import { AlignLeft } from 'lucide-react';

export default function OrderBookWidget() {
  const { activeSymbol, orderBooks, tickerData } = useStore();

  const ob = orderBooks[activeSymbol];
  const ticker = tickerData[activeSymbol];

  if (!ob || !ob.bids || !ob.asks) {
    return (
      <div className="widget-card" style={{ height: '100%' }}>
        <div className="widget-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <AlignLeft size={13} className="logo-icon" />
            <span className="widget-title">Level 2 Order Book</span>
          </div>
        </div>
        <div className="widget-content" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>
          Loading order book…
        </div>
      </div>
    );
  }

  const bids = ob.bids;
  const asks = ob.asks;

  // Cumulative depth calculation
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
  const priceDecimals = (activeSymbol.includes('XRP') || activeSymbol.includes('ADA') || activeSymbol.includes('DOGE') || (ticker && ticker.price < 2.0)) ? 4 : 2;
  const qtyDecimals = activeSymbol.includes('USDT') ? 4 : 2;

  // Spread
  const bestBid = bids[0] ? bids[0][0] : 0;
  const bestAsk = asks[0] ? asks[0][0] : 0;
  const spread    = bestAsk - bestBid;
  const spreadPct = bestAsk > 0 ? (spread / bestAsk) * 100 : 0;

  // Imbalance (bid volume vs ask volume within visible rows)
  const bidVol = processedBids.reduce((s, r) => s + r.qty, 0);
  const askVol = processedAsks.reduce((s, r) => s + r.qty, 0);
  const totalVol = bidVol + askVol || 1;
  const bidPct = (bidVol / totalVol) * 100;
  const isLong = bidPct >= 50;

  const displayAsks = [...processedAsks].reverse();

  return (
    <div className="widget-card" style={{ height: '100%' }}>
      <div className="widget-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <AlignLeft size={13} className="logo-icon" />
          <span className="widget-title">Level 2</span>
        </div>
        <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
          Spread: <span className="num-mono">{spread.toFixed(priceDecimals)}</span>
        </span>
      </div>

      {/* Bid/Ask Imbalance Meter */}
      <div style={{ padding: '6px 12px', borderBottom: '1px solid var(--border-color)', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4, fontSize: 'var(--fs-2xs)' }}>
          <span style={{ color: 'var(--color-up)', fontWeight: 700 }}>B {bidPct.toFixed(0)}%</span>
          <span style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-2xs)' }}>Order Imbalance</span>
          <span style={{ color: 'var(--color-down)', fontWeight: 700 }}>{(100 - bidPct).toFixed(0)}% A</span>
        </div>
        <div className="imbalance-bar">
          <div className="imbalance-fill" style={{
            width: `${bidPct}%`,
            background: isLong
              ? `linear-gradient(to right, rgba(16,185,129,0.6), rgba(16,185,129,0.9))`
              : `linear-gradient(to right, rgba(239,68,68,0.6), rgba(239,68,68,0.9))`,
          }} />
        </div>
      </div>

      <div className="widget-content" style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
        {/* Column headers */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', padding: '5px 12px', fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', borderBottom: '1px solid var(--border-subtle)', flexShrink: 0, fontWeight: 600, letterSpacing: '0.3px', textTransform: 'uppercase' }}>
          <div>Price</div>
          <div style={{ textAlign: 'right' }}>Size</div>
          <div style={{ textAlign: 'right' }}>Total</div>
        </div>

        {/* Asks */}
        <div style={{ flex: 1, overflowY: 'hidden', display: 'flex', flexDirection: 'column', justifyContent: 'flex-end' }}>
          {displayAsks.map((ask, idx) => {
            const pct = (ask.cumulative / maxCumulative) * 100;
            return (
              <div key={`ask-${idx}`} style={{
                display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', padding: '3.5px 12px',
                fontSize: 'var(--fs-xs)', position: 'relative',
                background: `linear-gradient(to left, rgba(239,68,68,0.08) ${pct}%, transparent ${pct}%)`,
              }}>
                <div className="num-mono text-down" style={{ fontWeight: 600 }}>{ask.price.toFixed(priceDecimals)}</div>
                <div className="num-mono" style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{ask.qty.toFixed(qtyDecimals)}</div>
                <div className="num-mono" style={{ textAlign: 'right', color: 'var(--text-muted)' }}>{ask.cumulative.toFixed(qtyDecimals)}</div>
              </div>
            );
          })}
        </div>

        {/* Mid price row */}
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '6px 12px', flexShrink: 0,
          borderTop: '1px solid var(--border-color)', borderBottom: '1px solid var(--border-color)',
          background: 'rgba(255,255,255,0.02)',
        }}>
          <div className="num-mono" style={{ fontWeight: 800, fontSize: 'var(--fs-lg)', color: ticker?.change_24h >= 0 ? 'var(--color-up)' : 'var(--color-down)', letterSpacing: '-0.3px' }}>
            {ticker ? ticker.price.toFixed(priceDecimals) : '—'}
          </div>
          <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
            <span className="num-mono">{spreadPct.toFixed(3)}%</span> spread
          </div>
        </div>

        {/* Bids */}
        <div style={{ flex: 1, overflowY: 'hidden', display: 'flex', flexDirection: 'column', justifyContent: 'flex-start' }}>
          {processedBids.map((bid, idx) => {
            const pct = (bid.cumulative / maxCumulative) * 100;
            return (
              <div key={`bid-${idx}`} style={{
                display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', padding: '3.5px 12px',
                fontSize: 'var(--fs-xs)', position: 'relative',
                background: `linear-gradient(to left, rgba(16,185,129,0.08) ${pct}%, transparent ${pct}%)`,
              }}>
                <div className="num-mono text-up" style={{ fontWeight: 600 }}>{bid.price.toFixed(priceDecimals)}</div>
                <div className="num-mono" style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{bid.qty.toFixed(qtyDecimals)}</div>
                <div className="num-mono" style={{ textAlign: 'right', color: 'var(--text-muted)' }}>{bid.cumulative.toFixed(qtyDecimals)}</div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
