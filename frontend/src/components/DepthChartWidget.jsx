import React, { useMemo, useState } from 'react';
import { useStore } from '../store/useStore';
import { WidgetShell, WidgetEmpty } from './WidgetShell';
import { cn } from '@/lib/utils';
import { LineChart } from 'lucide-react';
import {
  autoAggStep,
  useOrderBookDepth,
} from '../hooks/useOrderBookDepth';
import { flashClass, useOrderBookFlash } from '../hooks/useOrderBookFlash';
import { useMassiveHealth } from '../hooks/useMassiveHealth';
import { massiveBookBadge } from '../lib/massiveMarket';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';

const DEPTH_LEVELS = 24;
const AGG_OPTIONS = [
  { value: 'auto', label: 'Auto' },
  { value: '0.001', label: '0.001' },
  { value: '0.01', label: '0.01' },
  { value: '0.1', label: '0.1' },
  { value: '1', label: '1' },
  { value: '10', label: '10' },
];

export default function DepthChartWidget() {
  const activeSymbol = useStore((state) => state.activeSymbol);
  const terminalMode = useStore((state) => state.terminalMode);
  const massiveHealth = useMassiveHealth();
  const ob = useStore((state) => state.orderBooks[activeSymbol]);
  const [aggMode, setAggMode] = useState('auto');
  const flash = useOrderBookFlash(activeSymbol);

  const probeMid = useStore((state) => {
    const ticker = state.tickerData[activeSymbol];
    const book = state.orderBooks[activeSymbol];
    const bid = book?.bids?.[0]?.[0];
    const ask = book?.asks?.[0]?.[0];
    if (bid && ask) return (bid + ask) / 2;
    return ticker?.price ?? bid ?? ask ?? 0;
  });

  const aggStep = useMemo(() => {
    if (aggMode === 'auto') {
      const dec = probeMid >= 1 && probeMid < 10 ? 4 : probeMid < 2 ? 4 : 2;
      return autoAggStep(probeMid, dec);
    }
    return Number(aggMode);
  }, [aggMode, probeMid]);

  const depth = useOrderBookDepth(activeSymbol, { maxLevels: DEPTH_LEVELS, aggStep });
  const bookBadge = massiveBookBadge(activeSymbol, terminalMode, massiveHealth);

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

  const { askRows, bidRows, maxCumulative, bestBid, bestAsk, mid, spread, priceDecimals, qtyDecimals } = depth;

  return (
    <WidgetShell
      className="h-full"
      icon={LineChart}
      title="Market Depth"
      headerRight={
        <div className="flex items-center gap-2">
          {bookBadge && (
            <Badge variant="outline" className="h-4 px-1 text-[0.55rem] uppercase tracking-wide">
              {bookBadge}
            </Badge>
          )}
          <Select value={aggMode} onValueChange={setAggMode}>
            <SelectTrigger className="h-6 w-[4.5rem] text-[0.62rem]" aria-label="Price aggregation">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {AGG_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value} className="text-xs">
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="text-[0.62rem] text-muted-foreground">
            Spread{' '}
            <span className="num-mono font-semibold text-foreground">
              {spread > 0 ? spread.toFixed(priceDecimals) : '—'}
            </span>
          </span>
        </div>
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
              key={`ask-${row.price}-${idx}`}
              side="ask"
              price={row.price}
              qty={row.qty}
              cumulative={row.cumulative}
              maxCumulative={maxCumulative}
              priceDecimals={priceDecimals}
              qtyDecimals={qtyDecimals}
              flashCls={idx === askRows.length - 1 ? flashClass(flash.ask) : ''}
            />
          ))}
        </div>

        <div
          className={cn(
            'sticky z-[2] shrink-0 border-y border-border bg-muted/30 px-3 py-1.5 text-center backdrop-blur-sm',
            flash.bid || flash.ask ? flashClass(flash.bid || flash.ask) : '',
          )}
        >
          <div className="text-[0.62rem] font-semibold uppercase tracking-wide text-muted-foreground">
            Mid
          </div>
          <div className="num-mono text-lg font-extrabold tracking-tight text-foreground">
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
              key={`bid-${row.price}-${idx}`}
              side="bid"
              price={row.price}
              qty={row.qty}
              cumulative={row.cumulative}
              maxCumulative={maxCumulative}
              priceDecimals={priceDecimals}
              qtyDecimals={qtyDecimals}
              flashCls={idx === 0 ? flashClass(flash.bid) : ''}
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
  flashCls = '',
}) {
  const isAsk = side === 'ask';
  const pct = (cumulative / maxCumulative) * 100;

  return (
    <div className={cn('grid grid-cols-[1fr_auto_1fr] items-center px-2 py-0.5 text-xs', flashCls)}>
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
