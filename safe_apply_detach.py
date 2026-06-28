import re

path = 'frontend/src/components/ResizableDock.jsx'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Add imports
imports = """import { useDetachedPanels } from '../hooks/useDetachedPanels';
import DetachedPanelPortal from './dock/DetachedPanelPortal';
import { ExternalLink } from 'lucide-react';
"""

if "import { useDetachedPanels }" not in content:
    # insert before "import PositionsTab"
    content = content.replace("import PositionsTab", imports + "\nimport PositionsTab")

# Add hook inside component
hook = """  const updateWorkspace = useSettingsStore(state => state.updateWorkspace);
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
"""

content = content.replace("  const updateWorkspace = useSettingsStore(state => state.updateWorkspace);", hook)

# Now, we must replace each <PositionsTab /> with {renderTabContent('positions', PositionsTab)}
# Let's list the components and their tab ids:
tabs = [
    ('positions', 'PositionsTab'),
    ('orders', 'OrdersTab'),
    ('balances', 'BalancesTab'),
    ('algo', 'AlgoTab'),
    ('scanner', 'ScannerTab'),
    ('analyst', 'AnalystTab'),
    ('reconcile', 'ReconciliationTab'),
    ('bots', 'BotHistoryTab'),
    ('ticks', 'TickViewerTab'),
    ('history', 'TradeHistoryContent'),
    ('equity', 'EquityCurveTab')
]

for tabId, comp in tabs:
    # We must find `<Comp />` and replace with `{renderTabContent('id', Comp)}`
    # Also handle `<Comp />` inside ErrorBoundary if any
    content = re.sub(rf'<{comp}\s*/>', rf'{{renderTabContent("{tabId}", {comp})}}', content)


# Now add the Portals rendering before the final `return (` of the `if (dockCollapsed)` block or at the end of the file.
# Actually, it's safer to put them right inside `return (` of `ResizableDock` main return, before the `<GlobalDeployDialog />`.
# Or just inside the empty fragment `<>`
portals = """
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
            <ScannerTab />
          </DetachedPanelPortal>
        )}
        {isDetached('analyst') && (
          <DetachedPanelPortal title="Analyst" onClose={() => attach('analyst')}>
            <AnalystTab />
          </DetachedPanelPortal>
        )}
        {isDetached('reconcile') && (
          <DetachedPanelPortal title="Reconcile" onClose={() => attach('reconcile')}>
            <ReconciliationTab />
          </DetachedPanelPortal>
        )}
        {isDetached('bots') && (
          <DetachedPanelPortal title="Bot History" onClose={() => attach('bots')}>
            <BotHistoryTab />
          </DetachedPanelPortal>
        )}
        {isDetached('ticks') && (
          <DetachedPanelPortal title="Ticks" onClose={() => attach('ticks')}>
            <TickViewerTab />
          </DetachedPanelPortal>
        )}
        {isDetached('history') && (
          <DetachedPanelPortal title="History" onClose={() => attach('history')}>
            <TradeHistoryContent />
          </DetachedPanelPortal>
        )}
        {isDetached('equity') && (
          <DetachedPanelPortal title="Equity Curve" onClose={() => attach('equity')}>
            <EquityCurveTab />
          </DetachedPanelPortal>
        )}
"""

content = content.replace("<GlobalDeployDialog switchToAlgoTab={() => handleTabChange('algo')} />", 
                          "<GlobalDeployDialog switchToAlgoTab={() => handleTabChange('algo')} />\n" + portals)


with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
