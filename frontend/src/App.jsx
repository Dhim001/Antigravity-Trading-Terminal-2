import React, { useState, useCallback, useEffect } from 'react';
import { useStore } from './store/useStore';
import { useWebSocket } from './hooks/useWebSocket';

import WatchlistWidget       from './components/WatchlistWidget';
import ChartWidget           from './components/ChartWidget';
import MultiChartGrid        from './components/MultiChartGrid';
import OrderBookWidget       from './components/OrderBookWidget';
import OrderEntryWidget      from './components/OrderEntryWidget';
import AlgoTraderEngine      from './components/AlgoTraderEngine';
import SystemControlPanel    from './components/SystemControlPanel';
import MarketOverviewStrip   from './components/MarketOverviewStrip';
import ResizableDock         from './components/ResizableDock';
import SymbolCommandPalette  from './components/SymbolCommandPalette';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { TrendingUp, LayoutGrid, BarChart2, Settings, Search } from 'lucide-react';

const DOCK_DEFAULT = 320;

export default function App() {
  const connectionStatus = useStore(state => state.connectionStatus);
  const viewMode         = useStore(state => state.viewMode);
  const setViewMode      = useStore(state => state.setViewMode);
  const isLive           = useStore(state => state.isLive);
  const terminalMode     = useStore(state => state.terminalMode);
  useWebSocket('ws://127.0.0.1:8765');

  const [showAdmin, setShowAdmin]   = useState(false);
  const [dockHeight, setDockHeight] = useState(DOCK_DEFAULT);
  const [paletteOpen, setPaletteOpen] = useState(false);

  const handleDockHeightChange = useCallback(h => setDockHeight(h), []);

  const connected = connectionStatus === 'connected';

  useEffect(() => {
    const onKeyDown = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setPaletteOpen(open => !open);
      }
      if ((e.metaKey || e.ctrlKey) && e.key === '1') {
        e.preventDefault();
        setViewMode('single');
      }
      if ((e.metaKey || e.ctrlKey) && e.key === '2') {
        e.preventDefault();
        setViewMode('multi');
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [setViewMode]);

  return (
    <div
      className="dashboard-container"
      style={{ '--dock-h': `${dockHeight}px` }}
    >
      <AlgoTraderEngine />
      <SystemControlPanel isOpen={showAdmin} onClose={() => setShowAdmin(false)} />
      <SymbolCommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onOpenAdmin={() => setShowAdmin(true)}
      />

      <header className="terminal-header">
        <div className="brand-section">
          <TrendingUp size={20} className="logo-icon" />
          <span className="brand-title">ANTIGRAVITY</span>

          {isLive ? (
            <Badge variant="live" className="gap-1.5 px-2 py-0.5 text-[0.62rem] font-extrabold tracking-wider">
              <span className="size-1.5 animate-ping rounded-full bg-current" />
              LIVE · {terminalMode}
            </Badge>
          ) : (
            <Badge variant="secondary" className="px-2 py-0.5 text-[0.62rem] font-bold tracking-wide">
              SIMULATED
            </Badge>
          )}

          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => setShowAdmin(true)}
                className="ml-1 text-muted-foreground hover:text-trading-accent"
              >
                <Settings data-icon="inline-start" />
                <span className="sr-only">System Control & Admin Panel</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>System Control & Admin Panel</TooltipContent>
          </Tooltip>
        </div>

        <Tabs value={viewMode} onValueChange={setViewMode}>
          <TabsList className="h-[var(--control-h)] border border-border bg-muted/40">
            <TabsTrigger value="single" className="gap-1">
              <BarChart2 data-icon="inline-start" />
              Chart
            </TabsTrigger>
            <TabsTrigger value="multi" className="gap-1">
              <LayoutGrid data-icon="inline-start" />
              Multi-Chart
            </TabsTrigger>
          </TabsList>
        </Tabs>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="h-[var(--control-h)] gap-1.5 px-2.5 text-xs text-muted-foreground"
              onClick={() => setPaletteOpen(true)}
            >
              <Search />
              <span className="hidden sm:inline">Search</span>
              <kbd className="pointer-events-none hidden rounded border border-border bg-muted px-1 font-mono text-[0.6rem] sm:inline">
                ⌘K
              </kbd>
            </Button>
          </TooltipTrigger>
          <TooltipContent>Symbol search & quick actions (⌘K)</TooltipContent>
        </Tooltip>

        <Badge variant="outline" className="gap-1.5 px-2 py-0.5 text-[0.75rem] font-semibold">
          <span
            className={cn(
              'size-1.5 rounded-full',
              connected
                ? isLive
                  ? 'bg-trading-warn shadow-[0_0_6px_var(--color-crypto)]'
                  : 'bg-trading-up shadow-[0_0_6px_var(--color-up)]'
                : 'bg-trading-down shadow-[0_0_6px_var(--color-down)]'
            )}
          />
          {connected ? (isLive ? 'Live Broker' : 'Simulated') : 'Disconnected'}
        </Badge>
      </header>

      <MarketOverviewStrip />

      <aside className="watchlist-sidebar">
        <WatchlistWidget />
      </aside>

      <main className="workspace-main">
        {viewMode === 'single'
          ? <ChartWidget />
          : <MultiChartGrid onSwitchToSingle={() => setViewMode('single')} />
        }
      </main>

      <section className="trading-panel">
        <OrderEntryWidget />
        <OrderBookWidget />
      </section>

      <ResizableDock setDockHeight={handleDockHeightChange} />
    </div>
  );
}
