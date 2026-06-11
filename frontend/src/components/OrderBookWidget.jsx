import React from 'react';
import { useStore } from '../store/useStore';
import { WidgetShell, WidgetToolbar, WidgetEmpty } from './WidgetShell';
import { cn } from '@/lib/utils';
import { ScrollArea } from '@/components/ui/scroll-area';
import { AlignLeft } from 'lucide-react';

function DepthRow({ side, price, qty, cumulative, pct, priceDecimals, qtyDecimals }) {
  const isAsk = side === 'ask';
  return (
    <div className="relative grid grid-cols-3 px-3 py-1 text-xs">
      <div
        className={cn('absolute inset-y-0 right-0', isAsk ? 'bg-trading-down/10' : 'bg-trading-up/10')}
        style={{ width: `${pct}%` }}
        aria-hidden
      />
      <div className={cn('relative num-mono font-semibold', isAsk ? 'text-trading-down' : 'text-trading-up')}>
        {price.toFixed(priceDecimals)}
      </div>
      <div className="relative num-mono text-right text-muted-foreground">
        {qty.toFixed(qtyDecimals)}
      </div>
      <div className="relative num-mono text-right text-muted-foreground/80">
        {cumulative.toFixed(qtyDecimals)}
      </div>
    </div>
  );
}

export default function OrderBookWidget() {
  const activeSymbol = useStore(state => state.activeSymbol);
  const ob = useStore(state => state.orderBooks[activeSymbol]);
  const ticker = useStore(state => state.tickerData[activeSymbol]);

  if (!ob || !ob.bids || !ob.asks) {
    return (
      <WidgetShell icon={AlignLeft} title="Level 2 Order Book" className="h-full">
        <WidgetEmpty message="Loading order book…" />
      </WidgetShell>
    );
  }

  const bids = ob.bids;
  const asks = ob.asks;

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
  const priceDecimals = (
    activeSymbol.includes('XRP') ||
    activeSymbol.includes('ADA') ||
    activeSymbol.includes('DOGE') ||
    (ticker && ticker.price < 2.0)
  ) ? 4 : 2;
  const qtyDecimals = activeSymbol.includes('USDT') ? 4 : 2;

  const bestBid = bids[0] ? bids[0][0] : 0;
  const bestAsk = asks[0] ? asks[0][0] : 0;
  const spread = bestAsk - bestBid;
  const spreadPct = bestAsk > 0 ? (spread / bestAsk) * 100 : 0;

  const bidVol = processedBids.reduce((s, r) => s + r.qty, 0);
  const askVol = processedAsks.reduce((s, r) => s + r.qty, 0);
  const totalVol = bidVol + askVol || 1;
  const bidPct = (bidVol / totalVol) * 100;
  const isLong = bidPct >= 50;
  const displayAsks = [...processedAsks].reverse();

  const imbalanceToolbar = (
    <WidgetToolbar className="flex-col items-stretch gap-1 py-1.5">
      <div className="flex items-center justify-between text-[0.62rem]">
        <span className="font-bold text-trading-up">B {bidPct.toFixed(0)}%</span>
        <span className="text-muted-foreground">Order Imbalance</span>
        <span className="font-bold text-trading-down">{(100 - bidPct).toFixed(0)}% A</span>
      </div>
      <div className="imbalance-bar">
        <div
          className={cn(
            'imbalance-fill h-full rounded-sm transition-all',
            isLong ? 'bg-gradient-to-r from-trading-up/60 to-trading-up' : 'bg-gradient-to-r from-trading-down/60 to-trading-down',
          )}
          style={{ width: `${bidPct}%` }}
        />
      </div>
    </WidgetToolbar>
  );

  return (
    <WidgetShell
      className="h-full"
      icon={AlignLeft}
      title="Level 2"
      headerRight={
        <span className="text-[0.62rem] text-muted-foreground">
          Spread:{' '}
          <span className="num-mono font-semibold text-foreground">{spread.toFixed(priceDecimals)}</span>
        </span>
      }
      toolbar={imbalanceToolbar}
      contentClassName="flex min-h-0 flex-1 flex-col overflow-hidden p-0"
    >
      <div className="sticky top-0 z-[1] grid shrink-0 grid-cols-3 border-b border-border/60 bg-background/95 px-3 py-1 text-[0.62rem] font-semibold uppercase tracking-wide text-muted-foreground backdrop-blur-sm">
        <div>Price</div>
        <div className="text-right">Size</div>
        <div className="text-right">Total</div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <ScrollArea className="min-h-0 flex-1">
          <div className="flex min-h-full flex-col justify-end">
            {displayAsks.map((ask, idx) => (
              <DepthRow
                key={`ask-${idx}`}
                side="ask"
                price={ask.price}
                qty={ask.qty}
                cumulative={ask.cumulative}
                pct={(ask.cumulative / maxCumulative) * 100}
                priceDecimals={priceDecimals}
                qtyDecimals={qtyDecimals}
              />
            ))}
          </div>
        </ScrollArea>

        <div className="sticky z-[2] flex shrink-0 items-center justify-between border-y border-border bg-muted/30 px-3 py-1.5 backdrop-blur-sm">
          <div className={cn(
            'num-mono text-lg font-extrabold tracking-tight',
            ticker?.change_24h >= 0 ? 'text-trading-up' : 'text-trading-down',
          )}>
            {ticker ? ticker.price.toFixed(priceDecimals) : '—'}
          </div>
          <div className="text-[0.62rem] text-muted-foreground">
            <span className="num-mono">{spreadPct.toFixed(3)}%</span> spread
          </div>
        </div>

        <ScrollArea className="min-h-0 flex-1">
          {processedBids.map((bid, idx) => (
            <DepthRow
              key={`bid-${idx}`}
              side="bid"
              price={bid.price}
              qty={bid.qty}
              cumulative={bid.cumulative}
              pct={(bid.cumulative / maxCumulative) * 100}
              priceDecimals={priceDecimals}
              qtyDecimals={qtyDecimals}
            />
          ))}
        </ScrollArea>
      </div>
    </WidgetShell>
  );
}
