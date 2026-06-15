/**
 * All bots (active + stopped) from bot_list_all.
 */
import React, { useEffect, useCallback, useMemo } from 'react';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import StrategyBadge from './StrategyBadge';
import { WidgetEmpty } from './WidgetShell';
import { RefreshCw, History, ExternalLink } from 'lucide-react';
import { cn } from '@/lib/utils';
import { shortBotId } from '@/lib/botAttribution';

function statusVariant(status) {
  if (status === 'RUNNING') return 'buy';
  if (status === 'PAUSED') return 'secondary';
  if (status === 'STOPPED') return 'outline';
  return 'secondary';
}

export default function BotHistoryTab() {
  const botHistory = useStore(s => s.botHistory);
  const setSelectedBotId = useStore(s => s.setSelectedBotId);
  const setBotDrawerOpen = useStore(s => s.setBotDrawerOpen);

  const refresh = useCallback(() => {
    sendAction(Action.BOT_LIST_ALL, { limit: 200 });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const sorted = useMemo(() => [...(botHistory ?? [])].sort((a, b) => {
    const sa = a.status === 'RUNNING' ? 0 : a.status === 'PAUSED' ? 1 : 2;
    const sb = b.status === 'RUNNING' ? 0 : b.status === 'PAUSED' ? 1 : 2;
    if (sa !== sb) return sa - sb;
    return (b.total_pnl ?? 0) - (a.total_pnl ?? 0);
  }), [botHistory]);

  const stats = useMemo(() => {
    let running = 0;
    let paused = 0;
    let stopped = 0;
    let totalPnl = 0;
    for (const bot of sorted) {
      if (bot.status === 'RUNNING') running += 1;
      else if (bot.status === 'PAUSED') paused += 1;
      else stopped += 1;
      totalPnl += bot.total_pnl ?? 0;
    }
    return { running, paused, stopped, totalPnl };
  }, [sorted]);

  const openDetail = (botId) => {
    setSelectedBotId(botId);
    setBotDrawerOpen(true);
    sendAction(Action.BOT_GET_DETAIL, { bot_id: botId });
  };

  const pnlPositive = stats.totalPnl >= 0;

  return (
    <div className="dock-panel-tab">
      <header className="dock-panel-tab__toolbar">
        <div className="dock-panel-tab__toolbar-lead">
          <div className="dock-panel-tab__toolbar-icon" aria-hidden>
            <History size={14} />
          </div>
          <div className="dock-panel-tab__toolbar-copy">
            <span className="dock-panel-tab__toolbar-title">Bot History</span>
            <span className="dock-panel-tab__toolbar-subtitle num-mono">
              {sorted.length} bot{sorted.length === 1 ? '' : 's'}
              {sorted.length > 0 && (
                <> · {stats.running} run · {stats.paused} pause · {stats.stopped} stop</>
              )}
            </span>
          </div>
        </div>
        <div className="dock-panel-tab__toolbar-actions">
          {sorted.length > 0 && (
            <div className="dock-panel-tab__toolbar-meta">
              <span className="dock-panel-tab__meta-label">Total P&L</span>
              <span
                className={cn(
                  'dock-panel-tab__meta-value num-mono',
                  pnlPositive ? 'dock-panel-tab__meta-value--up' : 'dock-panel-tab__meta-value--down',
                )}
              >
                {pnlPositive ? '+' : ''}${stats.totalPnl.toFixed(2)}
              </span>
            </div>
          )}
          <Button variant="outline" size="sm" className="h-7 text-xs" onClick={refresh}>
            <RefreshCw data-icon="inline-start" aria-hidden />
            Refresh
          </Button>
        </div>
      </header>

      {sorted.length === 0 ? (
        <div className="dock-panel-tab__empty">
          <WidgetEmpty
            icon={History}
            title="No bot history"
            description="Deploy a bot from the Algo tab — stopped bots remain listed here."
          />
        </div>
      ) : (
        <>
          <div className="dock-panel-tab__table-wrap scroll-panel-y scroll-panel-y-0">
            <table className="terminal-table dock-panel-tab__table min-w-[640px] text-[0.62rem]">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Strategy</th>
                  <th>Status</th>
                  <th className="text-right">PnL</th>
                  <th className="text-right">Win%</th>
                  <th className="text-right">Trades</th>
                  <th className="text-center">Detail</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map(bot => (
                  <tr key={bot.id} className={cn(bot.status === 'STOPPED' && 'opacity-75')}>
                    <td className="font-semibold">{bot.symbol}</td>
                    <td><StrategyBadge strategy={bot.strategy} compact /></td>
                    <td><Badge variant={statusVariant(bot.status)}>{bot.status}</Badge></td>
                    <td className={cn(
                      'num-mono text-right',
                      (bot.total_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down',
                    )}>
                      ${Number(bot.total_pnl ?? 0).toFixed(2)}
                    </td>
                    <td className="num-mono text-right">
                      {bot.win_rate != null ? `${bot.win_rate}%` : '—'}
                    </td>
                    <td className="num-mono text-right">{bot.trade_count ?? 0}</td>
                    <td className="text-center">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => openDetail(bot.id)}
                        title={`View ${shortBotId(bot.id)}`}
                      >
                        <ExternalLink />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <footer className="dock-panel-tab__footer">
            <span>
              {stats.running} running · {stats.paused} paused · {stats.stopped} stopped
            </span>
            <span className="dock-panel-tab__footer-highlight">
              Combined P&L:{' '}
              <span
                className={cn(
                  'num-mono font-bold',
                  pnlPositive ? 'text-trading-up' : 'text-trading-down',
                )}
              >
                {pnlPositive ? '+' : ''}${stats.totalPnl.toFixed(2)}
              </span>
            </span>
          </footer>
        </>
      )}
    </div>
  );
}
