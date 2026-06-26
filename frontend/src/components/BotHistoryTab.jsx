/**
 * All bots (active + stopped) from bot_list_all.
 */
import React, { useEffect, useCallback, useMemo, useState } from 'react';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import StrategyBadge from './StrategyBadge';
import TradeExplainCard from './TradeExplainCard';
import { WidgetEmpty } from './WidgetShell';
import {
  DataTableRoot,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableHead,
  DataTableCell,
} from './DataTableShell';
import { RefreshCw, History, ExternalLink, ChevronDown, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import { shortBotId, parseTradeTimestamp } from '@/lib/botAttribution';

function statusVariant(status) {
  if (status === 'RUNNING') return 'buy';
  if (status === 'PAUSED') return 'secondary';
  if (status === 'STOPPED') return 'outline';
  return 'secondary';
}

function formatTradeTime(timestamp) {
  const d = parseTradeTimestamp(timestamp);
  if (!d) return '—';
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export default function BotHistoryTab() {
  const botHistory = useStore(s => s.botHistory);
  const botDetail = useStore(s => s.botDetail);
  const agentInsights = useStore(s => s.agentInsights);
  const agentInsightHistory = useStore(s => s.agentInsightHistory);
  const setSelectedBotId = useStore(s => s.setSelectedBotId);
  const setBotDrawerOpen = useStore(s => s.setBotDrawerOpen);

  const [expandedBotId, setExpandedBotId] = useState(null);
  const [detailCache, setDetailCache] = useState({});

  const refresh = useCallback(() => {
    sendAction(Action.BOT_LIST_ALL, { limit: 200 });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!botDetail?.bot?.id) return;
    setDetailCache((prev) => ({
      ...prev,
      [botDetail.bot.id]: botDetail,
    }));
  }, [botDetail]);

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

  const toggleExpand = (botId) => {
    if (expandedBotId === botId) {
      setExpandedBotId(null);
      return;
    }
    setExpandedBotId(botId);
    if (!detailCache[botId]) {
      sendAction(Action.BOT_GET_DETAIL, { bot_id: botId });
    }
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
            <DataTableRoot variant="dock" className="dock-panel-tab__table min-w-[640px] text-[0.62rem]">
              <DataTableHeader>
                <tr>
                  <DataTableHead className="w-8" />
                  <DataTableHead>Symbol</DataTableHead>
                  <DataTableHead>Strategy</DataTableHead>
                  <DataTableHead>Status</DataTableHead>
                  <DataTableHead align="right">PnL</DataTableHead>
                  <DataTableHead align="right">Win%</DataTableHead>
                  <DataTableHead align="right">Trades</DataTableHead>
                  <DataTableHead align="center">Detail</DataTableHead>
                </tr>
              </DataTableHeader>
              <DataTableBody>
                {sorted.map(bot => {
                  const expanded = expandedBotId === bot.id;
                  const detail = detailCache[bot.id];
                  const trades = (detail?.trades ?? []).slice(0, 5);
                  return (
                    <React.Fragment key={bot.id}>
                      <DataTableRow
                        rowVariant="dock"
                        deferred
                        className={cn(bot.status === 'STOPPED' && 'opacity-75')}
                      >
                        <DataTableCell align="center">
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            className="h-6 w-6"
                            onClick={() => toggleExpand(bot.id)}
                            title={expanded ? 'Collapse fills' : 'Show recent fills & explain'}
                          >
                            {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
                          </Button>
                        </DataTableCell>
                        <DataTableCell className="font-semibold">{bot.symbol}</DataTableCell>
                        <DataTableCell><StrategyBadge strategy={bot.strategy} compact /></DataTableCell>
                        <DataTableCell><Badge variant={statusVariant(bot.status)}>{bot.status}</Badge></DataTableCell>
                        <DataTableCell
                          numeric
                          align="right"
                          className={(bot.total_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down'}
                        >
                          ${Number(bot.total_pnl ?? 0).toFixed(2)}
                        </DataTableCell>
                        <DataTableCell numeric align="right">
                          {bot.win_rate != null ? `${bot.win_rate}%` : '—'}
                        </DataTableCell>
                        <DataTableCell numeric align="right">{bot.trade_count ?? 0}</DataTableCell>
                        <DataTableCell align="center">
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={() => openDetail(bot.id)}
                            title={`View ${shortBotId(bot.id)}`}
                          >
                            <ExternalLink />
                          </Button>
                        </DataTableCell>
                      </DataTableRow>
                      {expanded && (
                        <DataTableRow rowVariant="dock" className="bot-history-expand-row hover:bg-transparent">
                          <DataTableCell colSpan={8} className="p-0">
                            <div className="bot-history-expand px-3 py-2">
                              {!detail ? (
                                <p className="text-xs text-muted-foreground py-2">Loading fills…</p>
                              ) : trades.length === 0 ? (
                                <p className="text-xs text-muted-foreground py-2">No fills yet</p>
                              ) : (
                                <div className="space-y-2">
                                  {trades.map((t) => (
                                    <div key={t.id ?? `${t.timestamp}-${t.side}`} className="bot-history-fill">
                                      <div className="flex flex-wrap items-center gap-2 text-[0.62rem]">
                                        <span className="text-muted-foreground">{formatTradeTime(t.timestamp)}</span>
                                        <Badge variant={t.side === 'BUY' ? 'buy' : 'sell'} className="h-4 text-[0.55rem]">
                                          {t.side}{t.is_exit ? ' exit' : ''}
                                        </Badge>
                                        <span className="num-mono">{Number(t.quantity).toFixed(4)} @ {Number(t.price).toFixed(4)}</span>
                                        {t.pnl != null && (
                                          <span className={cn('num-mono', t.pnl >= 0 ? 'text-trading-up' : 'text-trading-down')}>
                                            ${Number(t.pnl).toFixed(2)}
                                          </span>
                                        )}
                                      </div>
                                      {t.id != null && (
                                        <TradeExplainCard
                                          trade={t}
                                          symbol={bot.symbol}
                                          botId={bot.id}
                                          botStrategy={bot.strategy}
                                          botTimeframe={detail?.bot?.timeframe ?? bot.timeframe ?? '1m'}
                                          agentInsights={agentInsights}
                                          agentInsightHistory={agentInsightHistory}
                                          useLlm={Boolean(bot.config?.use_llm)}
                                          compact
                                        />
                                      )}
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          </DataTableCell>
                        </DataTableRow>
                      )}
                    </React.Fragment>
                  );
                })}
              </DataTableBody>
            </DataTableRoot>
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
