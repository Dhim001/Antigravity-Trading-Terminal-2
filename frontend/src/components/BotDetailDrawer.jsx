import React from 'react';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { cn } from '@/lib/utils';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollTablePanel } from './WidgetShell';
import StrategyBadge from './StrategyBadge';
import { getStrategyMeta } from '@/config/strategies';
import { Pause, PlayCircle, OctagonX } from 'lucide-react';

export default function BotDetailDrawer({ open, onOpenChange, onStop, onPause, onResume }) {
  const botDetail = useStore(s => s.botDetail);
  const selectedBotId = useStore(s => s.selectedBotId);
  const setSelectedBotId = useStore(s => s.setSelectedBotId);
  const setBotDetail = useStore(s => s.setBotDetail);

  const bot = botDetail?.bot;
  const stats = botDetail?.stats;
  const trades = botDetail?.trades ?? [];
  const strategyMeta = bot ? getStrategyMeta(bot.strategy) : null;

  const handleClose = () => {
    onOpenChange(false);
    setSelectedBotId(null);
    setBotDetail(null);
  };

  const refresh = () => {
    if (selectedBotId) {
      sendAction(Action.BOT_GET_DETAIL, { bot_id: selectedBotId });
    }
  };

  return (
    <Sheet open={open} onOpenChange={(v) => { if (!v) handleClose(); else onOpenChange(v); }}>
      <SheetContent side="right" className="bot-detail-drawer w-full sm:max-w-lg">
        <SheetHeader className="border-b border-border pb-3">
          <SheetTitle className="flex items-center gap-2 text-sm">
            {bot ? (
              <>
                <span className="font-bold">{bot.symbol}</span>
                <Badge variant={bot.status === 'RUNNING' ? 'buy' : 'secondary'}>{bot.status}</Badge>
              </>
            ) : 'Bot detail'}
          </SheetTitle>
          <SheetDescription className="text-xs">
            {bot && strategyMeta ? (
              <span className="flex flex-col gap-1.5">
                <StrategyBadge strategy={bot.strategy} />
                <span className="text-muted-foreground">{strategyMeta.tagline}</span>
                <span className="text-muted-foreground">
                  {bot.timeframe} · ${Number(bot.allocation).toLocaleString()} alloc
                </span>
              </span>
            ) : 'Select a bot'}
          </SheetDescription>
        </SheetHeader>

        {bot && stats && (
          <div className="bot-detail-drawer__body">
            <div className="bot-detail-stats shrink-0">
              <div><span>Trades</span><strong>{stats.trade_count}</strong></div>
              <div><span>Win rate</span><strong>{stats.win_rate}%</strong></div>
              <div><span>Total PnL</span><strong className={stats.total_pnl >= 0 ? 'text-trading-up' : 'text-trading-down'}>${stats.total_pnl}</strong></div>
              <div><span>Today</span><strong className={stats.daily_pnl >= 0 ? 'text-trading-up' : 'text-trading-down'}>${stats.daily_pnl}</strong></div>
            </div>

            <div className="flex shrink-0 flex-wrap gap-2">
              {bot.status === 'RUNNING' && (
                <>
                  <Button variant="outline" size="xs" onClick={() => onPause(bot.id)}><Pause /> Pause</Button>
                  <Button variant="outline" size="xs" onClick={refresh}>Refresh</Button>
                </>
              )}
              {bot.status === 'PAUSED' && (
                <Button variant="outline" size="xs" onClick={() => onResume(bot.id)}><PlayCircle /> Resume</Button>
              )}
              {bot.status !== 'STOPPED' && (
                <Button variant="destructive" size="xs" onClick={() => onStop(bot.id)}><OctagonX /> Stop</Button>
              )}
            </div>

            {bot?.config && Object.keys(bot.config).length > 0 && (
              <pre className="algo-config-preview text-[0.58rem] rounded-md border border-border bg-muted/20 p-2">
                {JSON.stringify(bot.config, null, 2)}
              </pre>
            )}

            <ScrollTablePanel className="bot-detail-drawer__trades">
              <table className="terminal-table m-0 text-[0.62rem]">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Side</th>
                    <th className="text-right">Qty</th>
                    <th className="text-right">Price</th>
                    <th className="text-right">PnL</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.length === 0 ? (
                    <tr><td colSpan="5" className="algo-table-empty">No trades yet</td></tr>
                  ) : (
                    trades.map(t => (
                      <tr key={t.id ?? `${t.timestamp}-${t.side}-${t.price}`}>
                        <td className="text-muted-foreground whitespace-nowrap">
                          {t.timestamp ? new Date(`${t.timestamp}Z`).toLocaleString() : '—'}
                        </td>
                        <td>{t.side}{t.is_exit ? ' ↗' : ''}</td>
                        <td className="num-mono text-right">{Number(t.quantity).toFixed(4)}</td>
                        <td className="num-mono text-right">{Number(t.price).toFixed(2)}</td>
                        <td className={cn('num-mono text-right', t.pnl != null && (t.pnl >= 0 ? 'text-trading-up' : 'text-trading-down'))}>
                          {t.pnl != null ? `$${Number(t.pnl).toFixed(2)}` : '—'}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </ScrollTablePanel>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
