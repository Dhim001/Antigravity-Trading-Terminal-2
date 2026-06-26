import React from 'react';
import { useStore } from '../store/useStore';
import { WidgetShell, WidgetToolbar, WidgetEmpty } from './WidgetShell';
import { cn } from '@/lib/utils';
import { ScrollArea } from '@/components/ui/scroll-area';
import { AlignLeft } from 'lucide-react';
import { useOrderBookDepth } from '../hooks/useOrderBookDepth';
import { flashClass, useOrderBookFlash } from '../hooks/useOrderBookFlash';
import { useMassiveHealth } from '../hooks/useMassiveHealth';
import { massiveBookBadge } from '../lib/massiveMarket';
import { Badge } from '@/components/ui/badge';

function DepthRow({ side, price, qty, cumulative, pct, priceDecimals, qtyDecimals, flashCls = '' }) {
  const isAsk = side === 'ask';
  return (
    <div className={cn('relative grid grid-cols-3 px-3 py-1 text-xs', flashCls)}>
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
  const terminalMode = useStore(state => state.terminalMode);
  const massiveHealth = useMassiveHealth();
  const ticker = useStore(state => state.tickerData[activeSymbol]);
  const ob = useStore(state => state.orderBooks[activeSymbol]);
  const flash = useOrderBookFlash(activeSymbol);
  const depth = useOrderBookDepth(activeSymbol);
  const bookBadge = massiveBookBadge(activeSymbol, terminalMode, massiveHealth);

  if (!ob || !ob.bids || !ob.asks || !depth) {
    return (
      <WidgetShell icon={AlignLeft} title="Level 2 Order Book" className="h-full">
        <WidgetEmpty message="Loading order book…" />
      </WidgetShell>
    );
  }

  const {
    asks,
    bids,
    maxCumulative,
    spread,
    spreadPct,
    bidPct,
    askPct,
    skew,
    priceDecimals,
    qtyDecimals,
  } = depth;
  const displayAsks = [...asks].reverse();

  const imbalanceToolbar = (
    <WidgetToolbar compact className="depth-imbalance-toolbar">
      <div className="depth-imbalance">
        <div className="depth-imbalance__meta">
          <div className="depth-imbalance__side depth-imbalance__side--bid">
            <span className="depth-imbalance__label">Bids</span>
            <span className="depth-imbalance__pct num-mono">{bidPct.toFixed(1)}%</span>
          </div>

          <div className="depth-imbalance__center">
            <span className="depth-imbalance__title">Order Imbalance</span>
            <span
              className={cn(
                'depth-imbalance__bias num-mono',
                skew > 0.5 && 'depth-imbalance__bias--bid',
                skew < -0.5 && 'depth-imbalance__bias--ask',
              )}
            >
              {Math.abs(skew) <= 0.5
                ? 'Balanced'
                : `${skew > 0 ? '+' : ''}${skew.toFixed(1)}% ${skew > 0 ? 'bid' : 'ask'}`}
            </span>
          </div>

          <div className="depth-imbalance__side depth-imbalance__side--ask">
            <span className="depth-imbalance__label">Asks</span>
            <span className="depth-imbalance__pct num-mono">{askPct.toFixed(1)}%</span>
          </div>
        </div>

        <div className="depth-imbalance__track" aria-hidden>
          <div
            className="depth-imbalance__fill depth-imbalance__fill--bid"
            style={{ width: `${bidPct}%` }}
          />
          <div
            className="depth-imbalance__fill depth-imbalance__fill--ask"
            style={{ width: `${askPct}%` }}
          />
          <div
            className="depth-imbalance__marker"
            style={{ left: `${bidPct}%` }}
          />
        </div>
      </div>
    </WidgetToolbar>
  );

  return (
    <WidgetShell
      className="h-full"
      icon={AlignLeft}
      title="Level 2"
      headerRight={
        <div className="flex items-center gap-2">
          {bookBadge && (
            <Badge variant="outline" className="h-4 px-1 text-[0.55rem] uppercase tracking-wide">
              {bookBadge}
            </Badge>
          )}
          <span className="text-[0.62rem] text-muted-foreground">
            Spread:{' '}
            <span className="num-mono font-semibold text-foreground">{spread.toFixed(priceDecimals)}</span>
          </span>
        </div>
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
                key={`ask-${ask.price}-${idx}`}
                side="ask"
                price={ask.price}
                qty={ask.qty}
                cumulative={ask.cumulative}
                pct={(ask.cumulative / maxCumulative) * 100}
                priceDecimals={priceDecimals}
                qtyDecimals={qtyDecimals}
                flashCls={idx === 0 ? flashClass(flash.ask) : ''}
              />
            ))}
          </div>
        </ScrollArea>

        <div
          className={cn(
            'sticky z-[2] flex shrink-0 items-center justify-between border-y border-border bg-muted/30 px-3 py-1.5 backdrop-blur-sm',
            flash.bid || flash.ask ? flashClass(flash.bid || flash.ask) : '',
          )}
        >
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
          {bids.map((bid, idx) => (
            <DepthRow
              key={`bid-${bid.price}-${idx}`}
              side="bid"
              price={bid.price}
              qty={bid.qty}
              cumulative={bid.cumulative}
              pct={(bid.cumulative / maxCumulative) * 100}
              priceDecimals={priceDecimals}
              qtyDecimals={qtyDecimals}
              flashCls={idx === 0 ? flashClass(flash.bid) : ''}
            />
          ))}
        </ScrollArea>
      </div>
    </WidgetShell>
  );
}
