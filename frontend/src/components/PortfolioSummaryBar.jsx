import { memo, useMemo } from 'react';
import { useStore } from '../store/useStore';
import { useSettingsStore } from '../store/useSettingsStore';
import {
  selectCashTotal,
  selectDayPnlTotal,
  selectInvestedTotal,
  selectRunningBotCount,
} from '../store/selectors';
import { cn } from '@/lib/utils';

/** Tiny inline SVG sparkline from the last ~20 equity points. */
function MiniSparkline({ data, width = 56, height = 14, className }) {
  if (!data || data.length < 2) return null;
  const vals = data.slice(-20);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const range = hi - lo || 1;
  const step = width / (vals.length - 1);
  const pts = vals
    .map((v, i) => `${(i * step).toFixed(1)},${(height - ((v - lo) / range) * height).toFixed(1)}`)
    .join(' ');
  const isUp = vals[vals.length - 1] >= vals[0];
  const color = isUp ? 'var(--color-trading-up, #10b981)' : 'var(--color-trading-down, #ef4444)';
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn('inline-block align-middle', className)}
      aria-hidden
    >
      <polyline
        points={pts}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function PortfolioSummaryBar({ compact = false }) {
  const cash = useStore(selectCashTotal);
  const invested = useStore(selectInvestedTotal);
  const dayPnl = useStore(selectDayPnlTotal);
  const runningBots = useStore(selectRunningBotCount);
  const connectionStatus = useStore((s) => s.connectionStatus);
  const apiStatus = useStore((s) => s.apiStatus);
  const tradeHistory = useStore((s) => s.tradeHistory);

  const layoutMode = useSettingsStore((s) => s.settings.workspace?.layoutMode || 'trade');
  const portfolioFocus = layoutMode === 'portfolio';
  const equity = cash + invested;

  const connected = connectionStatus === 'connected';
  const restOnly = !connected && apiStatus === 'ready';

  // P&L percentage
  const startEquity = equity - dayPnl;
  const pnlPct = startEquity > 0 ? (dayPnl / startEquity) * 100 : 0;
  const pnlPctStr = `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`;

  // Sparkline from recent sell fills
  const sparkData = useMemo(() => {
    const fills = (tradeHistory || [])
      .filter((t) => t.status === 'FILLED' && t.side === 'SELL' && t.realized_pnl != null)
      .sort((a, b) => a.timestamp - b.timestamp);
    if (fills.length < 2) return null;
    let cum = 0;
    return fills.slice(-20).map((t) => {
      cum += t.realized_pnl;
      return cum;
    });
  }, [tradeHistory]);

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
        <span className="ml-1 opacity-75 text-[0.65rem]">({pnlPctStr})</span>
      </span>
      {sparkData && <MiniSparkline data={sparkData} />}
      <span className="text-muted-foreground">
        <strong className="text-foreground">{runningBots}</strong> bots
      </span>
      {restOnly && (
        <span
          className="terminal-warn-chip"
          title="REST fallback — WebSocket reconnecting"
        >
          {compact ? 'REST' : 'REST fallback — WS reconnecting'}
        </span>
      )}
    </div>
  );
}

export default memo(PortfolioSummaryBar);
