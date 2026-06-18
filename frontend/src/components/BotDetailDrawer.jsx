import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { fetchAgentInsights } from '../api/endpoints';
import SubReportCards from './SubReportCards';
import { cn } from '@/lib/utils';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import StrategyBadge from './StrategyBadge';
import { getStrategyMeta } from '@/config/strategies';
import { parseTradeTimestamp, shortBotId } from '@/lib/botAttribution';
import { formatBarTimeframeLabel } from '@/lib/barTimeframes';
import { normalizeAnalystTimeframe, selectAgentInsight } from '@/lib/agentInsights';
import { invokeHttpAction } from '../api/transport';
import BotSnapshotChart from './BotSnapshotChart';
import BotConfigPanel from './BotConfigPanel';
import { Pause, PlayCircle, OctagonX, Loader2, GripVertical } from 'lucide-react';

const DRAWER_WIDTH_KEY = 'terminal_bot_drawer_width';
const DRAWER_WIDTH_DEFAULT = 448;
const DRAWER_WIDTH_MIN = 320;
const DRAWER_WIDTH_MAX = 720;

function readDrawerWidth() {
  try {
    const n = parseInt(localStorage.getItem(DRAWER_WIDTH_KEY), 10);
    if (!Number.isNaN(n)) return Math.min(DRAWER_WIDTH_MAX, Math.max(DRAWER_WIDTH_MIN, n));
  } catch (_) {}
  return DRAWER_WIDTH_DEFAULT;
}

function formatTradeTime(timestamp) {
  const d = parseTradeTimestamp(timestamp);
  if (!d) return '—';
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function isEntryTrade(trade) {
  if (!trade) return false;
  const v = trade.is_exit;
  return v === false || v === 0 || v === '0' || v == null;
}

function tradeIdKey(trade) {
  if (trade?.id == null || trade.id === '') return null;
  return String(trade.id);
}

function findInsightForTrade(trade, symbol, timeframe, agentInsights, agentInsightHistory) {
  if (!trade || !isEntryTrade(trade)) return null;
  const tf = normalizeAnalystTimeframe(timeframe);
  const barTime = trade.signal_bar_time;
  if (barTime != null) {
    const history = agentInsightHistory[symbol] ?? [];
    const match = history.find(
      (i) => i.bar_time === barTime && normalizeAnalystTimeframe(i.timeframe) === tf,
    );
    if (match) return match;
  }
  const current = selectAgentInsight(agentInsights, symbol, tf);
  if (current && barTime != null && current.bar_time === barTime) {
    return current;
  }
  return null;
}

function TradeExplain({
  trade,
  symbol,
  botId,
  botStrategy,
  botTimeframe,
  agentInsights,
  agentInsightHistory,
}) {
  const tradeKey = tradeIdKey(trade);
  const explain = useStore((s) => (tradeKey ? s.tradeExplains[tradeKey] : null));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  if (botStrategy !== 'CHART_AGENT' || !isEntryTrade(trade)) return null;

  const insight = explain?.insight
    ?? findInsightForTrade(trade, symbol, botTimeframe, agentInsights, agentInsightHistory);

  const fetchExplain = async () => {
    if (!tradeKey || !botId || loading || explain) return;
    setLoading(true);
    setError(null);
    try {
      await invokeHttpAction(Action.EXPLAIN_TRADE, { bot_id: botId, trade_id: tradeKey });
    } catch (err) {
      setError(err?.message || 'Could not load explanation');
    } finally {
      setLoading(false);
    }
  };

  const handleToggle = (e) => {
    e.stopPropagation();
    if (e.currentTarget.open) {
      void fetchExplain();
    }
  };

  const handleSummaryClick = (e) => {
    e.stopPropagation();
  };

  const hasContent = Boolean(
    explain?.summary
    || explain?.narrative
    || insight?.reasons?.length
    || insight?.narrative
    || insight?.sub_reports,
  );

  return (
    <details
      className="bot-trade-explain"
      onToggle={handleToggle}
      onClick={(e) => e.stopPropagation()}
    >
      <summary className="bot-trade-explain__summary" onClick={handleSummaryClick}>
        <span>Why we entered</span>
        {loading && <Loader2 className="size-3 animate-spin shrink-0" aria-hidden />}
      </summary>
      <div className="bot-trade-explain__body">
        {loading && !hasContent && (
          <p className="bot-trade-explain__status">Loading explanation…</p>
        )}
        {error && (
          <p className="bot-trade-explain__error">{error}</p>
        )}
        {!tradeKey && (
          <p className="bot-trade-explain__status">Explanation unavailable for this fill.</p>
        )}
        {explain?.summary && (
          <p className="bot-trade-explain__summary-text">{explain.summary}</p>
        )}
        {explain?.narrative && (
          <p className="bot-trade-explain__narrative">{explain.narrative}</p>
        )}
        {insight?.sub_reports ? (
          <div className="bot-trade-explain__reports">
            <SubReportCards subReports={insight.sub_reports} />
          </div>
        ) : insight?.reasons?.length > 0 ? (
          <ul className="bot-trade-explain__reasons">
            {insight.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        ) : null}
        {!explain?.summary && insight?.narrative && (
          <p className="bot-trade-explain__narrative">{insight.narrative}</p>
        )}
        {!loading && !error && tradeKey && !hasContent && (
          <p className="bot-trade-explain__status">
            No analyst insight recorded for this entry bar.
          </p>
        )}
      </div>
    </details>
  );
}

export default function BotDetailDrawer({ open, onOpenChange, onStop, onPause, onResume, nested = false }) {
  const botDetail = useStore(s => s.botDetail);
  const selectedBotId = useStore(s => s.selectedBotId);
  const setSelectedBotId = useStore(s => s.setSelectedBotId);
  const setBotDetail = useStore(s => s.setBotDetail);
  const agentInsights = useStore(s => s.agentInsights);
  const agentInsightHistory = useStore(s => s.agentInsightHistory);
  const setAgentInsightHistory = useStore(s => s.setAgentInsightHistory);

  const bot = botDetail?.bot;
  const position = botDetail?.position;
  const stats = botDetail?.stats;
  const trades = botDetail?.trades ?? [];
  const snapshots = botDetail?.snapshots ?? [];
  const strategyMeta = bot ? getStrategyMeta(bot.strategy) : null;
  const loading = open && selectedBotId && !bot;

  useEffect(() => {
    if (!open || !bot?.symbol || bot.strategy !== 'CHART_AGENT') return;
    fetchAgentInsights(bot.symbol, { setAgentInsightHistory }, 80).catch(() => {});
  }, [open, bot?.id, bot?.symbol, bot?.strategy, setAgentInsightHistory]);

  const [drawerWidth, setDrawerWidth] = useState(readDrawerWidth);
  const [resizing, setResizing] = useState(false);
  const isDragging = useRef(false);
  const startX = useRef(0);
  const startW = useRef(0);

  useEffect(() => {
    try { localStorage.setItem(DRAWER_WIDTH_KEY, String(drawerWidth)); } catch (_) {}
  }, [drawerWidth]);

  const onResizeMouseDown = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    isDragging.current = true;
    setResizing(true);
    startX.current = e.clientX;
    startW.current = drawerWidth;
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';
  }, [drawerWidth]);

  useEffect(() => {
    const onMove = (e) => {
      if (!isDragging.current) return;
      const delta = startX.current - e.clientX;
      const next = Math.min(DRAWER_WIDTH_MAX, Math.max(DRAWER_WIDTH_MIN, startW.current + delta));
      setDrawerWidth(next);
    };
    const onUp = () => {
      if (!isDragging.current) return;
      isDragging.current = false;
      setResizing(false);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, []);

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
      <SheetContent
        side="right"
        overlayClassName={nested ? 'terminal-sheet-overlay--nested' : undefined}
        className={cn(
          'terminal-sheet bot-detail-drawer sm:max-w-none',
          nested && 'terminal-sheet--nested',
          resizing && 'bot-detail-drawer--resizing',
        )}
        style={{
          width: `${drawerWidth}px`,
          maxWidth: 'min(92vw, 100%)',
        }}
      >
        <div
          className={cn('bot-detail-drawer__resize', resizing && 'dragging')}
          onMouseDown={onResizeMouseDown}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize bot detail panel"
          title="Drag to resize"
        >
          <span className="bot-detail-drawer__resize-grip" aria-hidden>
            <GripVertical size={12} />
          </span>
        </div>
        <SheetHeader className="terminal-sheet__header bot-detail-drawer__header">
          <SheetTitle className="bot-detail-drawer__title">
            {bot ? (
              <>
                <span className="truncate font-bold">{bot.symbol}</span>
                <Badge variant={bot.status === 'RUNNING' ? 'buy' : 'secondary'} className="shrink-0">
                  {bot.status}
                </Badge>
              </>
            ) : (
              <span className="text-muted-foreground">Bot detail</span>
            )}
          </SheetTitle>
          <SheetDescription asChild>
            <div className="bot-detail-drawer__meta">
              {bot && strategyMeta ? (
                <>
                  <StrategyBadge strategy={bot.strategy} />
                  <p className="bot-detail-drawer__tagline">{strategyMeta.tagline}</p>
                  <p className="bot-detail-drawer__subline">
                    {bot.execution_mode === 'TICK' ? 'tick' : formatBarTimeframeLabel(bot.timeframe)} bars
                    <span className="text-muted-foreground/60" aria-hidden> · </span>
                    ${Number(bot.allocation).toLocaleString()} allocated
                  </p>
                </>
              ) : (
                <span>{loading ? 'Loading bot metrics…' : 'Select a bot from the table'}</span>
              )}
            </div>
          </SheetDescription>
        </SheetHeader>

        {loading && (
          <div className="bot-detail-drawer__loading" aria-live="polite">
            <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
            <span>Fetching bot detail…</span>
          </div>
        )}

        {bot && stats && (
          <div className="terminal-sheet__body bot-detail-drawer__body">
            <div className="bot-detail-stats">
              <div className="bot-detail-stat">
                <span>Executions</span>
                <strong>{stats.trade_count}</strong>
              </div>
              <div className="bot-detail-stat">
                <span>Exits</span>
                <strong>{stats.exit_count ?? '—'}</strong>
              </div>
              <div className="bot-detail-stat">
                <span>Win rate</span>
                <strong>{stats.win_rate != null ? `${stats.win_rate}%` : '—'}</strong>
              </div>
              <div className="bot-detail-stat">
                <span>Total PnL</span>
                <strong className={stats.total_pnl >= 0 ? 'text-trading-up' : 'text-trading-down'}>
                  ${Number(stats.total_pnl).toFixed(2)}
                </strong>
              </div>
              <div className="bot-detail-stat">
                <span>Today</span>
                <strong className={stats.daily_pnl >= 0 ? 'text-trading-up' : 'text-trading-down'}>
                  ${Number(stats.daily_pnl).toFixed(2)}
                </strong>
              </div>
            </div>

            <BotConfigPanel
              botId={bot.id}
              strategy={bot.strategy}
              config={bot.config}
              botStatus={bot.status}
              position={position}
            />

            <section className="bot-detail-drawer__equity" aria-label="Bot equity snapshots">
              <header className="bot-detail-drawer__trades-header">
                <span>Equity curve</span>
                <Badge variant="secondary">{snapshots.length}</Badge>
              </header>
              <BotSnapshotChart snapshots={snapshots} allocation={bot.allocation} />
            </section>

            <div className="bot-detail-drawer__actions">
              {bot.status === 'RUNNING' && (
                <>
                  <Button variant="outline" size="xs" onClick={() => onPause(bot.id)}>
                    <Pause /> Pause
                  </Button>
                  <Button variant="outline" size="xs" onClick={refresh}>Refresh</Button>
                </>
              )}
              {bot.status === 'PAUSED' && (
                <Button variant="outline" size="xs" onClick={() => onResume(bot.id)}>
                  <PlayCircle /> Resume
                </Button>
              )}
              {bot.status !== 'STOPPED' && (
                <Button variant="destructive" size="xs" onClick={() => onStop(bot.id)}>
                  <OctagonX /> Stop
                </Button>
              )}
            </div>

            <section className="bot-detail-drawer__trades" aria-label="Bot trade history">
              <header className="bot-detail-drawer__trades-header">
                <span>Recent fills</span>
                <Badge variant="secondary">{trades.length}</Badge>
              </header>
              <div className="bot-detail-drawer__trades-scroll">
                <table className="terminal-table bot-detail-trades-table m-0">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Side</th>
                      <th className="text-right">Qty</th>
                      <th className="text-right">Fill</th>
                      <th className="text-right">PnL</th>
                      <th>Order</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.length === 0 ? (
                      <tr>
                        <td colSpan="6" className="algo-table-empty">No trades yet</td>
                      </tr>
                    ) : (
                      trades.map((t) => {
                        const showExplain = bot?.strategy === 'CHART_AGENT' && isEntryTrade(t);
                        return (
                          <React.Fragment key={t.id ?? `${t.timestamp}-${t.side}-${t.price}`}>
                            <tr>
                              <td className="bot-detail-trades-table__time" title={formatTradeTime(t.timestamp)}>
                                {formatTradeTime(t.timestamp)}
                              </td>
                              <td
                                className={cn(
                                  'bot-detail-trades-table__side',
                                  t.side === 'BUY' ? 'text-trading-up' : 'text-trading-down',
                                )}
                              >
                                {t.side}{t.is_exit ? ' exit' : ''}
                              </td>
                              <td className="num-mono text-right">
                                {Number(t.quantity).toFixed(4)}
                              </td>
                              <td className="num-mono text-right">
                                {Number(t.price).toFixed(4)}
                              </td>
                              <td
                                className={cn(
                                  'num-mono text-right',
                                  t.pnl != null && (t.pnl >= 0 ? 'text-trading-up' : 'text-trading-down'),
                                )}
                              >
                                {t.pnl != null ? `$${Number(t.pnl).toFixed(2)}` : '—'}
                              </td>
                              <td
                                className="bot-detail-trades-table__order num-mono"
                                title={t.order_id}
                              >
                                {t.order_id ? shortBotId(t.order_id) : '—'}
                              </td>
                            </tr>
                            {showExplain && (
                              <tr className="bot-detail-trades-table__explain-row">
                                <td colSpan={6}>
                                  <TradeExplain
                                    trade={t}
                                    symbol={bot?.symbol}
                                    botId={bot?.id}
                                    botStrategy={bot?.strategy}
                                    botTimeframe={bot?.timeframe}
                                    agentInsights={agentInsights}
                                    agentInsightHistory={agentInsightHistory}
                                  />
                                </td>
                              </tr>
                            )}
                          </React.Fragment>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
