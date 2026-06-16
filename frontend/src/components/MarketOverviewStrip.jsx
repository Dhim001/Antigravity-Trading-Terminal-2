import React, { useMemo } from 'react';
import { useStore } from '../store/useStore';
import { cn } from '@/lib/utils';

function getPriceDecimals(s, price) {
  if (s.includes('XRP') || s.includes('ADA') || s.includes('DOGE') || (price && price < 2.0)) return 4;
  return 2;
}

function assetKind(sym) {
  if (sym.includes('USDT')) return 'crypto';
  if (['SPY', 'QQQ'].includes(sym)) return 'etf';
  return 'equity';
}

const DOT_CLASS = {
  crypto: 'bg-trading-warn shadow-[0_0_4px_var(--color-crypto)]',
  etf: 'bg-trading-accent shadow-[0_0_4px_var(--color-etf)]',
  equity: 'bg-primary shadow-[0_0_4px_var(--color-equity)]',
};

function MarketStripItem({ sym, compact = false }) {
  const info = useStore(state => state.tickerData[sym]);
  const activeSymbol = useStore(state => state.activeSymbol);
  const setActiveSymbol = useStore(state => state.setActiveSymbol);

  const kind = assetKind(sym);
  const isActive = activeSymbol === sym;

  if (!info) {
    return (
      <div className="strip-item">
        <span className={cn('size-1.5 shrink-0 rounded-full', DOT_CLASS[kind])} />
        <span className="text-xs font-bold text-secondary-foreground">{sym}</span>
        <span className="text-xs text-muted-foreground">—</span>
      </div>
    );
  }

  const isUp = info.change_24h >= 0;
  const dec = getPriceDecimals(sym, info.price);

  return (
    <div
      className={cn(
        'strip-item border-b-2',
        isActive ? 'strip-item-active border-primary bg-primary/10' : 'border-transparent',
      )}
      onClick={() => setActiveSymbol(sym)}
    >
      <span className={cn('size-1.5 shrink-0 rounded-full', DOT_CLASS[kind])} />
      <span className={cn('text-xs font-bold tracking-wide', isActive ? 'text-foreground' : 'text-secondary-foreground')}>
        {compact ? sym.replace('USDT', '').slice(0, 4) : sym.replace('USDT', '')}
      </span>
      {!compact && (
        <>
      <span className={cn('num-mono text-xs font-semibold', isUp ? 'text-trading-up' : 'text-trading-down')}>
        {info.price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
      </span>
      <span className={cn('num-mono text-[0.62rem] strip-item-change', isUp ? 'text-trading-up' : 'text-trading-down')}>
        {isUp ? '▲' : '▼'}{Math.abs(info.change_24h).toFixed(2)}%
      </span>
        </>
      )}
    </div>
  );
}

export default function MarketOverviewStrip({ compact = false }) {
  const symbolsList = useStore(state => state.symbolsList);
  const items = useMemo(() => [...symbolsList, ...symbolsList], [symbolsList]);

  return (
    <div className={cn('market-strip', compact && 'market-strip--compact')}>
      <div className="strip-ticker">
        {items.map((sym, idx) => (
          <MarketStripItem key={`${sym}-${idx}`} sym={sym} compact={compact} />
        ))}
      </div>
    </div>
  );
}
