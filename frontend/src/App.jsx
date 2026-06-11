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
          <TrendingUp size={20} className="logo-icon shrink-0" aria-hidden />
          <span className="brand-title">ANTIGRAVITY</span>

          {isLive ? (
            <Badge variant="live" className="icon-label px-2 py-0.5 text-[0.62rem] font-extrabold tracking-wider">
              <span className="size-1.5 animate-ping rounded-full bg-current" />
              <span>LIVE</span>
              <span className="header-live-detail">· {terminalMode}</span>
            </Badge>
          ) : (
            <Badge variant="secondary" className="header-mode-badge px-2 py-0.5 text-[0.62rem] font-bold tracking-wide">
              SIMULATED
            </Badge>
          )}

          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => setShowAdmin(true)}
                className="text-muted-foreground hover:text-trading-accent"
              >
                <Settings aria-hidden />
                <span className="sr-only">System Control & Admin Panel</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>System Control & Admin Panel</TooltipContent>
          </Tooltip>
        </div>

        <div className="header-controls">
          <Tabs value={viewMode} onValueChange={setViewMode}>
            <TabsList className="header-view-switch">
              <TabsTrigger value="single" className="header-view-tab flex-none shadow-none" title="Chart view (⌘1)">
                <BarChart2 className="header-view-icon" strokeWidth={2} aria-hidden />
                <span className="header-label">Chart</span>
              </TabsTrigger>
              <TabsTrigger value="multi" className="header-view-tab flex-none shadow-none" title="Multi-chart grid (⌘2)">
                <LayoutGrid className="header-view-icon" strokeWidth={2} aria-hidden />
                <span className="header-label">Multi-Chart</span>
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>

        <div className="header-actions">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-[var(--control-h)] px-2.5 text-xs text-muted-foreground"
                onClick={() => setPaletteOpen(true)}
                title="Symbol search (⌘K)"
              >
                <Search data-icon="inline-start" />
                <span className="header-label">Search</span>
                <kbd className="header-search-kbd pointer-events-none rounded border border-border bg-muted px-1 font-mono text-[0.6rem]">
                  ⌘K
                </kbd>
              </Button>
            </TooltipTrigger>
            <TooltipContent>Symbol search & quick actions (⌘K)</TooltipContent>
          </Tooltip>

          <Badge
            variant="outline"
            className="icon-label px-2 py-0.5 text-[0.75rem] font-semibold"
            title={connected ? (isLive ? 'Live broker connected' : 'Simulated feed connected') : 'WebSocket disconnected'}
          >
            <span
              className={cn(
                'size-1.5 shrink-0 rounded-full',
                connected
                  ? isLive
                    ? 'bg-trading-warn shadow-[0_0_6px_var(--color-crypto)]'
                    : 'bg-trading-up shadow-[0_0_6px_var(--color-up)]'
                  : 'bg-trading-down shadow-[0_0_6px_var(--color-down)]'
              )}
            />
            <span className="header-label">
              {connected ? (isLive ? 'Live Broker' : 'Simulated') : 'Disconnected'}
            </span>
          </Badge>
        </div>
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
