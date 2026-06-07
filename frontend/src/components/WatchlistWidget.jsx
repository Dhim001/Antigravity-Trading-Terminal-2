import React, { useEffect, useState } from 'react';
import { useStore } from '../store/useStore';
import { Activity, ShieldAlert } from 'lucide-react';

export default function WatchlistWidget() {
  const { tickerData, priceDirections, activeSymbol, setActiveSymbol, connectionStatus, symbolsList } = useStore();
  const [flashStates, setFlashStates] = useState({});

  // Monitor price changes to trigger flash animations
  useEffect(() => {
    const newFlashStates = { ...flashStates };
    let changed = false;

    Object.entries(priceDirections).forEach(([symbol, dir]) => {
      if (dir !== 'flat' && (!flashStates[symbol] || flashStates[symbol].dir !== dir)) {
        newFlashStates[symbol] = { dir, key: Date.now() }; // Key forces re-render for anim
        changed = true;
      }
    });

    if (changed) {
      setFlashStates(newFlashStates);
    }
  }, [priceDirections]);

  const symbols = symbolsList;

  return (
    <div className="widget-card">
      <div className="widget-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Activity size={15} className="logo-icon" />
          <span className="widget-title">Market Watchlist</span>
        </div>
      </div>
      <div className="widget-content">
        <table className="terminal-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th style={{ textAlign: 'right' }}>Price</th>
              <th style={{ textAlign: 'right' }}>24h%</th>
            </tr>
          </thead>
          <tbody>
            {symbols.map((symbol) => {
              const info = tickerData[symbol];
              const flash = flashStates[symbol];
              const flashClass = flash ? (flash.dir === 'up' ? 'flash-up' : 'flash-down') : '';
              
              if (!info) {
                return (
                  <tr key={symbol}>
                    <td style={{ color: 'var(--text-muted)' }}>{symbol}</td>
                    <td colSpan={2} style={{ textAlign: 'right', color: 'var(--text-muted)', fontSize: '0.8rem' }}>
                      Loading...
                    </td>
                  </tr>
                );
              }

              const priceDecimals = (symbol.includes("XRP") || symbol.includes("ADA") || symbol.includes("DOGE") || info.price < 2.0) ? 4 : 2;
              const isUp = info.change_24h >= 0;

              return (
                <tr 
                  key={symbol} 
                  className={activeSymbol === symbol ? 'active-row' : ''}
                  onClick={() => setActiveSymbol(symbol)}
                  style={{ 
                    cursor: 'pointer',
                    backgroundColor: activeSymbol === symbol ? 'rgba(37, 99, 235, 0.08)' : 'transparent',
                    borderLeft: activeSymbol === symbol ? '3px solid var(--color-accent)' : '3px solid transparent'
                  }}
                >
                  <td style={{ fontWeight: '600', paddingLeft: activeSymbol === symbol ? '11px' : '14px' }}>
                    {symbol}
                  </td>
                  <td 
                    key={flash?.key} 
                    className={`${flashClass} num-mono`} 
                    style={{ 
                      textAlign: 'right', 
                      fontWeight: '600',
                      transition: 'color 0.2s',
                      color: flash ? (flash.dir === 'up' ? 'var(--color-up)' : 'var(--color-down)') : 'var(--text-primary)'
                    }}
                  >
                    {info.price.toLocaleString(undefined, { minimumFractionDigits: priceDecimals, maximumFractionDigits: priceDecimals })}
                  </td>
                  <td 
                    className={`num-mono ${isUp ? 'text-up' : 'text-down'}`} 
                    style={{ textAlign: 'right', fontWeight: '500' }}
                  >
                    {isUp ? '+' : ''}{info.change_24h}%
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
