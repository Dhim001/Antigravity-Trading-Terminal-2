/**
 * TradingView-style 4-tab report layout for Backtest Lab.
 */
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'performance', label: 'Performance' },
  { id: 'trades', label: 'Trades' },
  { id: 'properties', label: 'Properties' },
];

export default function BacktestReportTabs({
  defaultTab = 'overview',
  overview,
  performance,
  trades,
  properties,
}) {
  return (
    <Tabs defaultValue={defaultTab} className="backtest-report-tabs min-w-0">
      <TabsList className="backtest-report-tabs__list w-full">
        {TABS.map((tab) => (
          <TabsTrigger key={tab.id} value={tab.id} className="backtest-report-tabs__trigger">
            {tab.label}
          </TabsTrigger>
        ))}
      </TabsList>
      <TabsContent value="overview" className="backtest-report-tabs__panel">
        {overview}
      </TabsContent>
      <TabsContent value="performance" className="backtest-report-tabs__panel">
        {performance}
      </TabsContent>
      <TabsContent value="trades" className="backtest-report-tabs__panel">
        {trades}
      </TabsContent>
      <TabsContent value="properties" className="backtest-report-tabs__panel">
        {properties}
      </TabsContent>
    </Tabs>
  );
}
