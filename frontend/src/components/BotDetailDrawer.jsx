import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { cn } from '@/lib/utils';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import StrategyBadge from './StrategyBadge';
import { getStrategyMeta } from '@/config/strategies';
import { parseTradeTimestamp, shortBotId } from '@/lib/botAttribution';
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

export default function BotDetailDrawer({ open, onOpenChange, onStop, onPause, onResume }) {
  const botDetail = useStore(s => s.botDetail);
  const selectedBotId = useStore(s => s.selectedBotId);
  const setSelectedBotId = useStore(s => s.setSelectedBotId);
  const setBotDetail = useStore(s => s.setBotDetail);

  const bot = botDetail?.bot;
  const stats = botDetail?.stats;
  const trades = botDetail?.trades ?? [];
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
                          <td className="bot-detail-trades-table__time" title={formatTradeTime(t.timestamp)}>
                            {formatTradeTime(t.timestamp)}
                          </td>
                          <td>
                            <span className={cn(
                              'bot-detail-trades-table__side',
                              t.side === 'BUY' ? 'text-trading-up' : 'text-trading-down',
                            )}>
                              {t.side}{t.is_exit ? ' exit' : ''}
                            </span>
                          </td>
                          <td className="num-mono text-right">{Number(t.quantity).toFixed(4)}</td>
                          <td className="num-mono text-right">{Number(t.price).toFixed(4)}</td>
                          <td className={cn(
                            'num-mono text-right',
                            t.pnl != null && (t.pnl >= 0 ? 'text-trading-up' : 'text-trading-down'),
                          )}>
                            {t.pnl != null ? `$${Number(t.pnl).toFixed(2)}` : '—'}
                          </td>
                          <td className="num-mono bot-detail-trades-table__order" title={t.order_id}>
                            {t.order_id ? shortBotId(t.order_id) : '—'}
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
