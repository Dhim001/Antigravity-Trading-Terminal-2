import { useMemo } from 'react';
import { useStore } from '../store/useStore';
import { cn } from '@/lib/utils';
import {
  CommandDialog,
  Command,
  CommandInput,
  CommandList,
  CommandEmpty,
  CommandGroup,
  CommandItem,
  CommandSeparator,
  CommandShortcut,
} from '@/components/ui/command';
import {
  BarChart2, LayoutGrid, Settings, Bitcoin, LineChart,
} from 'lucide-react';

function assetKind(sym) {
  if (sym.includes('USDT')) return 'crypto';
  if (['SPY', 'QQQ'].includes(sym)) return 'etf';
  return 'equity';
}

const KIND_CLASS = {
  crypto: 'text-trading-warn',
  etf: 'text-trading-accent',
  equity: 'text-primary',
};

export default function SymbolCommandPalette({ open, onOpenChange, onOpenAdmin }) {
  const symbolsList = useStore(state => state.symbolsList);
  const activeSymbol = useStore(state => state.activeSymbol);
  const tickerData = useStore(state => state.tickerData);
  const setActiveSymbol = useStore(state => state.setActiveSymbol);
  const setViewMode = useStore(state => state.setViewMode);

  const sortedSymbols = useMemo(
    () => [...symbolsList].sort((a, b) => a.localeCompare(b)),
    [symbolsList],
  );

  const run = (action) => {
    action();
    onOpenChange(false);
  };

  const fmtPrice = (sym) => {
    const t = tickerData[sym];
    if (!t?.price) return null;
    const dec = sym.includes('XRP') || sym.includes('ADA') || sym.includes('DOGE') || t.price < 2 ? 4 : 2;
    return t.price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
  };

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Command palette"
      description="Search symbols and quick actions"
    >
      <Command>
        <CommandInput placeholder="Search symbol or command…" />
        <CommandList>
          <CommandEmpty>No matches found.</CommandEmpty>

          <CommandGroup heading="Symbols">
            {sortedSymbols.map(sym => {
              const kind = assetKind(sym);
              const price = fmtPrice(sym);
              const change = tickerData[sym]?.change_24h;
              const isActive = sym === activeSymbol;

              return (
                <CommandItem
                  key={sym}
                  value={`${sym} ${sym.replace('USDT', '')}`}
                  data-checked={isActive}
                  onSelect={() => run(() => setActiveSymbol(sym))}
                >
                  {kind === 'crypto' ? (
                    <Bitcoin className={cn('shrink-0', KIND_CLASS[kind])} aria-hidden />
                  ) : (
                    <LineChart className={cn('shrink-0', KIND_CLASS[kind])} aria-hidden />
                  )}
                  <span className="icon-label-tight min-w-0">
                    <span className="font-semibold">{sym.replace('USDT', '')}</span>
                    {sym.includes('USDT') && (
                      <span className="text-[0.62rem] text-muted-foreground">USDT</span>
                    )}
                  </span>
                  {price != null && (
                    <span className="ml-auto num-mono text-xs text-muted-foreground">{price}</span>
                  )}
                  {change != null && (
                    <span className={cn(
                      'num-mono text-[0.62rem] font-semibold',
                      change >= 0 ? 'text-trading-up' : 'text-trading-down',
                    )}>
                      {change >= 0 ? '+' : ''}{change.toFixed(2)}%
                    </span>
                  )}
                </CommandItem>
              );
            })}
          </CommandGroup>

          <CommandSeparator />

          <CommandGroup heading="Navigation">
            <CommandItem value="single chart view" onSelect={() => run(() => setViewMode('single'))}>
              <BarChart2 aria-hidden />
              Single Chart View
              <CommandShortcut>⌘1</CommandShortcut>
            </CommandItem>
            <CommandItem value="multi chart grid" onSelect={() => run(() => setViewMode('multi'))}>
              <LayoutGrid aria-hidden />
              Multi-Chart Grid
              <CommandShortcut>⌘2</CommandShortcut>
            </CommandItem>
            <CommandItem value="system settings admin" onSelect={() => run(() => onOpenAdmin?.())}>
              <Settings aria-hidden />
              System Control Panel
            </CommandItem>
          </CommandGroup>
        </CommandList>
      </Command>
    </CommandDialog>
  );
}
