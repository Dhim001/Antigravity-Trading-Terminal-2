import React, { useState, useRef, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { fetchAgentInsights } from '../api/endpoints';
import TradeExplainCard from './TradeExplainCard';
import BotSnapshotChart from './BotSnapshotChart';
import BotConfigPanel from './BotConfigPanel';
import BotCalibrationPanel from './BotCalibrationPanel';
import { StatCard } from '@/components/StatCard';
import { cn } from '@/lib/utils';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetFooter,
} from '@/components/ui/sheet';
import { Card, CardContent } from '@/components/ui/card';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import StrategyBadge from './StrategyBadge';
import { getStrategyMeta } from '@/config/strategies';
import { parseTradeTimestamp, shortBotId } from '@/lib/botAttribution';
import { formatBarTimeframeLabel } from '@/lib/barTimeframes';
import { openBacktestLabWithRun } from '@/lib/backtestLab';
import { backtestFingerprint } from '@/lib/backtestDisplay';
import {
  Pause,
  PlayCircle,
  OctagonX,
  Loader2,
  GripVertical,
  GripHorizontal,
  TrendingUp,
  History,
  ChevronRight,
  ChevronsDownUp,
  Maximize2,
  Minimize2,
  FlaskConical,
  AlertTriangle,
} from 'lucide-react';

const DRAWER_WIDTH_KEY = 'terminal_bot_drawer_width';
const DRAWER_HEIGHT_KEY = 'terminal_bot_drawer_height';
const DRAWER_TOP_KEY = 'terminal_bot_drawer_top';
const DRAWER_DETAILS_KEY = 'terminal_bot_drawer_details_open';
const DRAWER_TRADES_KEY = 'terminal_bot_drawer_trades_open';
const DRAWER_FULLSCREEN_KEY = 'terminal_bot_drawer_fullscreen';
const DRAWER_WIDTH_DEFAULT = 480;
const DRAWER_WIDTH_MIN = 320;
const DRAWER_WIDTH_MAX = 1100;
const DRAWER_HEIGHT_MIN = 420;

function readDrawerWidth() {
  try {
    const n = parseInt(localStorage.getItem(DRAWER_WIDTH_KEY), 10);
    if (!Number.isNaN(n)) return Math.min(DRAWER_WIDTH_MAX, Math.max(DRAWER_WIDTH_MIN, n));
  } catch (_) {}
  return DRAWER_WIDTH_DEFAULT;
}

function readStoredBool(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    if (v !== null) return v === 'true';
  } catch (_) {}
  return fallback;
}

function readDrawerHeight() {
  try {
    const n = parseInt(localStorage.getItem(DRAWER_HEIGHT_KEY), 10);
    if (!Number.isNaN(n) && n >= DRAWER_HEIGHT_MIN) {
      return Math.min(window.innerHeight - 24, n);
    }
  } catch (_) {}
  return null;
}

function readDrawerTop() {
  try {
    const n = parseInt(localStorage.getItem(DRAWER_TOP_KEY), 10);
    if (!Number.isNaN(n) && n >= 0) return n;
  } catch (_) {}
  return 0;
}

function maxDrawerHeight(top = 0) {
  return Math.max(DRAWER_HEIGHT_MIN, window.innerHeight - top - 12);
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

function BotDrawerSection({
  id,
  title,
  badge,
  defaultOpen = false,
  scrollable = false,
  children,
  className,
}) {
  const storageKey = `bot_drawer_section_${id}`;
  const [open, setOpen] = useState(() => readStoredBool(storageKey, defaultOpen));

  useEffect(() => {
    try { localStorage.setItem(storageKey, String(open)); } catch (_) {}
  }, [open, storageKey]);

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className={cn(
        'bot-drawer-section',
        scrollable && 'bot-drawer-section--scrollable',
        className,
      )}
    >
      <CollapsibleTrigger asChild>
        <button type="button" className="bot-drawer-section__trigger">
          <ChevronRight
            className={cn('bot-drawer-section__chevron', open && 'bot-drawer-section__chevron--open')}
            aria-hidden
          />
          <span className="bot-drawer-section__title">{title}</span>
          {badge != null && (
            <Badge variant="secondary" className="bot-drawer-section__badge">
              {badge}
            </Badge>
          )}
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent className="bot-drawer-section__content">
        <div className="bot-drawer-section__body">{children}</div>
      </CollapsibleContent>
    </Collapsible>
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
  const hasOpenPosition = position && Number(position.size) > 0;

  const [detailsOpen, setDetailsOpen] = useState(() => readStoredBool(DRAWER_DETAILS_KEY, true));
  const [tradesOpen, setTradesOpen] = useState(() => readStoredBool(DRAWER_TRADES_KEY, true));

  useEffect(() => {
    if (!open || !bot?.symbol || bot.strategy !== 'CHART_AGENT') return;
    fetchAgentInsights(bot.symbol, { setAgentInsightHistory }, 80).catch(() => {});
  }, [open, bot?.id, bot?.symbol, bot?.strategy, setAgentInsightHistory]);

  useEffect(() => {
    try { localStorage.setItem(DRAWER_DETAILS_KEY, String(detailsOpen)); } catch (_) {}
  }, [detailsOpen]);

  useEffect(() => {
    try { localStorage.setItem(DRAWER_TRADES_KEY, String(tradesOpen)); } catch (_) {}
  }, [tradesOpen]);

  const [drawerWidth, setDrawerWidth] = useState(readDrawerWidth);
  const [drawerHeight, setDrawerHeight] = useState(readDrawerHeight);
  const [drawerTop, setDrawerTop] = useState(readDrawerTop);
  const [fullscreen, setFullscreen] = useState(() => readStoredBool(DRAWER_FULLSCREEN_KEY, false));
  const [resizing, setResizing] = useState(false);
  const [dragging, setDragging] = useState(false);
  const dragMode = useRef(null);
  const startX = useRef(0);
  const startY = useRef(0);
  const startW = useRef(0);
  const startH = useRef(0);
  const startTop = useRef(0);

  useEffect(() => {
    try { localStorage.setItem(DRAWER_WIDTH_KEY, String(drawerWidth)); } catch (_) {}
  }, [drawerWidth]);

  useEffect(() => {
    try {
      if (drawerHeight == null) localStorage.removeItem(DRAWER_HEIGHT_KEY);
      else localStorage.setItem(DRAWER_HEIGHT_KEY, String(drawerHeight));
    } catch (_) {}
  }, [drawerHeight]);

  useEffect(() => {
    try { localStorage.setItem(DRAWER_TOP_KEY, String(drawerTop)); } catch (_) {}
  }, [drawerTop]);

  useEffect(() => {
    try { localStorage.setItem(DRAWER_FULLSCREEN_KEY, String(fullscreen)); } catch (_) {}
  }, [fullscreen]);

  const beginDrag = useCallback((mode, e) => {
    e.preventDefault();
    e.stopPropagation();
    dragMode.current = mode;
    setResizing(mode === 'width' || mode === 'height');
    setDragging(mode === 'move');
    startX.current = e.clientX;
    startY.current = e.clientY;
    startW.current = drawerWidth;
    startH.current = drawerHeight ?? maxDrawerHeight(drawerTop);
    startTop.current = drawerTop;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = mode === 'width'
      ? 'ew-resize'
      : mode === 'height'
        ? 'ns-resize'
        : 'grabbing';
  }, [drawerWidth, drawerHeight, drawerTop]);

  const onWidthResizeMouseDown = useCallback((e) => beginDrag('width', e), [beginDrag]);
  const onHeightResizeMouseDown = useCallback((e) => beginDrag('height', e), [beginDrag]);
  const onHeaderMouseDown = useCallback((e) => {
    if (fullscreen || nested) return;
    if (e.target.closest('button, a, input, select, textarea, [data-slot="sheet-close"]')) return;
    beginDrag('move', e);
  }, [beginDrag, fullscreen, nested]);

  useEffect(() => {
    const onMove = (e) => {
      const mode = dragMode.current;
      if (!mode) return;
      if (mode === 'width') {
        const delta = startX.current - e.clientX;
        setDrawerWidth(Math.min(DRAWER_WIDTH_MAX, Math.max(DRAWER_WIDTH_MIN, startW.current + delta)));
        return;
      }
      if (mode === 'height') {
        const delta = e.clientY - startY.current;
        const maxH = maxDrawerHeight(startTop.current);
        setDrawerHeight(Math.min(maxH, Math.max(DRAWER_HEIGHT_MIN, startH.current + delta)));
        return;
      }
      if (mode === 'move') {
        const deltaY = e.clientY - startY.current;
        const nextTop = Math.max(0, Math.min(window.innerHeight - DRAWER_HEIGHT_MIN, startTop.current + deltaY));
        setDrawerTop(nextTop);
        setDrawerHeight((h) => {
          const current = h ?? startH.current;
          const maxH = maxDrawerHeight(nextTop);
          return Math.min(current, maxH);
        });
      }
    };
    const onUp = () => {
      if (!dragMode.current) return;
      dragMode.current = null;
      setResizing(false);
      setDragging(false);
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

  const totalPnl = Number(stats?.total_pnl ?? 0);
  const dailyPnl = Number(stats?.daily_pnl ?? 0);
  const panelHeight = fullscreen ? null : (drawerHeight ?? maxDrawerHeight(drawerTop));

  return (
    <Sheet open={open} onOpenChange={(v) => { if (!v) handleClose(); else onOpenChange(v); }}>
      <SheetContent
        side="right"
        overlayClassName={nested ? 'terminal-sheet-overlay--nested' : undefined}
        className={cn(
          'terminal-sheet bot-detail-drawer sm:max-w-none',
          nested && 'terminal-sheet--nested',
          (resizing || dragging) && 'bot-detail-drawer--resizing',
          !detailsOpen && 'bot-detail-drawer--details-collapsed',
          fullscreen && 'bot-detail-drawer--fullscreen',
        )}
        style={fullscreen ? {
          width: 'min(1100px, 96vw)',
          maxWidth: '96vw',
          '--bot-drawer-top': '24px',
          '--bot-drawer-height': 'calc(100dvh - 48px)',
        } : {
          width: `${drawerWidth}px`,
          maxWidth: 'min(96vw, 100%)',
          '--bot-drawer-top': nested ? '0px' : `${drawerTop}px`,
          '--bot-drawer-height': panelHeight ? `${panelHeight}px` : '100dvh',
        }}
      >
        {!fullscreen && !nested && (
          <div
            className={cn('bot-detail-drawer__resize', resizing && 'dragging')}
            onMouseDown={onWidthResizeMouseDown}
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize bot detail panel width"
            title="Drag to resize width"
          >
            <span className="bot-detail-drawer__resize-grip" aria-hidden>
              <GripVertical />
            </span>
          </div>
        )}

        {!nested && (
          <div className="bot-detail-drawer__header-tools">
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="bot-detail-drawer__expand-btn shrink-0"
              onClick={() => setFullscreen((v) => !v)}
              title={fullscreen ? 'Exit expanded view' : 'Expand panel'}
            >
              {fullscreen ? <Minimize2 aria-hidden /> : <Maximize2 aria-hidden />}
            </Button>
          </div>
        )}

        <SheetHeader
          className={cn(
            'terminal-sheet__header bot-detail-drawer__header',
            !nested && !fullscreen && 'bot-detail-drawer__header--draggable',
            dragging && 'bot-detail-drawer__header--dragging',
          )}
          onMouseDown={onHeaderMouseDown}
        >
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
                  {bot.config?.backtest_run_id && (
                    <div className="bot-detail-drawer__source-backtest mt-1.5 flex flex-wrap items-center gap-1.5">
                      <Button
                        type="button"
                        variant="outline"
                        size="xs"
                        className="h-6 gap-1 text-[0.62rem]"
                        onClick={() => {
                          openBacktestLabWithRun(bot.config.backtest_run_id).catch(() => {
                            toast.error('Could not load source backtest run');
                          });
                        }}
                      >
                        <FlaskConical className="size-3" aria-hidden />
                        Source backtest
                      </Button>
                      {bot.config.deploy_gate_passed_at && (
                        <Badge variant="secondary" className="text-[0.55rem] font-normal">
                          Gate passed
                        </Badge>
                      )}
                    </div>
                  )}
                  {bot.config?.backtest_fingerprint && bot.config && (() => {
                    const current = backtestFingerprint({
                      symbol: bot.symbol,
                      strategy: bot.strategy,
                      days: String(bot.config.backtest_days || '7'),
                      timeframe: bot.timeframe,
                      config: bot.config,
                    });
                    if (current !== bot.config.backtest_fingerprint) {
                      return (
                        <Alert variant="default" className="mt-2 py-1.5 px-2">
                          <AlertTriangle className="size-3.5" />
                          <AlertDescription className="text-[0.58rem] leading-snug">
                            Config drift — parameters differ from the linked backtest snapshot.
                          </AlertDescription>
                        </Alert>
                      );
                    }
                    return null;
                  })()}
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
          <div className="terminal-sheet__body bot-detail-drawer__shell">
            <div className="bot-detail-drawer__stats-strip">
              <div className="bot-detail-drawer__stats-toolbar">
                <span className="bot-detail-drawer__stats-label">Performance</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  className="bot-detail-drawer__collapse-btn"
                  onClick={() => setDetailsOpen((v) => !v)}
                  aria-expanded={detailsOpen}
                >
                  <ChevronsDownUp data-icon="inline-start" />
                  {detailsOpen ? 'Hide setup' : 'Show setup'}
                </Button>
              </div>

              {hasOpenPosition && (
                <Alert className="bot-detail-drawer__position-alert">
                  <TrendingUp aria-hidden />
                  <AlertTitle>Open position</AlertTitle>
                  <AlertDescription>
                    <span className="num-mono">
                      {Number(position.size).toFixed(4)} @ {Number(position.avg_price).toFixed(4)}
                    </span>
                    {position.take_profit_price != null && (
                      <>
                        {' · TP '}
                        <span className="num-mono">{Number(position.take_profit_price).toFixed(4)}</span>
                      </>
                    )}
                  </AlertDescription>
                </Alert>
              )}

              <div className="algo-backtest-stat-grid algo-backtest-stat-grid--compact bot-detail-drawer__stats-grid">
                <StatCard label="Executions" value={String(stats.trade_count ?? 0)} />
                <StatCard label="Exits" value={String(stats.exit_count ?? '—')} />
                <StatCard
                  label="Win rate"
                  value={stats.win_rate != null ? `${stats.win_rate}%` : '—'}
                  tone={stats.win_rate >= 50 ? 'up' : stats.win_rate != null ? 'down' : 'neutral'}
                />
                <StatCard
                  label="Total PnL"
                  value={`$${totalPnl.toFixed(2)}`}
                  tone={totalPnl >= 0 ? 'up' : 'down'}
                />
                <StatCard
                  label="Today"
                  value={`$${dailyPnl.toFixed(2)}`}
                  tone={dailyPnl >= 0 ? 'up' : 'down'}
                />
              </div>
            </div>

            <div className="bot-detail-drawer__main">
              <Collapsible
                open={detailsOpen}
                onOpenChange={setDetailsOpen}
                className="bot-detail-drawer__details"
              >
                <CollapsibleContent className="bot-detail-drawer__details-content">
                  <ScrollArea className="bot-detail-drawer__details-scroll">
                    <div className="bot-detail-drawer__details-inner">
                      <BotConfigPanel
                        botId={bot.id}
                        strategy={bot.strategy}
                        config={bot.config}
                        botStatus={bot.status}
                        botTimeframe={bot.timeframe}
                        position={position}
                      />

                      {bot.strategy === 'CHART_AGENT' && (
                        <BotDrawerSection
                          id="calibration"
                          title="Calibration & filters"
                          defaultOpen={false}
                          scrollable
                          className="bot-drawer-section--calibration"
                        >
                          <BotCalibrationPanel
                            botId={bot.id}
                            symbol={bot.symbol}
                            strategy={bot.strategy}
                            className="bot-calibration-panel"
                          />
                        </BotDrawerSection>
                      )}

                      <BotDrawerSection
                        id="equity"
                        title="Equity curve"
                        badge={snapshots.length}
                        defaultOpen={false}
                        className="bot-drawer-section--equity"
                      >
                        <div className="bot-detail-drawer__equity-body">
                          <BotSnapshotChart snapshots={snapshots} allocation={bot.allocation} />
                        </div>
                      </BotDrawerSection>
                    </div>
                  </ScrollArea>
                </CollapsibleContent>
              </Collapsible>

              <Collapsible
                open={tradesOpen}
                onOpenChange={setTradesOpen}
                className="bot-detail-drawer__trades-card"
              >
              <CollapsibleTrigger asChild>
                <button type="button" className="bot-detail-drawer__trades-trigger">
                  <ChevronRight
                    className={cn(
                      'bot-drawer-section__chevron',
                      tradesOpen && 'bot-drawer-section__chevron--open',
                    )}
                    aria-hidden
                  />
                  <span className="bot-drawer-section__title">Recent fills</span>
                  <Badge variant="secondary" className="bot-drawer-section__badge">
                    {trades.length}
                  </Badge>
                </button>
              </CollapsibleTrigger>
              <CollapsibleContent className="bot-detail-drawer__trades-content">
                <Card size="sm" className="bot-detail-drawer__trades-panel">
                  <CardContent className="bot-detail-drawer__trades-body">
                    {trades.length === 0 ? (
                      <Empty className="bot-detail-drawer__trades-empty border-0">
                        <EmptyHeader>
                          <EmptyMedia variant="icon">
                            <History aria-hidden />
                          </EmptyMedia>
                          <EmptyTitle>No trades yet</EmptyTitle>
                          <EmptyDescription>
                            Fills and trade explanations appear here once the bot executes.
                          </EmptyDescription>
                        </EmptyHeader>
                      </Empty>
                    ) : (
                      <ScrollArea className="bot-detail-drawer__trades-scroll">
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
                            {trades.map((t) => {
                              const showExplain = t.id != null;
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
                                        <TradeExplainCard
                                          trade={t}
                                          symbol={bot?.symbol}
                                          botId={bot?.id}
                                          botStrategy={bot?.strategy}
                                          botTimeframe={bot?.timeframe}
                                          agentInsights={agentInsights}
                                          agentInsightHistory={agentInsightHistory}
                                          useLlm={Boolean(bot?.config?.use_llm)}
                                        />
                                      </td>
                                    </tr>
                                  )}
                                </React.Fragment>
                              );
                            })}
                          </tbody>
                        </table>
                      </ScrollArea>
                    )}
                  </CardContent>
                </Card>
              </CollapsibleContent>
            </Collapsible>
            </div>

            {!fullscreen && !nested && (
              <div
                className={cn('bot-detail-drawer__resize-height', resizing && 'dragging')}
                onMouseDown={onHeightResizeMouseDown}
                role="separator"
                aria-orientation="horizontal"
                aria-label="Resize bot detail panel height"
                title="Drag to resize height"
              >
                <span className="bot-detail-drawer__resize-grip bot-detail-drawer__resize-grip--horizontal" aria-hidden>
                  <GripHorizontal />
                </span>
              </div>
            )}

            <SheetFooter className="bot-detail-drawer__footer">
              {bot.status === 'RUNNING' && (
                <>
                  <Button variant="outline" size="sm" onClick={() => onPause(bot.id)}>
                    <Pause data-icon="inline-start" />
                    Pause
                  </Button>
                  <Button variant="outline" size="sm" onClick={refresh}>
                    Refresh
                  </Button>
                </>
              )}
              {bot.status === 'PAUSED' && (
                <Button variant="outline" size="sm" onClick={() => onResume(bot.id)}>
                  <PlayCircle data-icon="inline-start" />
                  Resume
                </Button>
              )}
              {bot.status !== 'STOPPED' && (
                <Button variant="destructive" size="sm" onClick={() => onStop(bot.id)}>
                  <OctagonX data-icon="inline-start" />
                  Stop
                </Button>
              )}
            </SheetFooter>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
