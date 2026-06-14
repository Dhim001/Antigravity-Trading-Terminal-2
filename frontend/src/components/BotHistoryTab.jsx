/**
 * All bots (active + stopped) from bot_list_all.
 */
import React, { useEffect, useCallback } from 'react';
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

  const sorted = [...(botHistory ?? [])].sort((a, b) => {
    const sa = a.status === 'RUNNING' ? 0 : a.status === 'PAUSED' ? 1 : 2;
    const sb = b.status === 'RUNNING' ? 0 : b.status === 'PAUSED' ? 1 : 2;
    if (sa !== sb) return sa - sb;
    return (b.total_pnl ?? 0) - (a.total_pnl ?? 0);
  });

  const openDetail = (botId) => {
    setSelectedBotId(botId);
    setBotDrawerOpen(true);
    sendAction(Action.BOT_GET_DETAIL, { bot_id: botId });
  };

  if (!sorted.length) {
    return (
      <div className="flex min-h-0 flex-1 flex-col p-4">
        <WidgetEmpty
          icon={History}
          title="No bot history"
          description="Deploy a bot from the Algo tab — stopped bots remain listed here."
        />
        <Button variant="outline" size="sm" className="mx-auto mt-2 text-xs" onClick={refresh}>
          <RefreshCw data-icon="inline-start" />
          Refresh
        </Button>
      </div>
    );
  }

  return (
    <div className="bot-history-tab flex min-h-0 flex-1 flex-col">
      <div className="bot-history-tab__toolbar flex items-center justify-between gap-2 border-b border-border px-2 py-1.5">
        <span className="text-[0.62rem] text-muted-foreground">
          {sorted.length} bot{sorted.length !== 1 ? 's' : ''} total
        </span>
        <Button variant="ghost" size="xs" className="h-6 text-[0.62rem]" onClick={refresh}>
          <RefreshCw data-icon="inline-start" />
          Refresh
        </Button>
      </div>
      <div className="scroll-panel-y flex-1">
        <table className="terminal-table m-0 text-[0.62rem]">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Strategy</th>
              <th>Status</th>
              <th className="text-right">PnL</th>
              <th className="text-right">Win%</th>
              <th className="text-right">Trades</th>
              <th></th>
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
                <td className="num-mono text-right">{bot.win_rate != null ? `${bot.win_rate}%` : '—'}</td>
                <td className="num-mono text-right">{bot.trade_count ?? 0}</td>
                <td className="text-right">
                  <Button
                    variant="ghost"
                    size="xs"
                    className="h-6 px-1.5 text-[0.58rem]"
                    onClick={() => openDetail(bot.id)}
                    title={`View ${shortBotId(bot.id)}`}
                  >
                    <ExternalLink className="size-3" />
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
