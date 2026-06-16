import { useMemo } from 'react';
import { useStore } from '../store/useStore';
import { cn } from '@/lib/utils';

function sumBalances(balances) {
  let total = 0;
  for (const row of Object.values(balances || {})) {
    total += (row.balance ?? 0) - (row.locked ?? 0);
  }
  return total;
}

function sumPositionValue(positions, tickers) {
  let total = 0;
  for (const [sym, pos] of Object.entries(positions || {})) {
    const size = pos?.size ?? 0;
    if (!size) continue;
    const px = tickers?.[sym]?.price ?? pos.avg_price ?? 0;
    total += size * px;
  }
  return total;
}

export default function PortfolioSummaryBar() {
  const balances = useStore((s) => s.balances);
  const positions = useStore((s) => s.positions);
  const tickerData = useStore((s) => s.tickerData);
  const activeBots = useStore((s) => s.activeBots);
  const connectionStatus = useStore((s) => s.connectionStatus);
  const apiStatus = useStore((s) => s.apiStatus);

  const summary = useMemo(() => {
    const cash = sumBalances(balances);
    const invested = sumPositionValue(positions, tickerData);
    const equity = cash + invested;
    const runningBots = (activeBots || []).filter((b) => b.status === 'RUNNING').length;
    let dayPnl = 0;
    for (const pos of Object.values(positions || {})) {
      dayPnl += pos?.unrealized_pnl ?? pos?.pnl ?? 0;
    }
    return { cash, invested, equity, dayPnl, runningBots };
  }, [balances, positions, tickerData, activeBots]);

  const connected = connectionStatus === 'connected';
  const restOnly = !connected && apiStatus === 'ready';

  return (
    <div className="portfolio-summary-bar flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-1 text-[0.68rem]">
      <span className="text-muted-foreground">
        Equity <strong className="num-mono text-foreground">${summary.equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong>
      </span>
      <span className="text-muted-foreground">
        Cash <strong className="num-mono text-foreground">${summary.cash.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong>
      </span>
      <span className="text-muted-foreground">
        Invested <strong className="num-mono text-foreground">${summary.invested.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong>
      </span>
      <span className={cn(
        summary.dayPnl >= 0 ? 'text-trading-up' : 'text-trading-down',
      )}>
        Open P&L <strong className="num-mono">${summary.dayPnl.toFixed(2)}</strong>
      </span>
      <span className="text-muted-foreground">
        Bots <strong className="text-foreground">{summary.runningBots}</strong> running
      </span>
      {restOnly && (
        <span className="rounded border border-trading-warn/40 bg-trading-warn/10 px-1.5 py-0.5 text-trading-warn">
          REST fallback — WS reconnecting
        </span>
      )}
    </div>
  );
}
