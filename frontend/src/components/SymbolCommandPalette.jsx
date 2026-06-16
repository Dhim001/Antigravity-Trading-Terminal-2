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
  BarChart2, Brain, Bot, LayoutGrid, Search, Sparkles, SlidersHorizontal, ShieldAlert, LayoutTemplate,
} from 'lucide-react';
import { LAYOUT_MODE_CONFIG } from '../settings/layoutModes';

function assetKind(sym) {
  if (sym.includes('USDT')) return 'crypto';
  if (['SPY', 'QQQ'].includes(sym)) return 'etf';
  return 'equity';
}

const KIND_LABEL = {
  crypto: 'Crypto',
  etf: 'ETF',
  equity: 'Equity',
};

const KIND_CLASS = {
  crypto: 'command-palette__dot--crypto',
  etf: 'command-palette__dot--etf',
  equity: 'command-palette__dot--equity',
};

export default function SymbolCommandPalette({ open, onOpenChange, onOpenAdmin, onOpenSettings, onLayoutModeChange }) {
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
      className="command-palette"
      overlayClassName="command-palette-overlay"
    >
      <Command className="command-palette__command">
        <div className="command-palette__header">
          <div className="command-palette__header-lead">
            <div className="command-palette__header-icon" aria-hidden>
              <Sparkles size={14} />
            </div>
            <div className="command-palette__header-copy">
              <span className="command-palette__title">Quick Search</span>
              <span className="command-palette__subtitle">
                {sortedSymbols.length} symbols · navigation & commands
              </span>
            </div>
          </div>
          <kbd className="command-palette__esc">esc</kbd>
        </div>

        <CommandInput placeholder="Search symbol or command…" />

        <CommandList className="command-palette__list">
          <CommandEmpty className="command-palette__empty">
            <Search size={18} className="command-palette__empty-icon" aria-hidden />
            <span>No matches found</span>
          </CommandEmpty>

          <CommandGroup heading="Symbols" className="command-palette__group">
            {sortedSymbols.map(sym => {
              const kind = assetKind(sym);
              const price = fmtPrice(sym);
              const change = tickerData[sym]?.change_24h;
              const isActive = sym === activeSymbol;
              const shortSym = sym.replace('USDT', '');

              return (
                <CommandItem
                  key={sym}
                  value={`${sym} ${shortSym} ${KIND_LABEL[kind]}`}
                  data-checked={isActive}
                  className="command-palette__item command-palette__item--symbol"
                  onSelect={() => run(() => setActiveSymbol(sym))}
                >
                  <span className={cn('command-palette__dot', KIND_CLASS[kind])} aria-hidden />
                  <div className="command-palette__symbol-main min-w-0">
                    <span className="command-palette__symbol-name">{shortSym}</span>
                    <span className="command-palette__symbol-kind">{KIND_LABEL[kind]}</span>
                  </div>
                  <div className="command-palette__symbol-meta">
                    {price != null && (
                      <span className="command-palette__price num-mono">{price}</span>
                    )}
                    {change != null && (
                      <span className={cn(
                        'command-palette__change num-mono',
                        change >= 0 ? 'command-palette__change--up' : 'command-palette__change--down',
                      )}>
                        {change >= 0 ? '+' : ''}{change.toFixed(2)}%
                      </span>
                    )}
                  </div>
                </CommandItem>
              );
            })}
          </CommandGroup>

          <CommandSeparator className="command-palette__separator" />

          <CommandGroup heading="Navigation" className="command-palette__group">
            <CommandItem
              value="algo bot tab deploy"
              className="command-palette__item"
              onSelect={() => run(() => {
                window.dispatchEvent(new CustomEvent('dock-tab', { detail: 'algo' }));
              })}
            >
              <Bot aria-hidden />
              <span>Algo Bot Tab</span>
              <CommandShortcut>⌘B</CommandShortcut>
            </CommandItem>
            <CommandItem
              value="toggle watchlist sidebar"
              className="command-palette__item"
              onSelect={() => run(() => {
                window.dispatchEvent(new CustomEvent('sidebar-toggle'));
              })}
            >
              <LayoutGrid aria-hidden />
              <span>Toggle Watchlist Sidebar</span>
              <CommandShortcut>⌘[</CommandShortcut>
            </CommandItem>
            <CommandItem
              value="single chart view"
              className="command-palette__item"
              onSelect={() => run(() => setViewMode('single'))}
            >
              <BarChart2 aria-hidden />
              <span>Single Chart View</span>
              <CommandShortcut>⌘1</CommandShortcut>
            </CommandItem>
            <CommandItem
              value="multi chart grid"
              className="command-palette__item"
              onSelect={() => run(() => setViewMode('multi'))}
            >
              <LayoutGrid aria-hidden />
              <span>Multi-Chart Grid</span>
              <CommandShortcut>⌘2</CommandShortcut>
            </CommandItem>
            <CommandItem
              value="insights hub scanner analyst"
              className="command-palette__item"
              onSelect={() => run(() => {
                window.dispatchEvent(new CustomEvent('insights-hub-open'));
              })}
            >
              <Brain aria-hidden />
              <span>Insights Hub</span>
              <CommandShortcut>⌘I</CommandShortcut>
            </CommandItem>
            {onLayoutModeChange && Object.entries(LAYOUT_MODE_CONFIG).map(([id, cfg]) => (
              <CommandItem
                key={id}
                value={`layout mode ${cfg.label} ${id}`}
                className="command-palette__item"
                onSelect={() => run(() => onLayoutModeChange(id))}
              >
                <LayoutTemplate aria-hidden />
                <span>Layout: {cfg.label}</span>
              </CommandItem>
            ))}
            <CommandItem
              value="preferences settings appearance theme"
              className="command-palette__item"
              onSelect={() => run(() => onOpenSettings?.())}
            >
              <SlidersHorizontal aria-hidden />
              <span>Preferences</span>
              <CommandShortcut>⌘,</CommandShortcut>
            </CommandItem>
            <CommandItem
              value="system control admin"
              className="command-palette__item"
              onSelect={() => run(() => onOpenAdmin?.())}
            >
              <ShieldAlert aria-hidden />
              <span>System Control Panel</span>
            </CommandItem>
          </CommandGroup>
        </CommandList>

        <div className="command-palette__footer">
          <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
          <span><kbd>↵</kbd> select</span>
          <span><kbd>esc</kbd> close</span>
        </div>
      </Command>
    </CommandDialog>
  );
}
