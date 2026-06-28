import sys

def modify():
    path = 'frontend/src/components/ResizableDock.jsx'
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    render_helper = """
  const renderTabContent = (tabId, Component) => {
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
    return <Component hideToolbar={false} />;
  };
"""
    if 'renderTabContent' not in content:
        content = content.replace('  return (', render_helper + '\n  return (')

    content = content.replace('<PositionsTab />', '{renderTabContent(\'positions\', PositionsTab)}')
    content = content.replace('<OrdersTab />', '{renderTabContent(\'orders\', OrdersTab)}')
    content = content.replace('<BalancesTab />', '{renderTabContent(\'balances\', BalancesTab)}')
    content = content.replace('<AlgoTab />', '{renderTabContent(\'algo\', AlgoTab)}')
    content = content.replace('<ScannerTab />', '{renderTabContent(\'scanner\', ScannerTab)}')
    content = content.replace('<AnalystTab />', '{renderTabContent(\'analyst\', AnalystTab)}')
    content = content.replace('<ReconciliationTab />', '{renderTabContent(\'reconcile\', ReconciliationTab)}')
    content = content.replace('<BotHistoryTab />', '{renderTabContent(\'bots\', BotHistoryTab)}')
    content = content.replace('<TickViewerTab />', '{renderTabContent(\'ticks\', TickViewerTab)}')
    content = content.replace('<TradeHistoryContent />', '{renderTabContent(\'history\', TradeHistoryContent)}')
    content = content.replace('<EquityCurveTab />', '{renderTabContent(\'equity\', EquityCurveTab)}')

    portals = """
      {detachedTabs.includes('positions') && (
        <DetachedPanelPortal title="Positions" onClose={() => attach('positions')}>
          <PositionsTab hideToolbar={false} />
        </DetachedPanelPortal>
      )}
      {detachedTabs.includes('orders') && (
        <DetachedPanelPortal title="Orders" onClose={() => attach('orders')}>
          <OrdersTab hideToolbar={false} />
        </DetachedPanelPortal>
      )}
      {detachedTabs.includes('balances') && (
        <DetachedPanelPortal title="Balances" onClose={() => attach('balances')}>
          <BalancesTab hideToolbar={false} />
        </DetachedPanelPortal>
      )}
      {detachedTabs.includes('algo') && (
        <DetachedPanelPortal title="Algo Bot" onClose={() => attach('algo')}>
          <AlgoTab hideToolbar={false} />
        </DetachedPanelPortal>
      )}
      {detachedTabs.includes('scanner') && (
        <DetachedPanelPortal title="Scanner" onClose={() => attach('scanner')}>
          <ScannerTab hideToolbar={false} />
        </DetachedPanelPortal>
      )}
      {detachedTabs.includes('analyst') && (
        <DetachedPanelPortal title="Analyst" onClose={() => attach('analyst')}>
          <AnalystTab hideToolbar={false} />
        </DetachedPanelPortal>
      )}
      {detachedTabs.includes('reconcile') && (
        <DetachedPanelPortal title="Reconcile" onClose={() => attach('reconcile')}>
          <ReconciliationTab hideToolbar={false} />
        </DetachedPanelPortal>
      )}
      {detachedTabs.includes('bots') && (
        <DetachedPanelPortal title="Bot History" onClose={() => attach('bots')}>
          <BotHistoryTab hideToolbar={false} />
        </DetachedPanelPortal>
      )}
      {detachedTabs.includes('ticks') && (
        <DetachedPanelPortal title="Ticks" onClose={() => attach('ticks')}>
          <TickViewerTab hideToolbar={false} />
        </DetachedPanelPortal>
      )}
      {detachedTabs.includes('history') && (
        <DetachedPanelPortal title="History" onClose={() => attach('history')}>
          <TradeHistoryContent hideToolbar={false} />
        </DetachedPanelPortal>
      )}
      {detachedTabs.includes('equity') && (
        <DetachedPanelPortal title="Equity Curve" onClose={() => attach('equity')}>
          <EquityCurveTab hideToolbar={false} />
        </DetachedPanelPortal>
      )}
"""
    if 'DetachedPanelPortal title=' not in content:
        content = content.replace('    </Tabs>', portals + '    </Tabs>')

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == '__main__':
    modify()
