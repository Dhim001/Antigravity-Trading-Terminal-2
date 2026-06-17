import { memo } from 'react';
import { useStore } from '../store/useStore';
import { useSettingsStore } from '../store/useSettingsStore';
import {
  selectCashTotal,
  selectDayPnlTotal,
  selectInvestedTotal,
  selectRunningBotCount,
} from '../store/selectors';
import { cn } from '@/lib/utils';

function PortfolioSummaryBar({ compact = false }) {
  const cash = useStore(selectCashTotal);
  const invested = useStore(selectInvestedTotal);
  const dayPnl = useStore(selectDayPnlTotal);
  const runningBots = useStore(selectRunningBotCount);
  const connectionStatus = useStore((s) => s.connectionStatus);
  const apiStatus = useStore((s) => s.apiStatus);

  const layoutMode = useSettingsStore((s) => s.settings.workspace?.layoutMode || 'trade');
  const portfolioFocus = layoutMode === 'portfolio';
  const equity = cash + invested;

  const connected = connectionStatus === 'connected';
  const restOnly = !connected && apiStatus === 'ready';

  return (
    <div className={cn(
      'portfolio-summary-bar flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs',
      compact ? 'px-0 py-0' : 'px-3 py-1',
    )}>
      <span className="text-muted-foreground">
        Eq <strong className="num-mono text-foreground">${equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong>
      </span>
      {!compact && (
        <span className="text-muted-foreground">
          Cash <strong className="num-mono text-foreground">${cash.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong>
        </span>
      )}
      {compact && portfolioFocus && (
        <span className="text-muted-foreground">
          Cash <strong className="num-mono text-foreground">${cash.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong>
        </span>
      )}
      <span className="text-muted-foreground">
        Inv <strong className="num-mono text-foreground">${invested.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong>
      </span>
      <span className={cn(
        dayPnl >= 0 ? 'text-trading-up' : 'text-trading-down',
      )}>
        P&L <strong className="num-mono">${dayPnl.toFixed(2)}</strong>
      </span>
      <span className="text-muted-foreground">
        <strong className="text-foreground">{runningBots}</strong> bots
      </span>
      {restOnly && !compact && (
        <span className="rounded border border-trading-warn/40 bg-trading-warn/10 px-1.5 py-0.5 text-trading-warn">
          REST fallback — WS reconnecting
        </span>
      )}
    </div>
  );
}

export default memo(PortfolioSummaryBar);
