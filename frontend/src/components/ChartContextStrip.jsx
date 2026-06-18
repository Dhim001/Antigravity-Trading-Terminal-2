/**
 * ChartContextStrip — contextual breadcrumb under chart (UX-7).
 */
import { useStore } from '../store/useStore';
import { selectAgentInsight } from '../lib/agentInsights';
import { useSettingsStore } from '../store/useSettingsStore';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export default function ChartContextStrip() {
  const activeSymbol = useStore((s) => s.activeSymbol);
  const agentInsights = useStore((s) => s.agentInsights);
  const activeBots = useStore((s) => s.activeBots);
  const isBotRunning = useStore((s) => s.isBotRunning);
  const chartLayout = useSettingsStore((s) => s.settings.chartLayout);
  const chartTf = chartLayout?.timeframe || '1m';

  const insight = selectAgentInsight(agentInsights, activeSymbol, chartTf);
  const runningBot = activeBots.find((b) => b.status === 'RUNNING' && b.symbol === activeSymbol);

  const activeIndicators = Object.entries(chartLayout?.activeIndicators || {})
    .filter(([, on]) => on)
    .map(([k]) => k.toUpperCase())
    .slice(0, 4);

  const jumpDock = (tab) => {
    window.dispatchEvent(new CustomEvent('dock-tab', { detail: tab }));
  };

  return (
    <div className="chart-context-strip">
      <button type="button" className="chart-context-strip__seg" onClick={() => jumpDock('positions')}>
        <span className="text-muted-foreground">Symbol</span>
        <strong>{activeSymbol}</strong>
      </button>
      <span className="chart-context-strip__dot" aria-hidden>·</span>
      <button type="button" className="chart-context-strip__seg" onClick={() => window.dispatchEvent(new CustomEvent('open-settings', { detail: 'chart' }))}>
        <span className="text-muted-foreground">TF</span>
        <strong>{chartLayout?.timeframe || '1m'}</strong>
      </button>
      {activeIndicators.length > 0 && (
        <>
          <span className="chart-context-strip__dot" aria-hidden>·</span>
          <span className="chart-context-strip__seg chart-context-strip__seg--static">
            <span className="text-muted-foreground">Indicators</span>
            <strong>{activeIndicators.join(' ')}</strong>
          </span>
        </>
      )}
      {insight?.signal && insight.signal !== 'NONE' && (
        <>
          <span className="chart-context-strip__dot" aria-hidden>·</span>
          <Button
            variant="ghost"
            size="xs"
            className="chart-context-strip__action"
            onClick={() => jumpDock('analyst')}
          >
            Analyst
            <Badge variant={insight.signal === 'BUY' ? 'default' : 'destructive'} className="ml-1 h-4 px-1 text-[0.55rem]">
              {insight.signal}
            </Badge>
          </Button>
        </>
      )}
      {(isBotRunning || runningBot) && (
        <>
          <span className="chart-context-strip__dot" aria-hidden>·</span>
          <Button
            variant="ghost"
            size="xs"
            className={cn('chart-context-strip__action', runningBot && 'text-trading-up')}
            onClick={() => {
              window.dispatchEvent(new CustomEvent('automation-studio-open'));
              jumpDock('algo');
            }}
          >
            {runningBot ? `Bot ${runningBot.id.slice(0, 6)} RUNNING` : 'Bots active'}
          </Button>
        </>
      )}
    </div>
  );
}
