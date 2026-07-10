/**
 * ResizableDock.jsx
 * Bottom docked panel with tabs:
 *   Positions | Orders | Balances | Algo Bot | Analyst | Bot History | Ticks | History | Equity Curve
 *
 * Features:
 *  - Drag-to-resize via top handle (persists to workspace settings)
 *  - History tab can be expanded to full-screen overlay
 *  - Badge counts on Positions and Orders tabs
 */
import React, { useState, useRef, useEffect, useCallback, useMemo, Suspense, lazy } from 'react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { useResearchStore } from '../store/useResearchStore';
import { useSettingsStore } from '../store/useSettingsStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { fetchBots, withLlmModel } from '../api/endpoints';
import { getStoreActions } from '../api/dispatch';
import { selectCashTotal } from '../store/selectors';
import {
  Briefcase, List, Landmark, Cpu, Activity, TrendingUp,
  Play, Settings, Trash2, XSquare, Maximize2, Minimize2, ShieldAlert, Pause, PlayCircle, OctagonX,
  RefreshCw, AlertTriangle, Zap, History, Brain, Radar, ChevronUp, Loader2,
} from 'lucide-react';
import TradeHistoryContent from './TradeHistoryPanel';
import BacktestResultsPanel from './BacktestResultsPanel';
import BacktestProgressBar from './BacktestProgressBar';
import ReconciliationTab from './ReconciliationTab';
import ErrorBoundary from './ErrorBoundary';
import StrategyTemplateCard from './StrategyTemplateCard';
import {
  DataTableRoot,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableHead,
  DataTableCell,
} from './DataTableShell';
import StrategyBadge from './StrategyBadge';
import { WidgetEmpty, ScrollTablePanel } from './WidgetShell';
import { useVirtualRows } from './VirtualTableBody';

const TickViewerTab = lazy(() => import('./TickViewerTab'));
const BotHistoryTab = lazy(() => import('./BotHistoryTab'));
const AnalystTab = lazy(() => import('./AnalystTab'));
const ScannerTab = lazy(() => import('./ScannerTab'));
const EquityCurveTab = lazy(() => import('./EquityCurveTab'));

function DockTabFallback() {
  return <WidgetEmpty message="Loading tab…" />;
}

function DetachedLazyPanel({ children }) {
  return <Suspense fallback={<DockTabFallback />}>{children}</Suspense>;
}
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Label } from '@/components/ui/label';
import {
  InputGroup, InputGroupAddon, InputGroupInput, InputGroupText,
} from '@/components/ui/input-group';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Separator } from '@/components/ui/separator';
import {
  scheduleBacktestClientTimeout,
  clearBacktestClientTimeout,
  formatBacktestTimeoutLabel,
} from '../lib/backtestTimeouts';
import { cn } from '@/lib/utils';
import { formatLastSignal } from '@/lib/formatTime';
import { BAR_TIMEFRAMES, deployTimeframeSummary, formatBarTimeframeLabel } from '@/lib/barTimeframes';
import { isLiveMassiveMode, isPaperExecutionMode } from '@/lib/massiveMarket';
import { backtestFingerprint } from '@/lib/backtestDisplay';
import { selectAgentInsight } from '@/lib/agentInsights';
import { isSignalLog, logLineClass } from '@/lib/botLogInsight';
import ChartAgentDeployPreview from './ChartAgentDeployPreview';
import { buildBotLookup, getPositionBots, shortBotId } from '@/lib/botAttribution';
import { DOCK_GROUP_CONFIG, dockGroupForTab } from '../settings/layoutModes';
import { selectPositionStats } from '../store/selectors';
import { useShallow } from 'zustand/react/shallow';

const DOCK_MIN = 200;
const DOCK_MAX = 560;
const DOCK_DEFAULT = 320;

const DOCK_TAB_IDS = new Set([
  'positions', 'orders', 'balances', 'algo', 'scanner', 'analyst',
  'reconcile', 'bots', 'ticks', 'history', 'equity',
]);

function normalizeDockTab(tab) {
  return DOCK_TAB_IDS.has(tab) ? tab : 'positions';
}


import { useDetachedPanels } from '../hooks/useDetachedPanels';
import DetachedPanelPortal from './dock/DetachedPanelPortal';
import { ExternalLink } from 'lucide-react';

import PositionsTab from './dock/PositionsPanel';
import OrdersTab from './dock/OrdersPanel';
import BalancesTab from './dock/BalancesPanel';
import { AlgoTab } from './dock/AlgoPanel';
export { AlgoTab };
import GlobalDeployDialog from './dock/GlobalDeployDialog';
// ── Main ResizableDock ────────────────────────────────────────────
export default function ResizableDock({ setDockHeight: setParentDockHeight, initialDockHeight }) {
  const posCount = useStore((s) => Object.keys(s.positions).length);
  const pendingOrders = useStore((s) => s.orders.filter((o) => o.status === 'PENDING' || o.status === 'OCO_ACTIVE').length);
  const tradeHistoryCount = useStore((s) => s.tradeHistory.length);
  const botHistoryCount = useStore((s) => s.botHistory.length);
  const ambiguousCount = useStore((s) => (
    s.isLive && !isPaperExecutionMode(s.terminalMode, s.executionMode) ? s.ambiguousOrders.length : 0
  ));
  const paperExecution = useStore((s) => isPaperExecutionMode(s.terminalMode, s.executionMode));
  const isBotRunning = useStore((s) => s.isBotRunning);
  const isLive = useStore((s) => s.isLive);
  const activeSymbol = useStore((s) => s.activeSymbol);
  const analystBadge = useResearchStore((s) => (s.agentInsightHistory[activeSymbol] ?? []).length || null);
  const workspaceTab = normalizeDockTab(
    useSettingsStore(state => state.settings.workspace?.dockActiveTab || 'positions'),
  );
  const workspaceGroup = useSettingsStore(state => state.settings.workspace?.dockGroup || 'portfolio');
  const layoutMode = useSettingsStore(state => state.settings.workspace?.layoutMode || 'trade');
  const dockCollapsed = useSettingsStore(state => state.settings.workspace?.dockCollapsed ?? false);
  const updateWorkspace = useSettingsStore(state => state.updateWorkspace);
  const { isDetached, detach, attach } = useDetachedPanels();

  const renderTabContent = (tabId, ContentComponent) => {
    if (isDetached(tabId)) {
      return (
        <div className="flex h-full flex-col items-center justify-center text-muted-foreground p-8">
          <ExternalLink className="mb-2 opacity-50" size={24} />
          <p className="text-sm font-medium">Panel is open in a new window</p>
          <Button variant="outline" size="sm" className="mt-4" onClick={() => attach(tabId)}>
            Reattach to dock
          </Button>
        </div>
      );
    }
    return <ContentComponent />;
  };

  const [activeTab, setActiveTab] = useState(workspaceTab);
  const [visitedTabs, setVisitedTabs] = useState(() => new Set([workspaceTab]));
  const [activeGroup, setActiveGroup] = useState(
    DOCK_GROUP_CONFIG[workspaceGroup] ? workspaceGroup : dockGroupForTab(workspaceTab),
  );
  const [dockH, setDockH] = useState(() => initialDockHeight || DOCK_DEFAULT);
  const [historyFullscreen, setHistoryFullscreen] = useState(false);
  const isDragging = useRef(false);
  const startY    = useRef(0);
  const startH    = useRef(0);
  const dockHRef  = useRef(dockH);

  useEffect(() => {
    dockHRef.current = dockH;
  }, [dockH]);

  useEffect(() => {
    setActiveTab(workspaceTab);
    setActiveGroup(dockGroupForTab(workspaceTab));
  }, [workspaceTab]);

  useEffect(() => {
    setVisitedTabs((prev) => {
      if (prev.has(activeTab)) return prev;
      const next = new Set(prev);
      next.add(activeTab);
      return next;
    });
  }, [activeTab]);

  const mountTab = useCallback((tabId) => visitedTabs.has(tabId), [visitedTabs]);

  const renderMountedTab = (tabId, ContentComponent, options = {}) => {
    const { suspense = false } = options;
    if (!mountTab(tabId)) return null;
    const body = renderTabContent(tabId, ContentComponent);
    if (suspense) {
      return <Suspense fallback={<DockTabFallback />}>{body}</Suspense>;
    }
    return body;
  };

  useEffect(() => {
    if (DOCK_GROUP_CONFIG[workspaceGroup]) {
      setActiveGroup(workspaceGroup);
    }
  }, [workspaceGroup]);

  // Sync dock height to parent App so CSS variable can update
  useEffect(() => {
    setParentDockHeight(dockH);
  }, [dockH, setParentDockHeight]);

  useEffect(() => {
    const onDockTab = (e) => {
      if (e.detail) {
        const tab = normalizeDockTab(e.detail);
        setActiveTab(tab);
        setActiveGroup(dockGroupForTab(tab));
        updateWorkspace({ dockActiveTab: tab, dockGroup: dockGroupForTab(tab), dockCollapsed: false });
      }
    };
    const onDockGroup = (e) => {
      if (e.detail && DOCK_GROUP_CONFIG[e.detail]) {
        setActiveGroup(e.detail);
        const firstTab = DOCK_GROUP_CONFIG[e.detail].tabs[0];
        setActiveTab(firstTab);
        updateWorkspace({ dockGroup: e.detail, dockActiveTab: firstTab, dockCollapsed: false });
      }
    };
    window.addEventListener('dock-tab', onDockTab);
    window.addEventListener('dock-group', onDockGroup);
    return () => {
      window.removeEventListener('dock-tab', onDockTab);
      window.removeEventListener('dock-group', onDockGroup);
    };
  }, [updateWorkspace]);

  const expandDock = useCallback(() => {
    updateWorkspace({ dockCollapsed: false });
  }, [updateWorkspace]);

  const handleTabChange = useCallback((tab) => {
    if (!tab) return;
    let next = normalizeDockTab(tab);
    if (paperExecution && next === 'reconcile') next = 'algo';
    const group = dockGroupForTab(next);
    setActiveTab(next);
    setActiveGroup(group);
    updateWorkspace({ dockActiveTab: next, dockGroup: group, dockCollapsed: false });
  }, [updateWorkspace, paperExecution]);

  useEffect(() => {
    if (paperExecution && activeTab === 'reconcile') {
      handleTabChange('algo');
    }
  }, [paperExecution, activeTab, handleTabChange]);

  const handleGroupChange = useCallback((group) => {
    if (!group || !DOCK_GROUP_CONFIG[group]) return;
    const firstTab = DOCK_GROUP_CONFIG[group].tabs.includes(activeTab)
      ? activeTab
      : DOCK_GROUP_CONFIG[group].tabs[0];
    setActiveGroup(group);
    setActiveTab(firstTab);
    updateWorkspace({ dockGroup: group, dockActiveTab: firstTab, dockCollapsed: false });
  }, [activeTab, updateWorkspace]);

  const onMouseDown = useCallback(e => {
    isDragging.current = true;
    startY.current = e.clientY;
    startH.current = dockH;
    document.body.style.cursor = 'ns-resize';
    document.body.style.userSelect = 'none';
  }, [dockH]);

  useEffect(() => {
    const onMove = e => {
      if (!isDragging.current) return;
      const delta = startY.current - e.clientY;
      const newH = Math.max(DOCK_MIN, Math.min(DOCK_MAX, startH.current + delta));
      setDockH(newH);
    };
    const onUp = () => {
      if (isDragging.current) {
        isDragging.current = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        try { updateWorkspace({ dockHeight: dockHRef.current }); } catch {}
      }
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
  }, [updateWorkspace]);

  const scanBadge = useResearchStore((s) => {
    const rows = s.scanResults?.rows ?? [];
    return rows.filter((r) => r.signal && r.signal !== 'NONE').length || null;
  });

  const TABS = useMemo(() => {
    const tabs = [
      { id: 'positions', label: 'Positions', icon: Briefcase, badge: posCount || null, group: 'portfolio' },
      { id: 'orders',    label: 'Orders',    icon: List,     badge: pendingOrders || null, group: 'portfolio' },
      { id: 'balances',  label: 'Balances',  icon: Landmark, group: 'portfolio' },
      { id: 'algo',      label: 'Algo Bot',  icon: Cpu,      group: 'automation' },
      { id: 'scanner',   label: 'Scanner',   icon: Radar,    badge: scanBadge, group: 'intelligence', hint: 'Quick peek — open Hub (⌘I) for full scanner workspace' },
      { id: 'analyst',   label: 'Analyst',   icon: Brain,    badge: analystBadge, group: 'intelligence', hint: 'Quick peek — open Hub (⌘I) for full analyst history' },
      { id: 'reconcile', label: 'Reconcile', icon: AlertTriangle, badge: ambiguousCount || null, group: 'automation' },
      { id: 'bots',      label: 'Bot History', icon: History, badge: botHistoryCount || null, group: 'automation' },
      { id: 'ticks',     label: 'Ticks',     icon: Zap,      group: 'data' },
      { id: 'history',   label: 'History',   icon: Activity, badge: tradeHistoryCount || null, group: 'data' },
      { id: 'equity',    label: 'Equity Curve', icon: TrendingUp, group: 'data' },
    ];
    return paperExecution ? tabs.filter((t) => t.id !== 'reconcile') : tabs;
  }, [posCount, pendingOrders, scanBadge, analystBadge, ambiguousCount, botHistoryCount, tradeHistoryCount, paperExecution]);

  const groupTabs = TABS.filter((t) => t.group === activeGroup);
  const groupBadge = (groupId) => {
    const tabs = TABS.filter((t) => t.group === groupId);
    return tabs.reduce((sum, t) => sum + (Number(t.badge) || 0), 0) || null;
  };

  if (dockCollapsed) {
    const collapsedTab = TABS.find((t) => t.id === activeTab) ?? TABS.find((t) => t.id === workspaceTab);
    const CollapsedIcon = collapsedTab?.icon ?? Briefcase;
    const groupLabel = DOCK_GROUP_CONFIG[activeGroup]?.label ?? 'Portfolio';
    const groupTotal = groupBadge(activeGroup);

    return (
      <>
        <GlobalDeployDialog switchToAlgoTab={() => handleTabChange('algo')} />

        {isDetached('positions') && (
          <DetachedPanelPortal title="Positions" onClose={() => attach('positions')}>
            <PositionsTab />
          </DetachedPanelPortal>
        )}
        {isDetached('orders') && (
          <DetachedPanelPortal title="Orders" onClose={() => attach('orders')}>
            <OrdersTab />
          </DetachedPanelPortal>
        )}
        {isDetached('balances') && (
          <DetachedPanelPortal title="Balances" onClose={() => attach('balances')}>
            <BalancesTab />
          </DetachedPanelPortal>
        )}
        {isDetached('algo') && (
          <DetachedPanelPortal title="Algo Bot" onClose={() => attach('algo')}>
            <AlgoTab />
          </DetachedPanelPortal>
        )}
        {isDetached('scanner') && (
          <DetachedPanelPortal title="Scanner" onClose={() => attach('scanner')}>
            <DetachedLazyPanel><ScannerTab /></DetachedLazyPanel>
          </DetachedPanelPortal>
        )}
        {isDetached('analyst') && (
          <DetachedPanelPortal title="Analyst" onClose={() => attach('analyst')}>
            <DetachedLazyPanel><AnalystTab /></DetachedLazyPanel>
          </DetachedPanelPortal>
        )}
        {isDetached('reconcile') && (
          <DetachedPanelPortal title="Reconcile" onClose={() => attach('reconcile')}>
            <ReconciliationTab />
          </DetachedPanelPortal>
        )}
        {isDetached('bots') && (
          <DetachedPanelPortal title="Bot History" onClose={() => attach('bots')}>
            <DetachedLazyPanel><BotHistoryTab /></DetachedLazyPanel>
          </DetachedPanelPortal>
        )}
        {isDetached('ticks') && (
          <DetachedPanelPortal title="Ticks" onClose={() => attach('ticks')}>
            <DetachedLazyPanel><TickViewerTab /></DetachedLazyPanel>
          </DetachedPanelPortal>
        )}
        {isDetached('history') && (
          <DetachedPanelPortal title="History" onClose={() => attach('history')}>
            <TradeHistoryContent />
          </DetachedPanelPortal>
        )}
        {isDetached('equity') && (
          <DetachedPanelPortal title="Equity Curve" onClose={() => attach('equity')}>
            <DetachedLazyPanel><EquityCurveTab /></DetachedLazyPanel>
          </DetachedPanelPortal>
        )}

        <div
        className="bottom-dock bottom-dock--collapsed dock-collapsed-rail"
        data-layout-mode={layoutMode}
        data-dock-group={activeGroup}
        style={{ gridArea: 'dock' }}
        role="button"
        tabIndex={0}
        aria-label={`Expand dock — ${groupLabel}, ${collapsedTab?.label ?? 'tab'}`}
        title="Expand dock"
        onClick={expandDock}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            expandDock();
          }
        }}
      >
        <div className="dock-collapsed-rail__grip" aria-hidden>
          <span className="dock-collapsed-rail__grip-bar" />
        </div>

        <div className="dock-collapsed-rail__inner">
          <div className="dock-collapsed-rail__heading">
            <span className="dock-collapsed-rail__eyebrow">{groupLabel}</span>
            <div className="dock-collapsed-rail__title-row">
              <ChevronUp className="dock-collapsed-rail__chevron" aria-hidden />
              <span className="dock-collapsed-rail__icon-wrap" aria-hidden>
                <CollapsedIcon className="dock-collapsed-rail__title-icon" />
              </span>
              <span className="dock-collapsed-rail__title">{collapsedTab?.label ?? 'Dock'}</span>
              {collapsedTab?.badge != null && (
                <Badge variant="secondary" className="dock-collapsed-rail__badge">
                  {collapsedTab.badge}
                </Badge>
              )}
              {groupTotal != null && collapsedTab?.badge == null && (
                <Badge variant="secondary" className="dock-collapsed-rail__badge">
                  {groupTotal}
                </Badge>
              )}
              {collapsedTab?.id === 'algo' && isBotRunning && (
                <span className="dock-collapsed-rail__pulse" aria-hidden />
              )}
            </div>
          </div>
        </div>
      </div>
      </>
    );
  }

  return (
    <>
      <GlobalDeployDialog switchToAlgoTab={() => handleTabChange('algo')} />

        {isDetached('positions') && (
          <DetachedPanelPortal title="Positions" onClose={() => attach('positions')}>
            <PositionsTab />
          </DetachedPanelPortal>
        )}
        {isDetached('orders') && (
          <DetachedPanelPortal title="Orders" onClose={() => attach('orders')}>
            <OrdersTab />
          </DetachedPanelPortal>
        )}
        {isDetached('balances') && (
          <DetachedPanelPortal title="Balances" onClose={() => attach('balances')}>
            <BalancesTab />
          </DetachedPanelPortal>
        )}
        {isDetached('algo') && (
          <DetachedPanelPortal title="Algo Bot" onClose={() => attach('algo')}>
            <AlgoTab />
          </DetachedPanelPortal>
        )}
        {isDetached('scanner') && (
          <DetachedPanelPortal title="Scanner" onClose={() => attach('scanner')}>
            <DetachedLazyPanel><ScannerTab /></DetachedLazyPanel>
          </DetachedPanelPortal>
        )}
        {isDetached('analyst') && (
          <DetachedPanelPortal title="Analyst" onClose={() => attach('analyst')}>
            <DetachedLazyPanel><AnalystTab /></DetachedLazyPanel>
          </DetachedPanelPortal>
        )}
        {isDetached('reconcile') && (
          <DetachedPanelPortal title="Reconcile" onClose={() => attach('reconcile')}>
            <ReconciliationTab />
          </DetachedPanelPortal>
        )}
        {isDetached('bots') && (
          <DetachedPanelPortal title="Bot History" onClose={() => attach('bots')}>
            <DetachedLazyPanel><BotHistoryTab /></DetachedLazyPanel>
          </DetachedPanelPortal>
        )}
        {isDetached('ticks') && (
          <DetachedPanelPortal title="Ticks" onClose={() => attach('ticks')}>
            <DetachedLazyPanel><TickViewerTab /></DetachedLazyPanel>
          </DetachedPanelPortal>
        )}
        {isDetached('history') && (
          <DetachedPanelPortal title="History" onClose={() => attach('history')}>
            <TradeHistoryContent />
          </DetachedPanelPortal>
        )}
        {isDetached('equity') && (
          <DetachedPanelPortal title="Equity Curve" onClose={() => attach('equity')}>
            <DetachedLazyPanel><EquityCurveTab /></DetachedLazyPanel>
          </DetachedPanelPortal>
        )}

      <div
        className="bottom-dock flex flex-col"
        data-tour="bottom-dock"
        data-layout-mode={layoutMode}
        data-dock-group={activeGroup}
        data-compact={dockH < 280 ? '' : undefined}
        style={{ gridArea: 'dock', height: dockH, minHeight: DOCK_MIN }}
        aria-label="Trading dock"
      >
        <div className="dock-resize-handle" onMouseDown={onMouseDown} />

        <Tabs value={activeTab} onValueChange={handleTabChange} className="dock-tabs-root gap-0">
          <div className="dock-tab-bar">
            <div className="dock-group-rail">
              <ToggleGroup
                type="single"
                value={activeGroup}
                onValueChange={(v) => { if (v) handleGroupChange(v); }}
                variant="default"
                size="sm"
                spacing={0}
                className="dock-group-toggle"
              >
                {Object.entries(DOCK_GROUP_CONFIG).map(([groupId, cfg]) => (
                  <ToggleGroupItem
                    key={groupId}
                    value={groupId}
                    data-group={groupId}
                    className="text-xs font-semibold"
                  >
                    {cfg.label}
                    {groupBadge(groupId) != null && (
                      <Badge variant="secondary" className="ml-1 h-4 min-w-4 px-1 text-xs">
                        {groupBadge(groupId)}
                      </Badge>
                    )}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </div>
            <Separator orientation="vertical" className="shrink-0" />
            <div className="dock-tab-bar-inner scroll-fade-x">
              <TabsList variant="line" className="dock-tab-switch scroll-panel-x no-scrollbar min-w-0 flex-1 justify-start rounded-none border-0 bg-transparent">
                {groupTabs.map(tab => {
                  const Icon = tab.icon;
                  return (
                    <TabsTrigger
                      key={tab.id}
                      value={tab.id}
                      className="dock-tab-trigger shrink-0 px-2 text-xs xl:px-3"
                      title={tab.hint || tab.label}
                    >
                      <Icon data-icon="inline-start" />
                      <span className="header-label">{tab.label}</span>
                      {tab.badge != null && (
                        <Badge variant="secondary" className="h-4 min-w-4 px-1 text-xs font-bold">
                          {tab.badge}
                        </Badge>
                      )}
                      {tab.id === 'algo' && isBotRunning && (
                        <span className="dock-algo-pulse" aria-hidden />
                      )}
                    </TabsTrigger>
                  );
                })}
              </TabsList>
            </div>
            <div className="dock-tab-actions">
            {activeGroup === 'portfolio' && (
              <Button
                variant="outline"
                size="sm"
                title="Open Portfolio Dashboard — equity, allocation, risk analytics"
                onClick={() => window.dispatchEvent(new CustomEvent('portfolio-dashboard-open'))}
              >
                Dashboard
              </Button>
            )}
            {activeGroup === 'intelligence' && (
              <Button
                variant="outline"
                size="sm"
                title="Open Insights Hub — resizable scanner + analyst workspace (⌘I)"
                onClick={() => window.dispatchEvent(new CustomEvent('insights-hub-open'))}
              >
                Hub
              </Button>
            )}
            {activeGroup === 'automation' && (
              <Button variant="outline" size="sm" onClick={() => window.dispatchEvent(new CustomEvent('automation-studio-open'))}>
                Studio
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              className="dock-collapse-btn"
              onClick={() => updateWorkspace({ dockCollapsed: true })}
              title="Collapse dock"
            >
              <Minimize2 aria-hidden />
              <span className="dock-collapse-btn__label">Collapse</span>
            </Button>
            {activeTab === 'history' && (
              <Button
                variant="ghost"
                size="icon-sm"
                className="shrink-0"
                onClick={() => setHistoryFullscreen(f => !f)}
                title={historyFullscreen ? 'Collapse' : 'Expand to fullscreen'}
              >
                {historyFullscreen ? <Minimize2 /> : <Maximize2 />}
              </Button>
            )}
            </div>
          </div>

          <div className="dock-tab-panels">
          <TabsContent value="positions" className="dock-tab-body dock-tab-body--cached mt-0 overflow-hidden">
            <ErrorBoundary name="Positions">
              {renderMountedTab('positions', PositionsTab)}
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="orders" className="dock-tab-body dock-tab-body--cached mt-0 overflow-hidden">
            <ErrorBoundary name="Orders">
              {renderMountedTab('orders', OrdersTab)}
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="balances" className="dock-tab-body dock-tab-body--cached mt-0 overflow-hidden">
            <ErrorBoundary name="Balances">
              {renderMountedTab('balances', BalancesTab)}
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="algo" className="dock-tab-body dock-tab-body--cached mt-0 overflow-hidden">
            <ErrorBoundary name="Algo Bot">
              {renderMountedTab('algo', AlgoTab)}
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="scanner" className="dock-tab-body dock-tab-body--cached mt-0 overflow-hidden">
            <ErrorBoundary name="Scanner">
              {renderMountedTab('scanner', ScannerTab, { suspense: true })}
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="analyst" className="dock-tab-body dock-tab-body--cached mt-0 overflow-hidden">
            <ErrorBoundary name="Chart Analyst">
              {renderMountedTab('analyst', AnalystTab, { suspense: true })}
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="reconcile" className="dock-tab-body dock-tab-body--cached mt-0 overflow-hidden">
            <ErrorBoundary name="Reconciliation">
              {renderMountedTab('reconcile', ReconciliationTab)}
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="bots" className="dock-tab-body dock-tab-body--cached mt-0 overflow-hidden">
            <ErrorBoundary name="Bot History">
              {renderMountedTab('bots', BotHistoryTab, { suspense: true })}
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="ticks" className="dock-tab-body dock-tab-body--cached mt-0 overflow-hidden">
            <ErrorBoundary name="Ticks">
              {renderMountedTab('ticks', TickViewerTab, { suspense: true })}
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="equity" className="dock-tab-body dock-tab-body--cached mt-0 overflow-hidden">
            <ErrorBoundary name="Equity curve">
              {renderMountedTab('equity', EquityCurveTab, { suspense: true })}
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="history" className="dock-tab-body dock-tab-body--cached mt-0 overflow-hidden">
            <ErrorBoundary name="Trade history">
              {mountTab('history') && !historyFullscreen && <TradeHistoryContent embedded />}
            </ErrorBoundary>
          </TabsContent>
          </div>
        </Tabs>
      </div>

      {/* Expanded history sheet */}
      <Sheet open={historyFullscreen && activeTab === 'history'} onOpenChange={setHistoryFullscreen}>
        <SheetContent
          side="bottom"
          showCloseButton={false}
          className="terminal-sheet terminal-sheet--bottom flex min-h-0 flex-col gap-0 overflow-hidden rounded-t-xl border-t p-0 sm:max-w-full"
        >
          <SheetHeader className="sr-only">
            <SheetTitle>Trade history</SheetTitle>
            <SheetDescription>Expanded trade history view</SheetDescription>
          </SheetHeader>
          <ErrorBoundary name="Trade history (expanded)">
            <TradeHistoryContent embedded={false} onClose={() => setHistoryFullscreen(false)} />
          </ErrorBoundary>
        </SheetContent>
      </Sheet>
    </>
  );
}
