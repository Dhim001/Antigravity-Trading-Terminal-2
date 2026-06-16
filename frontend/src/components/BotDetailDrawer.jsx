import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { cn } from '@/lib/utils';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import StrategyBadge from './StrategyBadge';
import { getStrategyMeta } from '@/config/strategies';
import { parseTradeTimestamp, shortBotId } from '@/lib/botAttribution';
import BotSnapshotChart from './BotSnapshotChart';
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

function findInsightForTrade(trade, symbol, agentInsights, agentInsightHistory) {
  if (!trade || trade.is_exit) return null;
  const barTime = trade.signal_bar_time;
  if (barTime != null) {
    const history = agentInsightHistory[symbol] ?? [];
    const match = history.find((i) => i.bar_time === barTime);
    if (match) return match;
  }
  const current = agentInsights[symbol];
  if (current && barTime != null && current.bar_time === barTime) return current;
  return current?.reasons?.length ? current : null;
}

function TradeExplain({ trade, symbol, botStrategy, agentInsights, agentInsightHistory }) {
  if (botStrategy !== 'CHART_AGENT' || trade.is_exit) return null;
  const insight = findInsightForTrade(trade, symbol, agentInsights, agentInsightHistory);
  if (!insight?.reasons?.length && !insight?.narrative) return null;
  return (
    <details className="mt-1 text-[0.65rem] text-muted-foreground">
      <summary className="cursor-pointer text-trading-accent">Why we entered</summary>
      {insight.reasons?.length > 0 && (
        <ul className="mt-1 list-inside list-disc space-y-0.5">
          {insight.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      {insight.narrative && (
        <p className="mt-1 leading-relaxed">{insight.narrative}</p>
      )}
    </details>
  );
}

export default function BotDetailDrawer({ open, onOpenChange, onStop, onPause, onResume }) {
  const botDetail = useStore(s => s.botDetail);
  const selectedBotId = useStore(s => s.selectedBotId);
  const setSelectedBotId = useStore(s => s.setSelectedBotId);
  const setBotDetail = useStore(s => s.setBotDetail);
  const agentInsights = useStore(s => s.agentInsights);
  const agentInsightHistory = useStore(s => s.agentInsightHistory);

  const bot = botDetail?.bot;
  const position = botDetail?.position;
  const stats = botDetail?.stats;
  const trades = botDetail?.trades ?? [];
  const snapshots = botDetail?.snapshots ?? [];
  const strategyMeta = bot ? getStrategyMeta(bot.strategy) : null;
  const loading = open && selectedBotId && !bot;

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

  const [savingRisk, setSavingRisk] = useState(false);
  const [tpPercent, setTpPercent] = useState('');
  const [slPercent, setSlPercent] = useState('');

  useEffect(() => {
    if (!bot) return;
    const cfg = bot.config || {};
    setTpPercent(
      cfg.take_profit_percent != null ? String(cfg.take_profit_percent) : '',
    );
    setSlPercent(
      cfg.trailing_stop_percent != null
        ? String(cfg.trailing_stop_percent)
        : cfg.stop_loss_percent != null
          ? String(cfg.stop_loss_percent)
          : '',
    );
  }, [bot?.id, bot?.config]);

  const saveRiskConfig = async () => {
    if (!selectedBotId) return;
    setSavingRisk(true);
    const config = {};
    const tp = parseFloat(tpPercent);
    const sl = parseFloat(slPercent);
    if (!Number.isNaN(tp) && tp > 0) config.take_profit_percent = tp;
    else config.take_profit_percent = null;
    if (!Number.isNaN(sl) && sl > 0) config.trailing_stop_percent = sl;
    else config.trailing_stop_percent = null;
    try {
      await sendAction(Action.BOT_UPDATE_CONFIG, { bot_id: selectedBotId, config });
    } finally {
      setSavingRisk(false);
    }
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
        className={cn('bot-detail-drawer sm:max-w-none', resizing && 'bot-detail-drawer--resizing')}
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
        <SheetHeader className="bot-detail-drawer__header">
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
                    {bot.timeframe}
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
          <div className="bot-detail-drawer__body">
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

            {(position || bot.status === 'RUNNING' || bot.status === 'PAUSED') && (
              <section className="bot-detail-drawer__risk" aria-label="Position risk">
                <header className="bot-detail-drawer__trades-header">
                  <span>Take profit / stop loss</span>
                  {position && (
                    <Badge variant="secondary">
                      {Number(position.size).toFixed(4)} @ {Number(position.avg_price).toFixed(2)}
                    </Badge>
                  )}
                </header>
                {position?.take_profit_price != null && (
                  <p className="bot-detail-drawer__risk-active text-xs text-muted-foreground mb-2">
                    Active TP: {Number(position.take_profit_price).toFixed(4)}
                    {position.take_profit_percent != null && (
                      <> ({Number(position.take_profit_percent).toFixed(2)}%)</>
                    )}
                  </p>
                )}
                <div className="bot-detail-drawer__risk-form grid grid-cols-2 gap-2">
                  <div className="space-y-1">
                    <Label htmlFor="bot-tp-pct" className="text-xs">Take profit %</Label>
                    <Input
                      id="bot-tp-pct"
                      type="number"
                      min="0"
                      step="0.01"
                      placeholder="e.g. 3"
                      value={tpPercent}
                      onChange={(e) => setTpPercent(e.target.value)}
                      className="h-8 text-xs"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="bot-sl-pct" className="text-xs">Trailing stop %</Label>
                    <Input
                      id="bot-sl-pct"
                      type="number"
                      min="0"
                      step="0.01"
                      placeholder="e.g. 1.5"
                      value={slPercent}
                      onChange={(e) => setSlPercent(e.target.value)}
                      className="h-8 text-xs"
                    />
                  </div>
                </div>
                <Button
                  variant="outline"
                  size="xs"
                  className="mt-2"
                  disabled={savingRisk}
                  onClick={saveRiskConfig}
                >
                  {savingRisk ? <Loader2 className="size-3 animate-spin" /> : null}
                  Save risk settings
                </Button>
              </section>
            )}

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

            {bot?.config && Object.keys(bot.config).length > 0 && (
              <details className="bot-detail-drawer__config">
                <summary>Strategy config</summary>
                <pre className="algo-config-preview">{JSON.stringify(bot.config, null, 2)}</pre>
              </details>
            )}

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
                      trades.map(t => (
                        <tr key={t.id ?? `${t.timestamp}-${t.side}-${t.price}`}>
                          <td className="bot-detail-trades-table__time" colSpan={6}>
                            <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
                              <span title={formatTradeTime(t.timestamp)}>{formatTradeTime(t.timestamp)}</span>
                              <span className={cn(
                                'bot-detail-trades-table__side',
                                t.side === 'BUY' ? 'text-trading-up' : 'text-trading-down',
                              )}>
                                {t.side}{t.is_exit ? ' exit' : ''}
                              </span>
                              <span className="num-mono">{Number(t.quantity).toFixed(4)} @ {Number(t.price).toFixed(4)}</span>
                              <span className={cn(
                                'num-mono',
                                t.pnl != null && (t.pnl >= 0 ? 'text-trading-up' : 'text-trading-down'),
                              )}>
                                {t.pnl != null ? `$${Number(t.pnl).toFixed(2)}` : '—'}
                              </span>
                              <span className="num-mono text-muted-foreground" title={t.order_id}>
                                {t.order_id ? shortBotId(t.order_id) : '—'}
                              </span>
                            </div>
                            <TradeExplain
                              trade={t}
                              symbol={bot?.symbol}
                              botStrategy={bot?.strategy}
                              agentInsights={agentInsights}
                              agentInsightHistory={agentInsightHistory}
                            />
                          </td>
                        </tr>
                      ))
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
