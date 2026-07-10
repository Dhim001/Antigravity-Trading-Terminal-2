/**
 * TradingPanel — tabbed right rail with collapse (UX-3).
 */
import { useSettingsStore } from '../store/useSettingsStore';
import { useEffect } from 'react';
import OrderBookWidget from './OrderBookWidget';
import DepthChartWidget from './DepthChartWidget';
import OrderEntryWidget from './OrderEntryWidget';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { PanelRightClose, PanelRightOpen, ArrowLeftRight, BookOpen, LineChart } from 'lucide-react';
import { cn } from '@/lib/utils';
import { registerOrderBookConsumer } from '../services/orderBookInterest';

export default function TradingPanel({ hidden = false }) {
  const workspace = useSettingsStore((s) => s.settings.workspace);
  const updateWorkspace = useSettingsStore((s) => s.updateWorkspace);
  const collapsed = workspace?.rightPanelCollapsed ?? false;
  const tab = workspace?.rightPanelTab || 'trade';

  useEffect(() => {
    const onExpand = () => updateWorkspace({ rightPanelCollapsed: false, rightPanelTab: 'trade' });
    window.addEventListener('trading-panel-expand', onExpand);
    return () => window.removeEventListener('trading-panel-expand', onExpand);
  }, [updateWorkspace]);

  useEffect(() => {
    if (tab === 'book' || tab === 'depth') {
      return registerOrderBookConsumer();
    }
    return undefined;
  }, [tab]);

  if (hidden) return null;

  if (collapsed) {
    return (
      <aside
        className="trading-panel trading-panel--collapsed trading-panel-collapsed-rail"
        aria-label="Trading panel collapsed — click to expand"
        role="button"
        tabIndex={0}
        onClick={() => updateWorkspace({ rightPanelCollapsed: false })}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            updateWorkspace({ rightPanelCollapsed: false });
          }
        }}
      >
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              className="trading-panel__expand-btn"
              onClick={(e) => {
                e.stopPropagation();
                updateWorkspace({ rightPanelCollapsed: false });
              }}
              title="Expand trading panel"
            >
              <PanelRightOpen aria-hidden />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="left">Expand order panel</TooltipContent>
        </Tooltip>
      </aside>
    );
  }

  return (
    <section className={cn('trading-panel', 'trading-panel--tabbed')} data-tour="order-panel">
      <div className="trading-panel__header">
        <Tabs
          value={tab}
          onValueChange={(v) => v && updateWorkspace({ rightPanelTab: v })}
          className="trading-panel__tabs"
        >
          <TabsList className="trading-panel__tab-list">
            <TabsTrigger value="trade" className="text-xs">
              <ArrowLeftRight data-icon="inline-start" />
              Trade
            </TabsTrigger>
            <TabsTrigger value="book" className="text-xs">
              <BookOpen data-icon="inline-start" />
              Book
            </TabsTrigger>
            <TabsTrigger value="depth" className="text-xs">
              <LineChart data-icon="inline-start" />
              Depth
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <Button
          variant="ghost"
          size="icon-sm"
          className="trading-panel__collapse-btn"
          onClick={() => updateWorkspace({ rightPanelCollapsed: true })}
          title="Collapse panel (more chart space)"
        >
          <PanelRightClose aria-hidden />
        </Button>
      </div>
      <div className="trading-panel__body">
        {tab === 'trade' && <OrderEntryWidget />}
        {tab === 'book' && <OrderBookWidget />}
        {tab === 'depth' && <DepthChartWidget />}
      </div>
    </section>
  );
}
