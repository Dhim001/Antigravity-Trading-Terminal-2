import React from 'react';
import { useStore } from '../../store/useStore';
import { cn } from '@/lib/utils';
import { formatVol, getPriceDecimals } from '../../lib/chart/chartHelpers';

export default function ChartHeaderPrice({ symbol }) {
  const ticker = useStore((state) => state.tickerData[symbol]);
  const direction = useStore((state) => state.priceDirections[symbol]);

  if (!ticker) return null;
  const dec = getPriceDecimals(ticker.price);

  return (
    <div className="flex min-w-0 items-center gap-[var(--icon-gap-loose)] overflow-hidden text-sm">
      <span className={cn(
        'num-mono shrink-0 text-lg font-extrabold transition-colors',
        direction === 'up' ? 'text-trading-up' : direction === 'down' ? 'text-trading-down' : 'text-foreground',
      )}>
        {ticker.price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
      </span>
      <span className={cn('num-mono shrink-0 font-bold', ticker.change_24h >= 0 ? 'text-trading-up' : 'text-trading-down')}>
        {ticker.change_24h >= 0 ? '+' : ''}{Number(ticker.change_24h).toFixed(2)}%
      </span>
      <span className="hidden whitespace-nowrap text-xs text-muted-foreground xl:inline">
        H:<span className="num-mono"> {ticker.high_24h?.toFixed(dec)}</span>
        {' '}L:<span className="num-mono"> {ticker.low_24h?.toFixed(dec)}</span>
        {' '}V:<span className="num-mono"> {ticker.volume_24h ? formatVol(ticker.volume_24h) : '—'}</span>
      </span>
    </div>
  );
}
