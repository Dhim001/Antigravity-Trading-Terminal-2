import React, { useState, Suspense } from 'react';
import { Layout, Model } from 'flexlayout-react';
import 'flexlayout-react/style/dark.css';

import { lazyImport } from '../lib/lazyImport';
import ChartContextStrip from './ChartContextStrip';

// Lazy load actual inner panels instead of Resizable wrappers
const WatchlistSidebar = lazyImport(() => import('./WatchlistWidget'), 'watchlist');
const ChartWidget = lazyImport(() => import('./ChartWidget'), 'chart');
const MultiChartGrid = lazyImport(() => import('./MultiChartGrid'), 'multi-chart');

// Trading Panel inner widgets
const OrderEntryWidget = lazyImport(() => import('./OrderEntryWidget'), 'order-entry');
const OrderBookWidget = lazyImport(() => import('./OrderBookWidget'), 'order-book');
const DepthChartWidget = lazyImport(() => import('./DepthChartWidget'), 'depth-chart');

// Dock inner panels
const PositionsTab = lazyImport(() => import('./dock/PositionsPanel'), 'positions');
const OrdersTab = lazyImport(() => import('./dock/OrdersPanel'), 'orders');
const BalancesTab = lazyImport(() => import('./dock/BalancesPanel'), 'balances');
const AlgoTab = lazyImport(() => import('./dock/AlgoPanel').then(m => ({ default: m.AlgoTab })), 'algo');

// Intelligence inner panels
const ScannerTab = lazyImport(() => import('./ScannerTab'), 'scanner');
const AnalystTab = lazyImport(() => import('./AnalystTab'), 'analyst');
const CopilotTab = lazyImport(() => import('./dock/CopilotTab'), 'copilot');

// Automation and Data inner panels
const ReconciliationTab = lazyImport(() => import('./ReconciliationTab'), 'reconcile');
const BotHistoryTab = lazyImport(() => import('./BotHistoryTab'), 'bots');
const TickViewerTab = lazyImport(() => import('./TickViewerTab'), 'ticks');
const TradeHistoryPanel = lazyImport(() => import('./TradeHistoryPanel').then(m => ({ default: m.TradeHistoryContent })), 'history');
const EquityCurveTab = lazyImport(() => import('./EquityCurveTab'), 'equity');

function PanelFallback({ label = 'Loading…' }) {
  return (
    <div className="flex min-h-[120px] flex-1 items-center justify-center text-xs text-muted-foreground">
      {label}
    </div>
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
    type: "row",
    weight: 100,
    children: [
      {
        type: "tabset",
        weight: 15,
        children: [{ type: "tab", name: "Watchlist", component: "watchlist", enableClose: false }]
      },
      {
        type: "row",
        weight: 65,
        children: [
          {
            type: "tabset",
            weight: 70,
            children: [{ type: "tab", name: "Chart", component: "chart", enableClose: false }]
          },
          {
            type: "row",
            weight: 30,
            children: [
              {
                type: "tabset",
                weight: 50,
                children: [
                  { type: "tab", name: "Positions", component: "positions" },
                  { type: "tab", name: "Orders", component: "orders" },
                  { type: "tab", name: "History", component: "history" },
                  { type: "tab", name: "Balances", component: "balances" },
                  { type: "tab", name: "Bot History", component: "bots" },
                  { type: "tab", name: "Reconcile", component: "reconcile" }
                ]
              },
              {
                type: "tabset",
                weight: 50,
                children: [
                  { type: "tab", name: "Scanner", component: "scanner" },
                  { type: "tab", name: "Analyst", component: "analyst" },
                  { type: "tab", name: "Copilot", component: "copilot" },
                  { type: "tab", name: "Algo", component: "algo" },
                  { type: "tab", name: "Ticks", component: "ticks" },
                  { type: "tab", name: "Equity", component: "equity" }
                ]
              }
            ]
          }
        ]
      },
      {
        type: "tabset",
        weight: 20,
        children: [
          { type: "tab", name: "Trade", component: "order-entry", enableClose: false },
          { type: "tab", name: "Book", component: "order-book", enableClose: false },
          { type: "tab", name: "Depth", component: "depth-chart", enableClose: false }
        ]
      }
    ]
  }
};

export default function WorkspaceGrid({ viewMode }) {
  const [model] = useState(() => Model.fromJson(DEFAULT_LAYOUT));

  const factory = (node) => {
    const component = node.getComponent();
    switch (component) {
      case "watchlist":
        return <Suspense fallback={<PanelFallback />}><WatchlistSidebar /></Suspense>;
      case "chart":
        return (
          <section className="flex flex-col h-full w-full relative">
            <ChartContextStrip />
            <Suspense fallback={<PanelFallback label="Loading chart..." />}>
              {viewMode === 'multi' ? <MultiChartGrid /> : <ChartWidget id="main-chart" />}
            </Suspense>
          </section>
        );
      case "order-entry":
        return <Suspense fallback={<PanelFallback />}><OrderEntryWidget /></Suspense>;
      case "order-book":
        return <Suspense fallback={<PanelFallback />}><OrderBookWidget /></Suspense>;
      case "depth-chart":
        return <Suspense fallback={<PanelFallback />}><DepthChartWidget /></Suspense>;
      case "positions":
        return <Suspense fallback={<PanelFallback />}><PositionsTab /></Suspense>;
      case "orders":
        return <Suspense fallback={<PanelFallback />}><OrdersTab /></Suspense>;
      case "balances":
        return <Suspense fallback={<PanelFallback />}><BalancesTab /></Suspense>;
      case "algo":
        return <Suspense fallback={<PanelFallback />}><AlgoTab /></Suspense>;
      case "scanner":
        return <Suspense fallback={<PanelFallback />}><ScannerTab /></Suspense>;
      case "analyst":
        return <Suspense fallback={<PanelFallback />}><AnalystTab /></Suspense>;
      case "copilot":
        return <Suspense fallback={<PanelFallback />}><CopilotTab /></Suspense>;
      case "reconcile":
        return <Suspense fallback={<PanelFallback />}><ReconciliationTab /></Suspense>;
      case "bots":
        return <Suspense fallback={<PanelFallback />}><BotHistoryTab /></Suspense>;
      case "ticks":
        return <Suspense fallback={<PanelFallback />}><TickViewerTab /></Suspense>;
      case "history":
        return <Suspense fallback={<PanelFallback />}><TradeHistoryPanel embedded /></Suspense>;
      case "equity":
        return <Suspense fallback={<PanelFallback />}><EquityCurveTab /></Suspense>;
      default:
        return <div className="p-4 text-muted-foreground text-sm">Unknown Component</div>;
    }
  };

  return (
    <div className="flex-1 relative w-full h-full bg-background overflow-hidden" style={{ minHeight: '500px' }}>
      <Layout model={model} factory={factory} />
    </div>
  );
}
