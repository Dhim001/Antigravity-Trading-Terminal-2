/**
 * WatchlistWidget.jsx
 */
import React, { useEffect, useState, useMemo, useRef } from 'react';
import { useStore } from '../store/useStore';
import { WidgetShell, WidgetToolbar, WidgetEmpty, ScrollTablePanel } from './WidgetShell';
import { Input } from '@/components/ui/input';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { Search, Activity } from 'lucide-react';

const isCrypto = (sym) => sym.includes('USDT');
const isETF    = (sym) => ['SPY', 'QQQ'].includes(sym);
const getCategory = (sym) => isCrypto(sym) ? 'CRYPTO' : isETF(sym) ? 'ETF' : 'EQUITY';

const getPriceDecimals = (sym, price) => {
  if (sym.includes('XRP') || sym.includes('ADA') || sym.includes('DOGE') || (price != null && price < 2.0)) return 4;
  return 2;
};

const fmtVol = (v) => {
  if (!v) return '—';
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return v.toFixed(0);
};

function MiniSparkline({ points, isUp }) {
  if (!points || points.length < 2) return <span className="inline-block w-11" />;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const w = 44, h = 22;
  const xs = points.map((_, i) => (i / (points.length - 1)) * w);
  const ys = points.map(v => h - ((v - min) / range) * h * 0.85 - h * 0.075);
  const d = xs.map((x, i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(' ');
  const color = isUp ? 'var(--color-up)' : 'var(--color-down)';
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className="block shrink-0">
      <path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.85" />
    </svg>
  );
}

const WatchlistRow = React.memo(function WatchlistRow({ symbol }) {
  const info = useStore(state => state.tickerData[symbol]);
  const direction = useStore(state => state.priceDirections[symbol]);
  const activeSymbol = useStore(state => state.activeSymbol);
  const setActiveSymbol = useStore(state => state.setActiveSymbol);

  const [flashState, setFlashState] = useState(null);
  const sparkRef = useRef([]);

  useEffect(() => {
    if (info?.price) {
      const arr = sparkRef.current;
      if (arr.length === 0 || arr[arr.length - 1] !== info.price) {
        arr.push(info.price);
        if (arr.length > 24) arr.shift();
      }
    }
  }, [info?.price]);

  useEffect(() => {
    if (direction && direction !== 'flat') {
      setFlashState({ dir: direction, key: Date.now() });
    }
  }, [direction]);

  const cat = getCategory(symbol);
  const isActive = symbol === activeSymbol;
  const sparkData = sparkRef.current;
  const isUp = info?.change_24h >= 0;
  const dec = info ? getPriceDecimals(symbol, info.price) : 2;
  const shortSym = symbol.replace('USDT', '');
  const flashCls = flashState ? (flashState.dir === 'up' ? 'flash-up' : 'flash-down') : '';

  return (
    <tr
      onClick={() => setActiveSymbol(symbol)}
      className={cn(
        'cursor-pointer border-l-[3px] transition-colors hover:bg-muted/30',
        isActive ? 'border-l-primary bg-primary/10' : 'border-l-transparent',
      )}
    >
      <td className="py-1.5 pl-2 pr-1">
        <div className="icon-label-tight">
          <span
            className={cn(
              'size-1.5 shrink-0 rounded-full',
              cat === 'CRYPTO' && 'bg-trading-warn',
              cat === 'EQUITY' && 'bg-primary',
              cat === 'ETF' && 'bg-trading-accent',
              isActive && 'shadow-[0_0_5px_currentColor]',
            )}
          />
          <span className={cn('text-xs tracking-wide', isActive ? 'font-bold text-foreground' : 'font-semibold text-foreground/90')}>
            {shortSym}
          </span>
        </div>
      </td>
      <td className="px-0.5 py-1.5">
        <MiniSparkline points={sparkData} isUp={isUp} />
      </td>
      <td
        key={flashState?.key}
        className={cn(
          'num-mono px-1.5 py-1.5 text-right text-xs font-semibold',
          flashCls,
          flashState
            ? flashState.dir === 'up' ? 'text-trading-up' : 'text-trading-down'
            : 'text-foreground'
        )}
      >
        {info ? info.price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec }) : '…'}
      </td>
      <td className={cn('num-mono px-1.5 py-1.5 text-right text-[0.62rem] font-semibold', isUp ? 'text-trading-up' : 'text-trading-down')}>
        {info ? `${isUp ? '+' : ''}${Number(info.change_24h).toFixed(2)}%` : '—'}
      </td>
      <td className="num-mono py-1.5 pl-1 pr-2 text-right text-[0.62rem] text-muted-foreground">
        {info ? fmtVol(info.volume_24h) : '—'}
      </td>
    </tr>
  );
});

export default function WatchlistWidget() {
  const symbolsList = useStore(state => state.symbolsList);
  const [cat, setCat] = useState('ALL');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState({ field: 'symbol', dir: 'asc' });

  const tickerData = useStore(state => sort.field === 'symbol' ? null : state.tickerData);

  const handleSort = (field) => {
    setSort(prev => ({ field, dir: prev.field === field ? (prev.dir === 'asc' ? 'desc' : 'asc') : 'asc' }));
  };

  const displaySymbols = useMemo(() => {
    let list = symbolsList;
    if (cat !== 'ALL') list = list.filter(s => getCategory(s) === cat);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter(s => s.toLowerCase().includes(q));
    }
    return [...list].sort((a, b) => {
      if (sort.field === 'symbol') {
        return sort.dir === 'asc' ? a.localeCompare(b) : b.localeCompare(a);
      }
      const ia = tickerData?.[a], ib = tickerData?.[b];
      const va = ia?.[sort.field] ?? 0;
      const vb = ib?.[sort.field] ?? 0;
      return sort.dir === 'asc' ? va - vb : vb - va;
    });
  }, [symbolsList, cat, search, sort, tickerData]);

  const counts = useMemo(() => ({
    ALL: symbolsList.length,
    CRYPTO: symbolsList.filter(isCrypto).length,
    EQUITY: symbolsList.filter(s => !isCrypto(s) && !isETF(s)).length,
    ETF: symbolsList.filter(isETF).length,
  }), [symbolsList]);

  const toolbar = (
    <>
      <div className="relative w-full shrink-0 border-b border-border px-2.5 py-1.5">
        <Search size={12} className="pointer-events-none absolute top-1/2 left-[calc(0.625rem+var(--icon-gap))] -translate-y-1/2 shrink-0 text-muted-foreground" aria-hidden />
        <Input
          type="text"
          placeholder="Filter symbols…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="h-7 pl-8 text-xs"
        />
      </div>
      <WidgetToolbar className="py-0.5">
        <Tabs value={cat} onValueChange={setCat} className="w-full">
          <TabsList variant="line" className="scroll-panel-x no-scrollbar h-8 w-full justify-start rounded-none border-0 bg-transparent px-0">
            {[['ALL', 'All'], ['CRYPTO', 'Crypto'], ['EQUITY', 'Equity'], ['ETF', 'ETF']].map(([key, label]) => (
              <TabsTrigger key={key} value={key} className="px-2 text-[0.68rem]">
                {label}
                <Badge variant="secondary" className="h-4 min-w-4 px-1 text-[0.58rem] font-semibold">
                  {counts[key]}
                </Badge>
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </WidgetToolbar>
    </>
  );

  return (
    <WidgetShell icon={Activity} title="Watchlist" toolbar={toolbar} contentClassName="flex min-h-0 flex-col overflow-hidden p-0">
      <ScrollTablePanel>
        <table className="w-full min-w-[280px] border-collapse text-xs">
        <thead>
          <tr>
            {[
              { field: 'symbol', label: 'Symbol', align: 'left' },
              { field: null, label: '', align: 'left' },
              { field: 'price', label: 'Price', align: 'right' },
              { field: 'change_24h', label: '24h%', align: 'right' },
              { field: 'volume_24h', label: 'Vol', align: 'right' },
            ].map(({ field, label, align }) => (
              <th
                key={label || 'spark'}
                onClick={field ? () => handleSort(field) : undefined}
                className={cn(
                  'sticky top-0 z-[1] border-b border-border bg-background/95 px-2 py-1.5 text-[0.62rem] font-semibold uppercase tracking-wide text-muted-foreground backdrop-blur-sm',
                  align === 'right' ? 'text-right' : 'text-left',
                  field && 'cursor-pointer hover:text-foreground',
                  sort.field === field && 'text-trading-accent'
                )}
              >
                {label}
                {field && sort.field === field && (
                  <span className="ml-0.5 opacity-70">{sort.dir === 'asc' ? '↑' : '↓'}</span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {displaySymbols.map(symbol => (
            <WatchlistRow key={symbol} symbol={symbol} />
          ))}
        </tbody>
        </table>
        {displaySymbols.length === 0 && (
          <WidgetEmpty message="No symbols match your filter" />
        )}
      </ScrollTablePanel>
    </WidgetShell>
  );
}
