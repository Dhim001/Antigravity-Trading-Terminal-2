import React, { useCallback, useEffect, useState, Suspense } from 'react';
import { Actions, Layout, Model } from 'flexlayout-react';
import 'flexlayout-react/style/dark.css';

import { lazyImport, prefetchDockPanels, prefetchLazyImport } from '../lib/lazyImport';
import { useDetachedPanels } from '../hooks/useDetachedPanels';
import {
  getStandalonePanelDef,
  subscribeStandaloneEvents,
} from '../lib/standalonePanels';
import { focusDetachedPanelForTab } from '../lib/workspaceNav';
import {
  focusFlexLayoutComponent,
  focusFlexLayoutDockGroup,
  toggleFlexLayoutComponent,
} from '../lib/flexlayoutFocus';
import {
  listSelectedComponents,
  loadSelectedComponents,
  makePersistOnModelChange,
  persistOnSelectAction,
  restoreFlexLayoutSelection,
} from '../lib/flexlayoutPersist';
import { useSettingsStore } from '../store/useSettingsStore';
import ChartContextStrip from './ChartContextStrip';
import MountWhenVisible from './MountWhenVisible';
import MlTrainingFlexPanel from './dock/MlTrainingFlexPanel';
import AlgoFlexPanel from './dock/AlgoFlexPanel';
import CopilotFlexPanel from './dock/CopilotFlexPanel';
import InsightsFlexPanel from './dock/InsightsFlexPanel';
// Light portfolio tabs — eager so the first dock click is not a Suspense blank.
import PositionsTab from './dock/PositionsPanel';
import OrdersTab from './dock/OrdersPanel';
import BalancesTab from './dock/BalancesPanel';

// Lazy load actual inner panels instead of Resizable wrappers
const WatchlistSidebar = lazyImport(() => import('./WatchlistWidget'), 'watchlist');
const MarketMoversSidebar = lazyImport(() => import('./MarketMoversWidget'), 'movers');
const ChartWidget = lazyImport(() => import('./ChartWidget'), 'chart');
const MultiChartGrid = lazyImport(() => import('./MultiChartGrid'), 'multi-chart');

// Trading Panel inner widgets
const OrderEntryWidget = lazyImport(() => import('./OrderEntryWidget'), 'order-entry');
const OrderBookWidget = lazyImport(() => import('./OrderBookWidget'), 'order-book');
const DepthChartWidget = lazyImport(() => import('./DepthChartWidget'), 'depth-chart');
const FootprintPanel = lazyImport(() => import('./chart/FootprintPanel'), 'footprint');

// Automation and Data inner panels
const ReconciliationTab = lazyImport(() => import('./ReconciliationTab'), 'reconcile');
const BotHistoryTab = lazyImport(() => import('./BotHistoryTab'), 'bots');
const TickViewerTab = lazyImport(() => import('./TickViewerTab'), 'ticks');
const TradeHistoryPanel = lazyImport(() => import('./TradeHistoryPanel').then(m => ({ default: m.TradeHistoryContent })), 'history');
const EquityCurveTab = lazyImport(() => import('./EquityCurveTab'), 'equity');

/** Heavy tabs — unmount when deselected to reclaim ECharts / pollers / large trees. */
const UNMOUNT_WHEN_HIDDEN = new Set([
  'algo',
  'ml-training',
  'scanner',
  'analyst',
  'equity',
  'ticks',
  'copilot',
  'footprint',
]);

function PanelFallback({ label = 'Loading…' }) {
  return (
    <div className="flex min-h-[120px] flex-1 items-center justify-center text-xs text-muted-foreground">
      {label}
    </div>
  );
}

function wrapPanel(node, component, element) {
  if (!UNMOUNT_WHEN_HIDDEN.has(component)) return element;
  return (
    <MountWhenVisible
      node={node}
      placeholderLabel={`Loading ${component}…`}
      keepAliveMs={60_000}
    >
      {element}
    </MountWhenVisible>
  );
}


const DEFAULT_LAYOUT = {
  global: {
    tabEnableClose: true,
    tabSetHeaderHeight: 26,
    tabSetTabStripHeight: 26,
    enableEdgeDock: true,
    splitterSize: 6,
  },
  layout: {
    type: 'row',
    weight: 100,
    children: [
      {
        type: 'tabset',
        weight: 15,
        children: [
          { type: 'tab', name: 'Watchlist', component: 'watchlist', enableClose: false },
          { type: 'tab', name: 'Movers', component: 'movers', enableClose: false },
        ],
      },
      {
        type: 'row',
        weight: 65,
        children: [
          {
            type: 'tabset',
            weight: 70,
            children: [{ type: 'tab', name: 'Chart', component: 'chart', enableClose: false }],
          },
          {
            type: 'row',
            weight: 30,
            children: [
              {
                type: 'tabset',
                weight: 50,
                children: [
                  { type: 'tab', name: 'Positions', component: 'positions' },
                  { type: 'tab', name: 'Orders', component: 'orders' },
                  { type: 'tab', name: 'History', component: 'history' },
                  { type: 'tab', name: 'Balances', component: 'balances' },
                  { type: 'tab', name: 'Bot History', component: 'bots' },
                  { type: 'tab', name: 'Reconcile', component: 'reconcile' },
                ],
              },
              {
                type: 'tabset',
                weight: 50,
                children: [
                  { type: 'tab', name: 'Scanner', component: 'scanner' },
                  { type: 'tab', name: 'Analyst', component: 'analyst' },
                  { type: 'tab', name: 'Copilot', component: 'copilot' },
                  { type: 'tab', name: 'ML Training', component: 'ml-training' },
                  { type: 'tab', name: 'Algo', component: 'algo' },
                  { type: 'tab', name: 'Ticks', component: 'ticks' },
                  { type: 'tab', name: 'Equity', component: 'equity' },
                ],
              },
            ],
          },
        ],
      },
      {
        type: 'tabset',
        weight: 20,
        children: [
          { type: 'tab', name: 'Trade', component: 'order-entry', enableClose: false },
          { type: 'tab', name: 'Book', component: 'order-book', enableClose: false },
          { type: 'tab', name: 'Depth', component: 'depth-chart', enableClose: false },
          { type: 'tab', name: 'Footprint', component: 'footprint', enableClose: false },
        ],
      },
    ],
  },
};

/** Map legacy TradingPanel rightPanelTab ids → FlexLayout component ids. */
const RIGHT_PANEL_TAB_MAP = {
  trade: 'order-entry',
  book: 'order-book',
  depth: 'depth-chart',
  footprint: 'footprint',
};

export default function WorkspaceGrid({ viewMode }) {
  const { attach } = useDetachedPanels();

  const [model] = useState(() => {
    const m = Model.fromJson(DEFAULT_LAYOUT);
    // Restore before first paint so header Refresh UI does not flash Scanner.
    restoreFlexLayoutSelection(m, focusFlexLayoutComponent);
    // Cold start / empty session: honor saved Watchlist vs Movers preference.
    const saved = loadSelectedComponents();
    const hasLeft = saved.includes('watchlist') || saved.includes('movers');
    if (!hasLeft) {
      const pref = useSettingsStore.getState().settings?.workspace?.leftPanelTab;
      if (pref === 'movers' || pref === 'watchlist') {
        focusFlexLayoutComponent(m, pref);
      }
    }
    return m;
  });

  useEffect(() => {
    const onDockTab = (e) => {
      const panelId = typeof e.detail === 'string' ? e.detail : e.detail?.tab;
      if (!panelId) return;
      const mapped = RIGHT_PANEL_TAB_MAP[panelId] || panelId;
      focusFlexLayoutComponent(model, mapped);
    };
    const onDockGroup = (e) => {
      const group = typeof e.detail === 'string' ? e.detail : e.detail?.group;
      if (!group) return;
      focusFlexLayoutDockGroup(model, group);
    };
    const onSidebarExpand = () => {
      const pref = useSettingsStore.getState().settings?.workspace?.leftPanelTab;
      focusFlexLayoutComponent(model, pref === 'movers' ? 'movers' : 'watchlist');
    };
    const onSidebarToggle = () => {
      const pref = useSettingsStore.getState().settings?.workspace?.leftPanelTab;
      const left = pref === 'movers' ? 'movers' : 'watchlist';
      toggleFlexLayoutComponent(model, left, 'chart');
    };
    const onTradingExpand = () => {
      focusFlexLayoutComponent(model, 'order-entry');
    };
    const onLeftPanelPref = (e) => {
      const next = typeof e.detail === 'string' ? e.detail : e.detail?.tab;
      if (next !== 'watchlist' && next !== 'movers') return;
      focusFlexLayoutComponent(model, next);
    };

    window.addEventListener('dock-tab', onDockTab);
    window.addEventListener('dock-group', onDockGroup);
    window.addEventListener('sidebar-expand', onSidebarExpand);
    window.addEventListener('sidebar-toggle', onSidebarToggle);
    window.addEventListener('trading-panel-expand', onTradingExpand);
    window.addEventListener('left-panel-tab', onLeftPanelPref);
    return () => {
      window.removeEventListener('dock-tab', onDockTab);
      window.removeEventListener('dock-group', onDockGroup);
      window.removeEventListener('sidebar-expand', onSidebarExpand);
      window.removeEventListener('sidebar-toggle', onSidebarToggle);
      window.removeEventListener('trading-panel-expand', onTradingExpand);
      window.removeEventListener('left-panel-tab', onLeftPanelPref);
    };
  }, [model]);

  // Standalone panel windows closed / reattached → restore dock tabs.
  useEffect(() => {
    return subscribeStandaloneEvents(undefined, (msg) => {
      if (msg?.type !== 'closed' && msg?.type !== 'reattach') return;
      const def = getStandalonePanelDef(msg.panelId);
      for (const t of def?.dockTabs || []) attach(t);
    });
  }, [attach]);

  // Warm dock / trading panel chunks so first tab click is not a blank Suspense.
  useEffect(() => {
    prefetchDockPanels();
  }, []);

  const onAction = useCallback((action) => {
    const result = persistOnSelectAction(action, model);
    try {
      if (action?.type === Actions.SELECT_TAB) {
        // Prefetch as soon as the tab is selected (covers keyboard / dock-tab events).
        try {
          const tabId = action.data?.tabNode;
          const tabNode = tabId ? model.getNodeById(tabId) : null;
          const comp = tabNode?.getComponent?.();
          if (comp) {
            prefetchLazyImport(comp);
            // Clicking a detached tab should raise its standalone window, not
            // only show the in-dock placeholder.
            focusDetachedPanelForTab(comp);
          }
        } catch {
          /* ignore */
        }
        queueMicrotask(() => {
          const selected = listSelectedComponents(model);
          const left = selected.find((c) => c === 'movers' || c === 'watchlist');
          if (!left) return;
          const cur = useSettingsStore.getState().settings?.workspace?.leftPanelTab;
          if (cur !== left) {
            useSettingsStore.getState().updateWorkspace({ leftPanelTab: left });
          }
        });
      }
    } catch {
      /* ignore preference persist errors */
    }
    return result;
  }, [model]);

  const onRenderTab = useCallback((node, renderValues) => {
    const component = node?.getComponent?.();
    if (!component) return;
    const content = renderValues.content;
    renderValues.content = (
      <span
        className="inline-flex max-w-full items-center"
        onMouseEnter={() => prefetchLazyImport(component)}
        onFocus={() => prefetchLazyImport(component)}
      >
        {content}
      </span>
    );
  }, []);

  const factory = useCallback((node) => {
    const component = node.getComponent();
    let panel;
    switch (component) {
      case 'watchlist':
        panel = <Suspense fallback={<PanelFallback />}><WatchlistSidebar /></Suspense>;
        break;
      case 'movers':
        panel = <Suspense fallback={<PanelFallback />}><MarketMoversSidebar /></Suspense>;
        break;
      case 'chart':
        panel = (
          <section className="flex flex-col h-full w-full relative">
            <ChartContextStrip />
            <Suspense fallback={<PanelFallback label="Loading chart..." />}>
              {viewMode === 'multi' ? <MultiChartGrid /> : <ChartWidget id="main-chart" />}
            </Suspense>
          </section>
        );
        break;
      case 'order-entry':
        panel = <Suspense fallback={<PanelFallback />}><OrderEntryWidget /></Suspense>;
        break;
      case 'order-book':
        panel = <Suspense fallback={<PanelFallback />}><OrderBookWidget /></Suspense>;
        break;
      case 'depth-chart':
        panel = <Suspense fallback={<PanelFallback />}><DepthChartWidget /></Suspense>;
        break;
      case 'footprint':
        panel = (
          <Suspense fallback={<PanelFallback label="Loading footprint…" />}>
            <FootprintPanel />
          </Suspense>
        );
        break;
      case 'positions':
        panel = <PositionsTab />;
        break;
      case 'orders':
        panel = <OrdersTab />;
        break;
      case 'balances':
        panel = <BalancesTab />;
        break;
      case 'algo':
        panel = <AlgoFlexPanel />;
        break;
      case 'scanner':
        panel = <InsightsFlexPanel tab="scanner" />;
        break;
      case 'analyst':
        panel = <InsightsFlexPanel tab="analyst" />;
        break;
      case 'copilot':
        panel = <CopilotFlexPanel />;
        break;
      case 'ml-training':
        panel = <MlTrainingFlexPanel />;
        break;
      case 'reconcile':
        panel = <Suspense fallback={<PanelFallback />}><ReconciliationTab /></Suspense>;
        break;
      case 'bots':
        panel = <Suspense fallback={<PanelFallback />}><BotHistoryTab /></Suspense>;
        break;
      case 'ticks':
        panel = <Suspense fallback={<PanelFallback />}><TickViewerTab /></Suspense>;
        break;
      case 'history':
        panel = <Suspense fallback={<PanelFallback />}><TradeHistoryPanel embedded /></Suspense>;
        break;
      case 'equity':
        panel = <Suspense fallback={<PanelFallback />}><EquityCurveTab /></Suspense>;
        break;
      default:
        return <div className="p-4 text-muted-foreground text-sm">Unknown Component</div>;
    }
    return wrapPanel(node, component, panel);
  }, [viewMode]);

  return (
    <div className="flex-1 relative w-full h-full bg-background overflow-hidden" style={{ minHeight: '500px' }}>
      <Layout
        model={model}
        factory={factory}
        onModelChange={makePersistOnModelChange(model)}
        onAction={onAction}
        onRenderTab={onRenderTab}
      />
    </div>
  );
}
