/**
 * MarketOverviewStrip.jsx
 * A full-width scrolling ticker tape showing all active symbols.
 * Positioned between header and main workspace.
 * Clicking a symbol tile switches the active symbol.
 */
import React, { useMemo } from 'react';
import { useStore } from '../store/useStore';

export default function MarketOverviewStrip() {
  const { symbolsList, tickerData, activeSymbol, setActiveSymbol } = useStore();

  const isCrypto = (sym) => sym.includes('USDT');
  const isETF = (sym) => ['SPY','QQQ'].includes(sym);
  const getColor = (sym) =>
    isCrypto(sym) ? 'var(--color-crypto)' :
    isETF(sym)    ? 'var(--color-etf)'    :
                    'var(--color-equity)';

  const getPriceDecimals = (sym, price) => {
    if (sym.includes('XRP') || sym.includes('ADA') || sym.includes('DOGE') || (price && price < 2.0)) return 4;
    return 2;
  };

  // Duplicate items so the scroll loop is seamless
  const items = useMemo(() => [...symbolsList, ...symbolsList], [symbolsList]);

  return (
    <div className="market-strip">
      <div className="strip-ticker">
        {items.map((sym, idx) => {
          const info = tickerData[sym];
          if (!info) {
            return (
              <div key={`${sym}-${idx}`} className="strip-item">
                <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: getColor(sym), flexShrink: 0 }} />
                <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, color: 'var(--text-secondary)' }}>{sym}</span>
                <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>—</span>
              </div>
            );
          }

          const isUp = info.change_24h >= 0;
          const dec = getPriceDecimals(sym, info.price);
          const isActive = activeSymbol === sym;

          return (
            <div
              key={`${sym}-${idx}`}
              className="strip-item"
              onClick={() => setActiveSymbol(sym)}
              style={{
                background: isActive ? 'rgba(37,99,235,0.1)' : undefined,
                borderBottom: isActive ? '2px solid var(--color-accent-light)' : '2px solid transparent',
              }}
            >
              <span style={{
                width: '5px', height: '5px', borderRadius: '50%',
                background: getColor(sym), flexShrink: 0,
                boxShadow: `0 0 4px ${getColor(sym)}`,
              }} />
              <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, color: isActive ? '#fff' : 'var(--text-secondary)', letterSpacing: '0.2px' }}>
                {sym.replace('USDT', '')}
              </span>
              <span className="num-mono" style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: isUp ? 'var(--color-up)' : 'var(--color-down)' }}>
                {info.price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
              </span>
              <span className="num-mono" style={{ fontSize: 'var(--fs-2xs)', color: isUp ? 'var(--color-up)' : 'var(--color-down)' }}>
                {isUp ? '▲' : '▼'}{Math.abs(info.change_24h).toFixed(2)}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
