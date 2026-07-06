/**
 * ChartSymbolSwitcher — click chart header symbol to search & switch asset.
 */
import { useMemo, useState } from 'react';
import { ChevronsUpDown } from 'lucide-react';
import { useStore } from '../../store/useStore';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';

function assetKind(sym) {
  if (sym.includes('USDT')) return 'crypto';
  if (['SPY', 'QQQ'].includes(sym)) return 'etf';
  return 'equity';
}

const KIND_LABEL = { crypto: 'Crypto', etf: 'ETF', equity: 'Equity' };

function fmtPrice(sym, tickerData) {
  const t = tickerData[sym];
  if (!t?.price) return null;
  const dec = sym.includes('XRP') || sym.includes('ADA') || sym.includes('DOGE') || t.price < 2 ? 4 : 2;
  return t.price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

export default function ChartSymbolSwitcher({ className, compact = false }) {
  const [open, setOpen] = useState(false);
  const symbolsList = useStore((s) => s.symbolsList);
  const activeSymbol = useStore((s) => s.activeSymbol);
  const tickerData = useStore((s) => s.tickerData);
  const setActiveSymbol = useStore((s) => s.setActiveSymbol);

  const sortedSymbols = useMemo(
    () => [...symbolsList].sort((a, b) => a.localeCompare(b)),
    [symbolsList],
  );

  const pick = (sym) => {
    if (sym && sym !== activeSymbol) setActiveSymbol(sym);
    setOpen(false);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size={compact ? 'xs' : 'sm'}
          className={cn(
            'chart-symbol-switcher h-auto max-w-full gap-1 px-1 font-bold uppercase tracking-wide text-foreground hover:bg-muted/50',
            !compact && 'widget-title text-[length:var(--fs-xs)]',
            className,
          )}
          aria-label={`Chart symbol ${activeSymbol}. Click to change.`}
          title="Change symbol"
        >
          <span className="truncate">{activeSymbol}</span>
          <ChevronsUpDown size={compact ? 11 : 12} className="shrink-0 opacity-55" aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" side="bottom" className="w-80 p-0">
        <Command>
          <CommandInput placeholder="Search symbol…" autoFocus />
          <CommandList className="max-h-64">
            <CommandEmpty>No symbol found.</CommandEmpty>
            <CommandGroup heading="Watchlist">
              {sortedSymbols.map((sym) => {
                const kind = assetKind(sym);
                const price = fmtPrice(sym, tickerData);
                const change = tickerData[sym]?.change_24h;
                const isActive = sym === activeSymbol;
                const shortSym = sym.replace('USDT', '');

                return (
                  <CommandItem
                    key={sym}
                    value={`${sym} ${shortSym} ${KIND_LABEL[kind]}`}
                    onSelect={() => pick(sym)}
                    className={cn(isActive && 'bg-muted/80')}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="font-semibold">{sym}</span>
                      <span className="ml-2 text-[0.65rem] text-muted-foreground">{KIND_LABEL[kind]}</span>
                    </span>
                    <span className="flex shrink-0 flex-col items-end text-[0.65rem] num-mono">
                      {price != null && <span>{price}</span>}
                      {change != null && (
                        <span className={change >= 0 ? 'text-trading-up' : 'text-trading-down'}>
                          {change >= 0 ? '+' : ''}
                          {change.toFixed(2)}%
                        </span>
                      )}
                    </span>
                  </CommandItem>
                );
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
