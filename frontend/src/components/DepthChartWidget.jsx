import React, { useMemo } from 'react';
import { useStore } from '../store/useStore';
import { WidgetShell, WidgetEmpty } from './WidgetShell';
import { cn } from '@/lib/utils';
import { LineChart } from 'lucide-react';

const DEPTH_LEVELS = 24;

function priceDecimalsFor(symbol, ticker) {
  if (
    symbol.includes('XRP') ||
    symbol.includes('ADA') ||
    symbol.includes('DOGE') ||
    (ticker && ticker.price < 2.0)
  ) {
    return 4;
  }
  return 2;
}

function qtyDecimalsFor(symbol) {
  return symbol.includes('USDT') ? 4 : 2;
}

/** @param {[number, number][]} levels */
function processSide(levels) {
  let cumulative = 0;
  return levels.map(([price, qty]) => {
    cumulative += qty;
    return { price, qty, cumulative };
  });
}

export default function DepthChartWidget() {
  const activeSymbol = useStore((state) => state.activeSymbol);
  const ob = useStore((state) => state.orderBooks[activeSymbol]);
  const ticker = useStore((state) => state.tickerData[activeSymbol]);

  const priceDecimals = priceDecimalsFor(activeSymbol, ticker);
  const qtyDecimals = qtyDecimalsFor(activeSymbol);

  const depth = useMemo(() => {
    if (!ob?.bids?.length && !ob?.asks?.length) return null;

    const bids = processSide(ob.bids || []);
    const asks = processSide(ob.asks || []);
    if (!bids.length && !asks.length) return null;

    const bidSlice = bids.slice(0, DEPTH_LEVELS);
    const askSlice = asks.slice(0, DEPTH_LEVELS);
    const maxCumulative = Math.max(
      bidSlice[bidSlice.length - 1]?.cumulative ?? 0,
      askSlice[askSlice.length - 1]?.cumulative ?? 0,
      1,
    );

    const bestBid = bids[0]?.price ?? 0;
    const bestAsk = asks[0]?.price ?? 0;
    const mid = bestBid && bestAsk
      ? (bestBid + bestAsk) / 2
      : (ticker?.price ?? bestBid ?? bestAsk);

    const askRows = [...askSlice].reverse();
    const bidRows = bidSlice;

    return {
      askRows,
      bidRows,
      maxCumulative,
      bestBid,
      bestAsk,
      mid,
      spread: bestAsk && bestBid ? bestAsk - bestBid : 0,
    };
  }, [ob, ticker?.price]);

  if (!ob) {
    return (
      <WidgetShell icon={LineChart} title="Market Depth" className="h-full">
        <WidgetEmpty message="Loading depth…" />
      </WidgetShell>
    );
  }

  if (!depth) {
    return (
      <WidgetShell icon={LineChart} title="Market Depth" className="h-full">
        <WidgetEmpty message="No depth data for this symbol yet." />
      </WidgetShell>
    );
  }

  const { askRows, bidRows, maxCumulative, bestBid, bestAsk, mid, spread } = depth;

  return (
    <WidgetShell
      className="h-full"
      icon={LineChart}
      title="Market Depth"
      headerRight={
        <span className="text-[0.62rem] text-muted-foreground">
          Spread{' '}
          <span className="num-mono font-semibold text-foreground">
            {spread > 0 ? spread.toFixed(priceDecimals) : '—'}
          </span>
        </span>
      }
      contentClassName="flex min-h-0 flex-1 flex-col overflow-hidden p-0"
    >
      <div className="grid shrink-0 grid-cols-[1fr_auto_1fr] border-b border-border/60 bg-background/95 px-2 py-1 text-[0.62rem] font-semibold uppercase tracking-wide text-muted-foreground">
        <div className="text-right text-trading-up">Bid size</div>
        <div className="px-2 text-center">Price</div>
        <div className="text-trading-down">Ask size</div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="flex min-h-0 flex-1 flex-col justify-end overflow-y-auto">
          {askRows.map((row, idx) => (
            <DepthLadderRow
              key={`ask-${idx}`}
              side="ask"
              price={row.price}
              qty={row.qty}
              cumulative={row.cumulative}
              maxCumulative={maxCumulative}
              priceDecimals={priceDecimals}
              qtyDecimals={qtyDecimals}
            />
          ))}
        </div>

        <div className="sticky z-[2] shrink-0 border-y border-border bg-muted/30 px-3 py-1.5 text-center backdrop-blur-sm">
          <div className="text-[0.62rem] font-semibold uppercase tracking-wide text-muted-foreground">
            Mid
          </div>
          <div
            className={cn(
              'num-mono text-lg font-extrabold tracking-tight',
              ticker?.change_24h >= 0 ? 'text-trading-up' : 'text-trading-down',
            )}
          >
            {mid ? mid.toFixed(priceDecimals) : '—'}
          </div>
          <div className="num-mono text-[0.62rem] text-muted-foreground">
            {bestBid ? bestBid.toFixed(priceDecimals) : '—'}
            {' · '}
            {bestAsk ? bestAsk.toFixed(priceDecimals) : '—'}
          </div>
        </div>

        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          {bidRows.map((row, idx) => (
            <DepthLadderRow
              key={`bid-${idx}`}
              side="bid"
              price={row.price}
              qty={row.qty}
              cumulative={row.cumulative}
              maxCumulative={maxCumulative}
              priceDecimals={priceDecimals}
              qtyDecimals={qtyDecimals}
            />
          ))}
        </div>
      </div>
    </WidgetShell>
  );
}

function DepthLadderRow({
  side,
  price,
  qty,
  cumulative,
  maxCumulative,
  priceDecimals,
  qtyDecimals,
}) {
  const isAsk = side === 'ask';
  const pct = (cumulative / maxCumulative) * 100;

  return (
    <div className="grid grid-cols-[1fr_auto_1fr] items-center px-2 py-0.5 text-xs">
      <div className="relative flex h-5 items-center justify-end pr-1">
        {!isAsk && (
          <>
            <div
              className="absolute inset-y-0 right-0 rounded-l-sm bg-trading-up/15"
              style={{ width: `${pct}%` }}
              aria-hidden
            />
            <span className="relative num-mono text-[0.62rem] text-muted-foreground">
              {qty.toFixed(qtyDecimals)}
            </span>
          </>
        )}
      </div>

      <div
        className={cn(
          'relative z-[1] min-w-[5.5rem] px-2 text-center num-mono font-semibold',
          isAsk ? 'text-trading-down' : 'text-trading-up',
        )}
      >
        {price.toFixed(priceDecimals)}
      </div>

      <div className="relative flex h-5 items-center pl-1">
        {isAsk && (
          <>
            <div
              className="absolute inset-y-0 left-0 rounded-r-sm bg-trading-down/15"
              style={{ width: `${pct}%` }}
              aria-hidden
            />
            <span className="relative num-mono text-[0.62rem] text-muted-foreground">
              {qty.toFixed(qtyDecimals)}
            </span>
          </>
        )}
      </div>
    </div>
  );
}
