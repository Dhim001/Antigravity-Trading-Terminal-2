import React, { useState, useCallback, useEffect, useRef, Suspense, lazy } from 'react';
import { useStore } from './store/useStore';
import { useSettingsStore } from './store/useSettingsStore';
import { useWebSocket } from './hooks/useWebSocket';
import { useBootstrap } from './hooks/useBootstrap';

import ResizableWatchlistSidebar from './components/ResizableWatchlistSidebar';
import SettingsBootstrap     from './components/SettingsBootstrap';
import CommandBar            from './components/CommandBar';
import SymbolCommandPalette  from './components/SymbolCommandPalette';
import ShortcutsSheet        from './components/ShortcutsSheet';
import HelpSheet             from './components/HelpSheet';
import OnboardingTour        from './components/OnboardingTour';
import ErrorBoundary         from './components/ErrorBoundary';
import WorkspaceSwitcher     from './components/WorkspaceSwitcher';
import ActivityCenter        from './components/ActivityCenter';
import BacktestLabSheet      from './components/BacktestLabSheet';
import SignalInsightDrawer   from './components/SignalInsightDrawer';
import ChartContextStrip     from './components/ChartContextStrip';
import { useAlertMonitor } from './hooks/useAlertMonitor';
import { applyLayoutMode } from './settings/layoutModes';
import MemoryDevBadge from './components/MemoryDevBadge';
import PwaInstallBanner from './components/PwaInstallBanner';

const ChartWidget = lazy(() => import('./components/ChartWidget'));
const MultiChartGrid = lazy(() => import('./components/MultiChartGrid'));
const SystemControlPanel = lazy(() => import('./components/SystemControlPanel'));
const SettingsPanel = lazy(() => import('./components/SettingsPanel'));
const InsightsHub = lazy(() => import('./components/InsightsHub'));
const AutomationStudio = lazy(() => import('./components/AutomationStudio'));
const PortfolioDashboard = lazy(() => import('./components/PortfolioDashboard'));
const BotDetailDrawer = lazy(() => import('./components/BotDetailDrawer'));
const TradingPanel = lazy(() => import('./components/TradingPanel'));
const ResizableDock = lazy(() => import('./components/ResizableDock'));

function PanelFallback({ label = 'Loading…' }) {
  return (
    <div className="flex min-h-[120px] flex-1 items-center justify-center text-xs text-muted-foreground">
      {label}
    </div>
  );
}

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { brokerLabel } from '@/lib/operator';
import {
  TrendingUp, LayoutGrid, BarChart2, SlidersHorizontal, Search, OctagonX,
  CircleHelp, Bell, Activity, RefreshCw,
} from 'lucide-react';
import { refreshFrontend } from './lib/refreshFrontend';
import { sendAction } from './api/transport';
import { Action } from './api/protocol';
import { fetchHealth } from './api/endpoints';
import IbFeedStatusBanner from './components/IbFeedStatusBanner';
import MassiveFeedStatusBanner from './components/MassiveFeedStatusBanner';
import { getStoreActions } from './api/dispatch';
import { openBacktestLabResults } from './lib/backtestLab';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';

const DOCK_DEFAULT = 320;

export default function App() {
  const connectionStatus = useStore(state => state.connectionStatus);
  const apiStatus          = useStore(state => state.apiStatus);
  const viewMode         = useStore(state => state.viewMode);
  const setViewMode      = useStore(state => state.setViewMode);
  const isLive           = useStore(state => state.isLive);
  const terminalMode     = useStore(state => state.terminalMode);
  const isBotRunning     = useStore(state => state.isBotRunning);
  const selectedBotId    = useStore(state => state.selectedBotId);
  const botDrawerOpen    = useStore(state => state.botDrawerOpen);
  const setBotDrawerOpen = useStore(state => state.setBotDrawerOpen);
  const distributed      = useStore(state => state.distributed);
  const workerAlive      = useStore(state => state.workerAlive);
  const workerHeartbeatAge = useStore(state => state.workerHeartbeatAge);
  const workspace = useSettingsStore(state => state.settings.workspace);
  const updateWorkspace = useSettingsStore(state => state.updateWorkspace);
  const setSettingsOpen = useSettingsStore(state => state.setPanelOpen);
  const settingsOpen = useSettingsStore(state => state.panelOpen);
  useBootstrap();
  useWebSocket();

  const [showAdmin, setShowAdmin]   = useState(false);
  const [dockHeight, setDockHeight] = useState(() => workspace?.dockHeight || DOCK_DEFAULT);
  const [sidebarWidth, setSidebarWidth] = useState(() => workspace?.sidebarWidth || 320);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [insightsOpen, setInsightsOpen] = useState(false);
  const [automationOpen, setAutomationOpen] = useState(false);
  const [portfolioOpen, setPortfolioOpen] = useState(false);
  const [stopBotsOpen, setStopBotsOpen] = useState(false);
  const zenPrevLayoutRef = useRef(null);
  const workspaceHydrated = useRef(false);

  const layoutMode = workspace?.layoutMode || 'trade';
  const zenMode = workspace?.zenMode ?? false;
  const modeConfig = applyLayoutMode(layoutMode);
  const panelEnabled = !zenMode && modeConfig.rightPanel;
  const panelCollapsed = workspace?.rightPanelCollapsed ?? false;
  const showDock = !zenMode && modeConfig.dockVisible;
  const density = workspace?.density || 'compact';

  useAlertMonitor();

  const handleDockHeightChange = useCallback((h) => {
    setDockHeight(h);
    updateWorkspace({ dockHeight: h });
  }, [updateWorkspace]);
  const handleSidebarLayout = useCallback(({ width, collapsed }) => {
    setSidebarWidth((prev) => (prev === width ? prev : width));
    setSidebarCollapsed((prev) => (prev === collapsed ? prev : !!collapsed));
  }, []);

  const handleLayoutModeChange = useCallback((mode) => {
    const cfg = applyLayoutMode(mode);
    updateWorkspace({
      layoutMode: mode,
      dockActiveTab: cfg.dockTab,
      dockGroup: cfg.dockGroup,
      dockHeight: cfg.dockHeight,
      rightPanelCollapsed: !cfg.rightPanel,
      rightPanelTab: cfg.rightPanelTab,
      dockCollapsed: false,
      zenMode: false,
    });
    setDockHeight(cfg.dockHeight);
    window.dispatchEvent(new CustomEvent('dock-tab', { detail: cfg.dockTab }));
    window.dispatchEvent(new CustomEvent('dock-group', { detail: cfg.dockGroup }));
  }, [updateWorkspace]);

  const toggleZenMode = useCallback(() => {
    const ws = useSettingsStore.getState().settings.workspace;
    const currentZen = ws?.zenMode ?? false;

    if (currentZen) {
      const restore = zenPrevLayoutRef.current ?? {
        rightPanelCollapsed: false,
        dockCollapsed: false,
        dockHeight: ws?.dockHeight || dockHeight || DOCK_DEFAULT,
      };
      updateWorkspace({ zenMode: false, ...restore });
      setDockHeight(restore.dockHeight ?? DOCK_DEFAULT);
      zenPrevLayoutRef.current = null;
    } else {
      zenPrevLayoutRef.current = {
        rightPanelCollapsed: ws?.rightPanelCollapsed ?? false,
        dockCollapsed: ws?.dockCollapsed ?? false,
        dockHeight: ws?.dockHeight ?? dockHeight,
      };
      updateWorkspace({ zenMode: true, rightPanelCollapsed: true, dockCollapsed: true });
    }
  }, [updateWorkspace, dockHeight]);

  useEffect(() => {
    if (workspaceHydrated.current) return;
    workspaceHydrated.current = true;
    if (workspace?.viewMode && workspace.viewMode !== viewMode) {
      setViewMode(workspace.viewMode);
    }
  }, [workspace?.viewMode, viewMode, setViewMode]);

  useEffect(() => {
    updateWorkspace({ viewMode });
  }, [viewMode, updateWorkspace]);

  // Poll /health for worker liveness while running distributed (badge stays fresh
  // without dedicated WS worker frames).
  useEffect(() => {
    if (!distributed) return undefined;
    let cancelled = false;
    const poll = () => {
      if (cancelled) return;
      fetchHealth(getStoreActions()).catch(() => {});
    };
    poll();
    const id = setInterval(poll, 20000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [distributed]);

  useEffect(() => {
    const onWorkspaceLoaded = (e) => {
      const ws = e.detail?.workspace;
      if (ws?.dockHeight) setDockHeight(ws.dockHeight);
      if (ws?.sidebarWidth) setSidebarWidth(ws.sidebarWidth);
      if (ws?.viewMode) setViewMode(ws.viewMode);
      if (ws?.dockActiveTab) {
        window.dispatchEvent(new CustomEvent('dock-tab', { detail: ws.dockActiveTab }));
      }
      if (ws?.dockGroup) {
        window.dispatchEvent(new CustomEvent('dock-group', { detail: ws.dockGroup }));
      }
    };
    window.addEventListener('terminal:workspace-loaded', onWorkspaceLoaded);
    return () => window.removeEventListener('terminal:workspace-loaded', onWorkspaceLoaded);
  }, [setViewMode]);

  useEffect(() => {
    const onInsights = () => {
      requestAnimationFrame(() => setInsightsOpen(true));
    };
    const onAutomation = () => {
      requestAnimationFrame(() => setAutomationOpen(true));
    };
    const onPortfolio = () => {
      requestAnimationFrame(() => setPortfolioOpen(true));
    };
    const onSettings = (e) => setSettingsOpen(true, e.detail);
    window.addEventListener('insights-hub-open', onInsights);
    window.addEventListener('automation-studio-open', onAutomation);
    window.addEventListener('portfolio-dashboard-open', onPortfolio);
    window.addEventListener('open-settings', onSettings);
    const onChartZen = () => toggleZenMode();
    window.addEventListener('chart-zen-toggle', onChartZen);
    const onChartFocus = () => {
      setViewMode('single');
      updateWorkspace({ rightPanelCollapsed: true, dockCollapsed: true });
    };
    window.addEventListener('chart-focus', onChartFocus);
    return () => {
      window.removeEventListener('insights-hub-open', onInsights);
      window.removeEventListener('automation-studio-open', onAutomation);
      window.removeEventListener('portfolio-dashboard-open', onPortfolio);
      window.removeEventListener('open-settings', onSettings);
      window.removeEventListener('chart-zen-toggle', onChartZen);
      window.removeEventListener('chart-focus', onChartFocus);
    };
  }, [setSettingsOpen, toggleZenMode, setViewMode, updateWorkspace]);

  const connected = connectionStatus === 'connected';
  const apiReady = apiStatus === 'ready';
  const connectionTitle = connected
    ? (isLive ? 'Live broker connected' : 'Simulated feed connected')
    : apiReady
      ? 'WebSocket reconnecting — data loaded via REST'
      : apiStatus === 'loading'
        ? 'Loading snapshot via REST…'
        : 'Backend unreachable — retrying WebSocket';

  useEffect(() => {
    const onKeyDown = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === ',') {
        e.preventDefault();
        setSettingsOpen(true);
      }
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
      if ((e.metaKey || e.ctrlKey) && e.key === 'b') {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('dock-tab', { detail: 'algo' }));
        window.dispatchEvent(new CustomEvent('dock-group', { detail: 'automation' }));
      }
      if ((e.metaKey || e.ctrlKey) && e.key === 'i') {
        e.preventDefault();
        setInsightsOpen(true);
      }
      if ((e.metaKey || e.ctrlKey) && e.key === '[') {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('sidebar-toggle'));
      }
      if (e.key === 'f' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        const tag = document.activeElement?.tagName;
        if (tag !== 'INPUT' && tag !== 'TEXTAREA') {
          e.preventDefault();
          toggleZenMode();
        }
      }
      if (e.key === '?' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        const tag = document.activeElement?.tagName;
        if (tag !== 'INPUT' && tag !== 'TEXTAREA') {
          e.preventDefault();
          setShortcutsOpen(true);
        }
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [setViewMode, setSettingsOpen, toggleZenMode]);

  const effectiveDockH = showDock
    ? (workspace?.dockCollapsed ? 44 : dockHeight)
    : 0;

  return (
    <div
      className="dashboard-container"
      data-sidebar-user=""
      data-layout-mode={layoutMode}
      data-zen={zenMode ? '' : undefined}
      data-density={density}
      data-panel-collapsed={panelEnabled && panelCollapsed ? '' : !panelEnabled ? '' : undefined}
      data-sidebar-collapsed={sidebarCollapsed ? '' : undefined}
      data-dock-hidden={!showDock && !zenMode ? '' : undefined}
      style={{
        '--dock-h': `${effectiveDockH}px`,
        '--dock-min': showDock ? '200px' : '36px',
        '--sidebar-w': `${sidebarWidth}px`,
        '--panel-w': !panelEnabled ? '0px' : panelCollapsed ? '44px' : undefined,
      }}
    >
      <a href="#main-chart" className="skip-link">
        Skip to chart
      </a>
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {connectionTitle}
      </div>
      <SettingsBootstrap />
      <Suspense fallback={null}>
        <SystemControlPanel isOpen={showAdmin} onClose={() => setShowAdmin(false)} />
      </Suspense>
      <Suspense fallback={null}>
        <SettingsPanel
          open={settingsOpen}
          onOpenChange={setSettingsOpen}
          onOpenAdmin={() => setShowAdmin(true)}
        />
      </Suspense>
      <SymbolCommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onOpenAdmin={() => setShowAdmin(true)}
        onOpenSettings={() => setSettingsOpen(true)}
        onLayoutModeChange={handleLayoutModeChange}
      />
      <ShortcutsSheet open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
      <HelpSheet open={helpOpen} onOpenChange={setHelpOpen} />
      <OnboardingTour />
      <Suspense fallback={null}>
        <InsightsHub open={insightsOpen} onOpenChange={setInsightsOpen} />
      </Suspense>
      <Suspense fallback={null}>
        <AutomationStudio open={automationOpen} onOpenChange={setAutomationOpen} />
      </Suspense>
      <Suspense fallback={null}>
        <PortfolioDashboard open={portfolioOpen} onOpenChange={setPortfolioOpen} />
      </Suspense>
      <Suspense fallback={null}>
        <ErrorBoundary name="Bot detail">
          <BotDetailDrawer
            open={botDrawerOpen && !!selectedBotId}
            nested={automationOpen}
            onOpenChange={setBotDrawerOpen}
            onStop={(bot_id) => sendAction(Action.BOT_STOP, { bot_id })}
            onPause={(bot_id) => sendAction(Action.BOT_PAUSE, { bot_id })}
            onResume={(bot_id) => sendAction(Action.BOT_RESUME, { bot_id })}
          />
        </ErrorBoundary>
      </Suspense>
      <ActivityCenter open={activityOpen} onOpenChange={setActivityOpen} />
      <BacktestLabSheet />
      <SignalInsightDrawer />

      <ErrorBoundary name="Header">
      <header className="terminal-header">
        <div className="terminal-header__zone terminal-header__zone--brand">
          <div className="brand-section">
            <div className="brand-mark" aria-hidden>
              <TrendingUp size={18} className="logo-icon shrink-0" />
            </div>
            <div className="brand-copy">
              <span className="brand-title">ANTIGRAVITY</span>
              <span className="brand-subtitle">Trading Terminal</span>
            </div>

            {isLive ? (
              <Badge variant="live" className="header-mode-badge header-mode-badge--live icon-label px-2 py-0.5 text-[0.62rem] font-extrabold tracking-wider" title={`Live broker: ${brokerLabel(terminalMode)}`}>
                <span className="size-1.5 animate-ping rounded-full bg-current" />
                <span>LIVE</span>
                <span className="header-live-detail">· {brokerLabel(terminalMode)}</span>
              </Badge>
            ) : (
              <Badge variant="secondary" className="header-mode-badge px-2 py-0.5 text-[0.62rem] font-bold tracking-wide" title="Simulated market (no live broker)">
                {brokerLabel(terminalMode)}
              </Badge>
            )}
          </div>
        </div>

        <div className="terminal-header__zone terminal-header__zone--nav">
          <div className="header-controls">
            <div className="header-controls-inner">
            <WorkspaceSwitcher layoutMode={layoutMode} onLayoutModeChange={handleLayoutModeChange} />
            <span className="header-controls-label">View</span>
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
          </div>
        </div>

        <div className="terminal-header__zone terminal-header__zone--actions">
          <div className="header-actions">
            <div className="header-actions-cluster">
            {distributed && (
              <Badge
                variant="outline"
                className="header-distributed-badge icon-label hidden sm:inline-flex"
                title={
                  workerAlive == null
                    ? 'Distributed mode — worker status unknown'
                    : workerAlive
                      ? `Worker online${workerHeartbeatAge != null ? ` · ${workerHeartbeatAge}s ago` : ''}`
                      : 'Worker offline — no recent heartbeat'
                }
              >
                <span
                  className={cn(
                    'header-status-dot',
                    workerAlive == null
                      ? 'header-status-dot--rest'
                      : workerAlive
                        ? 'header-status-dot--live'
                        : 'header-status-dot--down',
                  )}
                  aria-hidden
                />
                <span className="header-label">
                  {workerAlive == null ? 'Distributed' : workerAlive ? 'Worker' : 'Worker down'}
                </span>
              </Badge>
            )}

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => openBacktestLabResults()}
                  className="header-icon-btn text-muted-foreground hover:text-trading-accent"
                  title="Backtest Lab — Results, Optimizer, Jobs"
                >
                  <Activity aria-hidden />
                  <span className="sr-only">Backtest Lab</span>
                </Button>
              </TooltipTrigger>
              <TooltipContent>Backtest Lab</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => setActivityOpen(true)}
                  className="header-icon-btn text-muted-foreground hover:text-trading-accent"
                  title="Activity center"
                >
                  <Bell aria-hidden />
                  <span className="sr-only">Activity</span>
                </Button>
              </TooltipTrigger>
              <TooltipContent>Activity & alerts</TooltipContent>
            </Tooltip>

            {isBotRunning && (
              <>
                <Button
                  variant="destructive"
                  size="sm"
                  className="header-stop-bots-btn"
                  onClick={() => setStopBotsOpen(true)}
                  title="Stop all running bots"
                >
                  <OctagonX data-icon="inline-start" />
                  <span className="header-label">Stop Bots</span>
                </Button>
                <AlertDialog open={stopBotsOpen} onOpenChange={setStopBotsOpen}>
                  <AlertDialogContent className="sm:max-w-md">
                    <AlertDialogHeader>
                      <AlertDialogTitle>Stop all bots?</AlertDialogTitle>
                      <AlertDialogDescription>
                        This halts every active bot immediately. Open positions are not closed — use
                        System Control → Emergency Stop to flatten positions and cancel orders.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction
                        className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        onClick={() => sendAction(Action.BOT_STOP_ALL, {})}
                      >
                        Stop all bots
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </>
            )}

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => refreshFrontend()}
                  className="header-icon-btn text-muted-foreground hover:text-trading-accent"
                  title="Refresh UI — reload app"
                >
                  <RefreshCw aria-hidden />
                  <span className="sr-only">Refresh UI</span>
                </Button>
              </TooltipTrigger>
              <TooltipContent>Refresh UI</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => setHelpOpen(true)}
                  className="header-icon-btn text-muted-foreground hover:text-trading-accent"
                  title="Help & glossary"
                >
                  <CircleHelp aria-hidden />
                  <span className="sr-only">Help</span>
                </Button>
              </TooltipTrigger>
              <TooltipContent>Help, workflows, glossary</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => setSettingsOpen(true)}
                  className="header-icon-btn text-muted-foreground hover:text-trading-accent"
                  title="Preferences (⌘,)"
                >
                  <SlidersHorizontal aria-hidden />
                  <span className="sr-only">Preferences</span>
                </Button>
              </TooltipTrigger>
              <TooltipContent>Preferences (⌘,)</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  className="header-search-btn text-muted-foreground"
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
              className="header-status-chip icon-label px-2 py-0.5 text-[0.75rem] font-semibold"
              title={connectionTitle}
            >
              <span
                className={cn(
                  'header-status-dot size-1.5 shrink-0 rounded-full',
                  connected
                    ? isLive
                      ? 'header-status-dot--live'
                      : 'header-status-dot--connected'
                    : apiReady
                      ? 'header-status-dot--rest'
                      : 'header-status-dot--down',
                )}
              />
              <span className="header-label">
                {connected
                  ? (isLive ? 'Live Broker' : 'Simulated')
                  : apiReady
                    ? 'REST'
                    : apiStatus === 'loading'
                      ? 'Loading…'
                      : 'Disconnected'}
              </span>
            </Badge>
            </div>
          </div>
        </div>
      </header>
      </ErrorBoundary>

      {!zenMode && modeConfig.showCommandBar && (
        <ErrorBoundary name="Command bar">
          <div className="command-stack">
            {terminalMode === 'LIVE_IB' && <IbFeedStatusBanner />}
            {terminalMode === 'LIVE_MASSIVE' && <MassiveFeedStatusBanner />}
            <CommandBar />
          </div>
        </ErrorBoundary>
      )}

      <ErrorBoundary name="Watchlist">
        <ResizableWatchlistSidebar onLayoutChange={handleSidebarLayout} />
      </ErrorBoundary>

      <main id="main-chart" tabIndex={-1} className="workspace-main workspace-main--with-context">
        {viewMode === 'single' ? (
          <ErrorBoundary name="Chart">
            <Suspense fallback={<PanelFallback label="Loading chart…" />}>
              <ChartWidget />
            </Suspense>
          </ErrorBoundary>
        ) : (
          <ErrorBoundary name="Multi-chart grid">
            <Suspense fallback={<PanelFallback label="Loading charts…" />}>
              <MultiChartGrid onSwitchToSingle={() => setViewMode('single')} />
            </Suspense>
          </ErrorBoundary>
        )}
        {!zenMode && <ChartContextStrip />}
      </main>

      <Suspense fallback={null}>
        <TradingPanel hidden={!panelEnabled} />
      </Suspense>

      {showDock && (
        <ErrorBoundary name="Trading dock">
          <Suspense fallback={<PanelFallback label="Loading dock…" />}>
            <ResizableDock setDockHeight={handleDockHeightChange} initialDockHeight={dockHeight} />
          </Suspense>
        </ErrorBoundary>
      )}
      <MemoryDevBadge />
      <PwaInstallBanner />
    </div>
  );
}
