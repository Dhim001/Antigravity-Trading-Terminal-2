/**
 * TradingPanel — tabbed right rail with collapse (UX-3).
 */
import { useSettingsStore } from '../store/useSettingsStore';
import OrderBookWidget from './OrderBookWidget';
import OrderEntryWidget from './OrderEntryWidget';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { PanelRightClose, PanelRightOpen, ArrowLeftRight, BookOpen, LineChart } from 'lucide-react';
import { cn } from '@/lib/utils';

export default function TradingPanel({ hidden = false }) {
  const workspace = useSettingsStore((s) => s.settings.workspace);
  const updateWorkspace = useSettingsStore((s) => s.updateWorkspace);
  const collapsed = workspace?.rightPanelCollapsed ?? false;
  const tab = workspace?.rightPanelTab || 'trade';

  if (hidden) return null;

  if (collapsed) {
    return (
      <aside className="trading-panel trading-panel--collapsed" aria-label="Trading panel collapsed">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              className="trading-panel__expand-btn"
              onClick={() => updateWorkspace({ rightPanelCollapsed: false })}
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
    <section className={cn('trading-panel', 'trading-panel--tabbed')}>
      <div className="trading-panel__header">
        <Tabs
          value={tab}
          onValueChange={(v) => v && updateWorkspace({ rightPanelTab: v })}
          className="trading-panel__tabs"
        >
          <TabsList className="trading-panel__tab-list h-7">
            <TabsTrigger value="trade" className="text-[0.62rem] px-2 h-6">
              <ArrowLeftRight data-icon="inline-start" />
              Trade
            </TabsTrigger>
            <TabsTrigger value="book" className="text-[0.62rem] px-2 h-6">
              <BookOpen data-icon="inline-start" />
              Book
            </TabsTrigger>
            <TabsTrigger value="depth" className="text-[0.62rem] px-2 h-6">
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
        {tab === 'depth' && <OrderBookWidget />}
      </div>
    </section>
  );
}
