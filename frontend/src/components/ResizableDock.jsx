/**
 * ResizableDock.jsx
 * Bottom docked panel with tabs:
 *   Positions | Orders | Balances | Algo Bot | Analyst | Bot History | Ticks | History | Equity Curve
 *
 * Features:
 *  - Drag-to-resize via top handle (persists to localStorage)
 *  - History tab can be expanded to full-screen overlay
 *  - Badge counts on Positions and Orders tabs
 */
import React, { useState, useRef, useEffect, useCallback, useMemo, Suspense, lazy } from 'react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { useSettingsStore } from '../store/useSettingsStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import {
  Briefcase, List, Landmark, Cpu, Activity, TrendingUp,
  Play, Settings, Trash2, XSquare, Maximize2, Minimize2, ShieldAlert, Pause, PlayCircle, OctagonX,
  RefreshCw, AlertTriangle, Zap, History, Brain, Radar,
} from 'lucide-react';
import EquityCurveTab from './EquityCurveTab';
import TradeHistoryContent from './TradeHistoryPanel';
import BacktestResultsPanel from './BacktestResultsPanel';
import BotDetailDrawer from './BotDetailDrawer';
import ReconciliationTab from './ReconciliationTab';
import ErrorBoundary from './ErrorBoundary';
import StrategyTemplateCard from './StrategyTemplateCard';
import StrategyBadge from './StrategyBadge';
import { WidgetEmpty, ScrollTablePanel } from './WidgetShell';

const TickViewerTab = lazy(() => import('./TickViewerTab'));
const BotHistoryTab = lazy(() => import('./BotHistoryTab'));
const AnalystTab = lazy(() => import('./AnalystTab'));
const ScannerTab = lazy(() => import('./ScannerTab'));

function DockTabFallback() {
  return <WidgetEmpty message="Loading tab…" />;
}
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import { Label } from '@/components/ui/label';
import {
  InputGroup, InputGroupAddon, InputGroupInput, InputGroupText,
} from '@/components/ui/input-group';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { cn } from '@/lib/utils';
import { formatLastSignal } from '@/lib/formatTime';
import { buildBotLookup, getPositionBots, shortBotId } from '@/lib/botAttribution';
import { DOCK_GROUP_CONFIG, dockGroupForTab } from '../settings/layoutModes';

const DOCK_MIN = 200;
const DOCK_MAX = 560;
const DOCK_DEFAULT = 320;
const STORAGE_KEY = 'terminal_dock_height';

const DOCK_TAB_IDS = new Set([
  'positions', 'orders', 'balances', 'algo', 'scanner', 'analyst',
  'reconcile', 'bots', 'ticks', 'history', 'equity',
]);

function normalizeDockTab(tab) {
  return DOCK_TAB_IDS.has(tab) ? tab : 'positions';
}

// ── Tiny formatters ───────────────────────────────────────────────
const priceDecimals = (sym, price) =>
  (sym?.includes('XRP') || sym?.includes('ADA') || sym?.includes('DOGE') || (price != null && price < 2.0)) ? 4 : 2;

const fmtP = (n, d = 2) =>
  n == null ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

const QUOTE_ASSETS = new Set(['USD', 'USDT']);

const assetFromSymbol = (sym) =>
  sym.includes('USDT') && sym !== 'USDT' ? sym.replace('USDT', '') : sym;

/** Binance maps USD → USDT; skip duplicate row/total when values match. */
const isQuoteAlias = (usd, usdt) =>
  Boolean(usd && usdt && usd.balance === usdt.balance && usd.locked === usdt.locked);

function buildBalanceView(balances, assetMark) {
  const usd = balances.USD;
  const usdt = balances.USDT;
  const alias = isQuoteAlias(usd, usdt);

  let cashAvailable = 0;
  let cashLocked = 0;
  if (usdt) {
    cashAvailable += usdt.balance - usdt.locked;
    cashLocked += usdt.locked;
  } else if (usd) {
    cashAvailable += usd.balance - usd.locked;
    cashLocked += usd.locked;
  }
  if (usd && !alias && usdt) {
    cashAvailable += usd.balance - usd.locked;
    cashLocked += usd.locked;
  }

  let holdingsUsd = 0;
  let totalEquity = 0;
  const rows = [];

  for (const [asset, bal] of Object.entries(balances)) {
    if (asset === 'USD' && alias) continue;
    if (Math.abs(bal.balance) < 1e-8 && bal.locked === 0) continue;

    const avail = bal.balance - bal.locked;
    const isQuote = QUOTE_ASSETS.has(asset);
    const mark = isQuote ? 1 : assetMark[asset];
    const usdValue = mark != null ? bal.balance * mark : null;

    if (usdValue != null) totalEquity += usdValue;
    if (!isQuote && usdValue != null) holdingsUsd += usdValue;

    rows.push({ asset, bal, avail, usdValue, isQuote });
  }

  rows.sort((a, b) => {
    if (a.isQuote !== b.isQuote) return a.isQuote ? -1 : 1;
    return (b.usdValue ?? 0) - (a.usdValue ?? 0);
  });

  return { rows, stats: { cashAvailable, cashLocked, holdingsUsd, totalEquity } };
}

// ── Position Row ──────────────────────────────────────────────────
const PositionRow = React.memo(function PositionRow({ sym, pos, ownerBots = [] }) {
  const mark = useStore(state => state.tickerData[sym]?.price ?? pos.avg_price);
  const activeSymbol = useStore(state => state.activeSymbol);

  const handleClose = () => {
    sendAction(Action.PLACE_ORDER, {
      symbol: sym,
      type: 'MARKET',
      side: pos.size > 0 ? 'SELL' : 'BUY',
      quantity: Math.abs(pos.size),
    });
  };

  const uPnl = pos.size * (mark - pos.avg_price);
  const pct  = pos.avg_price > 0 ? ((mark - pos.avg_price) / pos.avg_price) * 100 : 0;
  const isLong = pos.size >= 0;
  const dec = priceDecimals(sym, Math.max(mark, pos.avg_price));
  const isActive = sym === activeSymbol;

  return (
    <tr className={cn(isActive && 'row-active')}>
      <td>
        <span className={cn('font-bold', isActive ? 'text-primary' : 'text-foreground')}>{sym}</span>
        {ownerBots.length > 0 && (
          <div className="mt-0.5 flex flex-wrap gap-1">
            {ownerBots.map((bot) => (
              <span key={bot.id} className="inline-flex items-center">
                <StrategyBadge strategy={bot.strategy} compact />
                <span className="ml-1 text-[0.58rem] text-muted-foreground num-mono" title={bot.id}>
                  {shortBotId(bot.id)}
                  {bot._size != null && (
                    <span className="ml-0.5 opacity-70">({Math.abs(bot._size).toFixed(3)})</span>
                  )}
                </span>
              </span>
            ))}
          </div>
        )}
        {(pos.stop_loss_price || pos.take_profit_price) && (
          <div className="mt-0.5 icon-label-tight text-[0.62rem] text-muted-foreground">
            {pos.stop_loss_price && (
              <span className="text-trading-down">SL:{pos.stop_loss_price.toFixed(dec)}</span>
            )}
            {pos.take_profit_price && (
              <span className="text-trading-up">TP:{pos.take_profit_price.toFixed(dec)}</span>
            )}
          </div>
        )}
      </td>
      <td>
        <Badge variant={isLong ? 'buy' : 'sell'}>{isLong ? 'LONG' : 'SHORT'}</Badge>
      </td>
      <td className="num-mono text-right">
        {Math.abs(pos.size).toLocaleString(undefined, { minimumFractionDigits: 4 })}
      </td>
      <td className="num-mono text-right">
        {pos.avg_price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
      </td>
      <td className="num-mono text-right">
        {mark.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
      </td>
      <td className={cn('num-mono text-right font-bold', uPnl >= 0 ? 'text-trading-up' : 'text-trading-down')}>
        {uPnl >= 0 ? '+' : ''}{fmtP(uPnl)}
      </td>
      <td className={cn('num-mono text-right font-semibold', pct >= 0 ? 'text-trading-up' : 'text-trading-down')}>
        {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
      </td>
      <td className="text-center">
        <Button variant="destructive" size="xs" onClick={handleClose} title={`Close ${sym} position`}>
          CLOSE
        </Button>
      </td>
    </tr>
  );
});

// ── Positions Tab ─────────────────────────────────────────────────
function PositionsTab() {
  const positions = useStore(state => state.positions);
  const tickerData = useStore(state => state.tickerData);
  const activeBots = useStore(state => state.activeBots);
  const tradeHistory = useStore(state => state.tradeHistory);
  const entries = Object.entries(positions);

  const stats = useMemo(() => {
    let totalPnl = 0;
    let longCount = 0;
    let shortCount = 0;
    for (const [sym, pos] of entries) {
      const mark = tickerData[sym]?.price ?? pos.avg_price;
      totalPnl += pos.size * (mark - pos.avg_price);
      if (pos.size >= 0) longCount += 1;
      else shortCount += 1;
    }
    return { totalPnl, longCount, shortCount };
  }, [entries, tickerData]);

  const botCtx = { activeBots, tradeHistory };
  const pnlPositive = stats.totalPnl >= 0;

  return (
    <div className="dock-panel-tab">
      <header className="dock-panel-tab__toolbar">
        <div className="dock-panel-tab__toolbar-lead">
          <div className="dock-panel-tab__toolbar-icon" aria-hidden>
            <Briefcase size={14} />
          </div>
          <div className="dock-panel-tab__toolbar-copy">
            <span className="dock-panel-tab__toolbar-title">Open Positions</span>
            <span className="dock-panel-tab__toolbar-subtitle num-mono">
              {entries.length} position{entries.length === 1 ? '' : 's'}
              {entries.length > 0 && (
                <> · {stats.longCount}L / {stats.shortCount}S</>
              )}
            </span>
          </div>
        </div>
        {entries.length > 0 && (
          <div className="dock-panel-tab__toolbar-meta">
            <span className="dock-panel-tab__meta-label">Unrealized</span>
            <span
              className={cn(
                'dock-panel-tab__meta-value num-mono',
                pnlPositive ? 'dock-panel-tab__meta-value--up' : 'dock-panel-tab__meta-value--down',
              )}
            >
              {pnlPositive ? '+' : ''}${fmtP(stats.totalPnl)}
            </span>
          </div>
        )}
      </header>

      {entries.length === 0 ? (
        <div className="dock-panel-tab__empty">
          <WidgetEmpty icon={Briefcase} message="No open positions" />
        </div>
      ) : (
        <>
          <div className="dock-panel-tab__table-wrap scroll-panel-y scroll-panel-y-0">
            <table className="terminal-table dock-panel-tab__table min-w-[880px]">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th className="text-right">Size</th>
                  <th className="text-right">Avg Entry</th>
                  <th className="text-right">Mark Price</th>
                  <th className="text-right">Unrealized P&L</th>
                  <th className="text-right">% Return</th>
                  <th className="text-center">Close</th>
                </tr>
              </thead>
              <tbody>
                {entries.map(([sym, pos]) => (
                  <PositionRow
                    key={sym}
                    sym={sym}
                    pos={pos}
                    ownerBots={getPositionBots(sym, pos, botCtx)}
                  />
                ))}
              </tbody>
            </table>
          </div>

          <footer className="dock-panel-tab__footer">
            <span>
              {entries.length} open · {stats.longCount} long · {stats.shortCount} short
            </span>
            <span className="dock-panel-tab__footer-highlight">
              Total unrealized:{' '}
              <span
                className={cn(
                  'num-mono font-bold',
                  pnlPositive ? 'text-trading-up' : 'text-trading-down',
                )}
              >
                {pnlPositive ? '+' : ''}${fmtP(stats.totalPnl)}
              </span>
            </span>
          </footer>
        </>
      )}
    </div>
  );
}

// ── Orders Tab ────────────────────────────────────────────────────
function OrdersTab() {
  const orders = useStore(state => state.orders);
  const activeBots = useStore(state => state.activeBots);
  const { byId } = buildBotLookup(activeBots);
  const active = orders.filter(o => o.status === 'PENDING');

  const stats = useMemo(() => {
    let buyCount = 0;
    let sellCount = 0;
    let totalValue = 0;
    for (const ord of active) {
      if (ord.side === 'BUY') buyCount += 1;
      else sellCount += 1;
      totalValue += (ord.price || 0) * ord.quantity;
    }
    return { buyCount, sellCount, totalValue };
  }, [active]);

  return (
    <div className="dock-panel-tab">
      <header className="dock-panel-tab__toolbar">
        <div className="dock-panel-tab__toolbar-lead">
          <div className="dock-panel-tab__toolbar-icon" aria-hidden>
            <List size={14} />
          </div>
          <div className="dock-panel-tab__toolbar-copy">
            <span className="dock-panel-tab__toolbar-title">Pending Orders</span>
            <span className="dock-panel-tab__toolbar-subtitle num-mono">
              {active.length} order{active.length === 1 ? '' : 's'}
              {active.length > 0 && (
                <> · {stats.buyCount}B / {stats.sellCount}S</>
              )}
            </span>
          </div>
        </div>
        {active.length > 0 && (
          <div className="dock-panel-tab__toolbar-meta">
            <span className="dock-panel-tab__meta-label">Notional</span>
            <span className="dock-panel-tab__meta-value num-mono">
              ${fmtP(stats.totalValue)}
            </span>
          </div>
        )}
      </header>

      {active.length === 0 ? (
        <div className="dock-panel-tab__empty">
          <WidgetEmpty icon={List} message="No pending orders" />
        </div>
      ) : (
        <>
          <div className="dock-panel-tab__table-wrap scroll-panel-y scroll-panel-y-0">
            <table className="terminal-table dock-panel-tab__table min-w-[640px]">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Source</th>
                  <th>Type</th>
                  <th>Side</th>
                  <th className="text-right">Price</th>
                  <th className="text-right">Qty</th>
                  <th className="text-right">Value</th>
                  <th className="text-center">Cancel</th>
                </tr>
              </thead>
              <tbody>
                {active.map(ord => {
                  const dec = priceDecimals(ord.symbol, ord.price);
                  const isBuy = ord.side === 'BUY';
                  const value = (ord.price || 0) * ord.quantity;
                  const bot = ord.bot_id ? byId[ord.bot_id] : null;
                  return (
                    <tr key={ord.id}>
                      <td className="font-bold">{ord.symbol}</td>
                      <td className="text-xs">
                        {bot ? (
                          <StrategyBadge strategy={bot.strategy} compact />
                        ) : (
                          <span className="text-muted-foreground">Manual</span>
                        )}
                      </td>
                      <td className="text-xs text-secondary-foreground">{ord.type}</td>
                      <td><Badge variant={isBuy ? 'buy' : 'sell'}>{ord.side}</Badge></td>
                      <td className="num-mono text-right">
                        {ord.price ? ord.price.toFixed(dec) : 'MKT'}
                      </td>
                      <td className="num-mono text-right">
                        {ord.quantity.toLocaleString(undefined, { minimumFractionDigits: 4 })}
                      </td>
                      <td className="num-mono text-right text-secondary-foreground">
                        ${fmtP(value)}
                      </td>
                      <td className="text-center">
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => sendAction(Action.CANCEL_ORDER, { order_id: ord.id })}
                          title="Cancel order"
                          className="text-trading-down hover:text-trading-down"
                        >
                          <XSquare />
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <footer className="dock-panel-tab__footer">
            <span>
              {active.length} pending · {stats.buyCount} buy · {stats.sellCount} sell
            </span>
            <span className="dock-panel-tab__footer-highlight">
              Total notional:{' '}
              <span className="num-mono font-bold">${fmtP(stats.totalValue)}</span>
            </span>
          </footer>
        </>
      )}
    </div>
  );
}

// ── Balances Tab ──────────────────────────────────────────────────
function BalancesTab() {
  const balances = useStore(state => state.balances);
  const tickerData = useStore(state => state.tickerData);
  const symbolsList = useStore(state => state.symbolsList);

  const assetMark = useMemo(() => {
    const map = {};
    for (const sym of symbolsList || []) {
      const asset = assetFromSymbol(sym);
      const price = tickerData[sym]?.price;
      if (price != null) map[asset] = price;
    }
    return map;
  }, [symbolsList, tickerData]);

  const { rows, stats } = useMemo(
    () => buildBalanceView(balances, assetMark),
    [balances, assetMark],
  );

  return (
    <div className="dock-panel-tab">
      <header className="dock-panel-tab__toolbar">
        <div className="dock-panel-tab__toolbar-lead">
          <div className="dock-panel-tab__toolbar-icon" aria-hidden>
            <Landmark size={14} />
          </div>
          <div className="dock-panel-tab__toolbar-copy">
            <span className="dock-panel-tab__toolbar-title">Account Balances</span>
            <span className="dock-panel-tab__toolbar-subtitle num-mono">
              {rows.length} asset{rows.length === 1 ? '' : 's'}
            </span>
          </div>
        </div>
        {rows.length > 0 && (
          <div className="dock-panel-tab__toolbar-meta">
            <span className="dock-panel-tab__meta-label">Total equity</span>
            <span className="dock-panel-tab__meta-value num-mono">
              ${fmtP(stats.totalEquity)}
            </span>
          </div>
        )}
      </header>

      {rows.length === 0 ? (
        <div className="dock-panel-tab__empty">
          <WidgetEmpty icon={Landmark} message="Loading balances…" />
        </div>
      ) : (
        <>
          <div className="dock-panel-tab__table-wrap scroll-panel-y scroll-panel-y-0">
            <table className="terminal-table dock-panel-tab__table min-w-[560px]">
              <thead>
                <tr>
                  <th>Asset</th>
                  <th className="text-right">Total Balance</th>
                  <th className="text-right">Locked</th>
                  <th className="text-right">Available</th>
                  <th className="text-right">USD Value</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ asset, bal, avail, usdValue, isQuote }) => {
                  const dec = isQuote ? 2 : 6;
                  return (
                    <tr key={asset}>
                      <td className="font-bold">{asset}</td>
                      <td className="num-mono text-right">
                        {bal.balance.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
                      </td>
                      <td className="num-mono text-right text-muted-foreground">
                        {bal.locked.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
                      </td>
                      <td className={cn('num-mono text-right font-bold', avail > 0 ? 'text-foreground' : 'text-muted-foreground')}>
                        {avail.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
                      </td>
                      <td className="num-mono text-right text-secondary-foreground">
                        {usdValue != null ? `$${fmtP(usdValue)}` : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <footer className="dock-panel-tab__footer">
            <span>{rows.length} assets · cash + holdings</span>
            <span className="dock-panel-tab__footer-highlight">
              Cash available:{' '}
              <span className="num-mono font-bold">${fmtP(stats.cashAvailable)}</span>
              {stats.cashLocked > 0 && (
                <span className="text-muted-foreground">
                  {' '}· locked ${fmtP(stats.cashLocked)}
                </span>
              )}
              {stats.holdingsUsd > 0 && (
                <span className="text-muted-foreground">
                  {' '}· holdings ${fmtP(stats.holdingsUsd)}
                </span>
              )}
              {' '}· total ${fmtP(stats.totalEquity)}
            </span>
          </footer>
        </>
      )}
    </div>
  );
}

// ── Algo Bot Tab ──────────────────────────────────────────────────
function AlgoTab() {
  const {
    activeBots, botStrategy, botExecutionMode, botConfig, activeSymbol, symbolsList,
    setBotStrategy, setBotExecutionMode, updateBotConfig, clearBotLogs, botLogs,
    strategyTemplates, backtestResults, backtestRuns, setChartInteractionMode,
    isLive, allowLiveBots, terminalMode, terminalRole, distributed, botMinCandles,
    setActiveSymbol,
    selectedBotId, setSelectedBotId, setBotDetail, setBotDrawerOpen,
    ambiguousOrders, setAmbiguousOrders,
  } = useStore();
  const positions = useStore(state => state.positions);
  const agentInsights = useStore(state => state.agentInsights);

  const liveBotsBlocked = isLive && !allowLiveBots;
  const runningCount = activeBots.filter(b => b.status === 'RUNNING').length;
  const [deployOpen, setDeployOpen] = useState(false);
  const [stopAllOpen, setStopAllOpen] = useState(false);
  const [backtestDays, setBacktestDays] = useState('7');
  const logScrollRef = useRef(null);
  const logCountRef = useRef(0);

  useEffect(() => {
    if (botLogs.length > logCountRef.current && logScrollRef.current) {
      logScrollRef.current.scrollTop = 0;
    }
    logCountRef.current = botLogs.length;
  }, [botLogs]);

  const confirmDeploy = () => {
    setDeployOpen(false);
    handleCreateBot();
  };

  const handleCreateBot = () => {
    if (liveBotsBlocked) {
      toast.error('Live bot trading is disabled. Set ALLOW_LIVE_BOTS=true on the server.');
      return;
    }
    if (!botConfig.allocation || botConfig.allocation <= 0) {
      toast.error('Enter a valid capital allocation amount');
      return;
    }

    sendAction(Action.BOT_CREATE, {
      strategy: botStrategy,
      symbol: activeSymbol,
      timeframe: botExecutionMode === 'TICK' ? 'tick' : '1m',
      allocation: botConfig.allocation,
      execution_mode: botExecutionMode,
      config: botConfig,
    });
  };

  const filteredTemplates = strategyTemplates.filter(
    t => (t.execution_mode || 'BAR_CLOSE') === botExecutionMode,
  );

  const handleRunBacktest = () => {
    sendAction(Action.RUN_BACKTEST, {
      strategy: botStrategy,
      symbol: activeSymbol,
      config: botConfig,
      days: parseInt(backtestDays, 10) || 7,
    });
  };

  const selectTemplate = (template) => {
    setBotStrategy(template.strategy);
    if (template.execution_mode) {
      setBotExecutionMode(template.execution_mode);
    }
    updateBotConfig({ ...template.config, allocation: template.allocation });
  };

  const handleStopBot = (bot_id) => {
    sendAction(Action.BOT_STOP, { bot_id });
  };

  const handlePauseBot = (bot_id) => {
    sendAction(Action.BOT_PAUSE, { bot_id });
  };

  const handleResumeBot = (bot_id) => {
    sendAction(Action.BOT_RESUME, { bot_id });
  };

  const handleStopAll = () => {
    if (activeBots.length === 0) return;
    setStopAllOpen(true);
  };

  const confirmStopAll = () => {
    setStopAllOpen(false);
    sendAction(Action.BOT_STOP_ALL, {});
  };

  const statusBadgeVariant = (status) => {
    if (status === 'RUNNING') return 'buy';
    if (status === 'PAUSED') return 'secondary';
    if (status === 'ERROR') return 'destructive';
    return 'sell';
  };

  const logLineClass = (log) => {
    if (log.includes('BUY') || log.includes('SUCCESS')) return 'algo-log-line algo-log-line--success';
    if (log.includes('SELL') || log.includes('ERROR') || log.includes('STOP')) return 'algo-log-line algo-log-line--error';
    if (log.includes('WARN')) return 'algo-log-line algo-log-line--warn';
    if (log.includes('INFO') || log.includes('started')) return 'algo-log-line algo-log-line--info';
    return 'algo-log-line';
  };

  const selectBot = (bot_id) => {
    const bot = activeBots.find(b => b.id === bot_id);
    if (bot?.symbol && bot.symbol !== activeSymbol) {
      setActiveSymbol(bot.symbol);
    }
    setSelectedBotId(bot_id);
    setBotDrawerOpen(true);
    sendAction(Action.BOT_GET_DETAIL, { bot_id });
  };

  useEffect(() => {
    if (!selectedBotId) return;
    if (activeBots.some(b => b.id === selectedBotId)) {
      sendAction(Action.BOT_GET_DETAIL, { bot_id: selectedBotId });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedBotId, activeBots.length]);

  const refreshReconciliation = useCallback(() => {
    sendAction(Action.ADMIN_GET_RECONCILIATION, {});
  }, []);

  useEffect(() => {
    if (isLive) refreshReconciliation();
  }, [isLive, refreshReconciliation]);

  const handleAutoReconcile = () => {
    sendAction(Action.ADMIN_RECONCILE, {});
    setTimeout(refreshReconciliation, 500);
  };

  const handleDismissAmbiguous = (orderId) => {
    sendAction(Action.ADMIN_RESOLVE_AMBIGUOUS, { order_id: orderId, resolution: 'dismissed' });
    setAmbiguousOrders(ambiguousOrders.filter(o => o.id !== orderId));
  };

  return (
    <div className="algo-tab">
      <header className="algo-tab__toolbar">
        <div className="algo-tab__toolbar-lead">
          <div className="algo-tab__toolbar-icon" aria-hidden>
            <Cpu size={14} />
          </div>
          <div className="algo-tab__toolbar-copy">
            <span className="algo-tab__toolbar-title">Algo Trading</span>
            <span className="algo-tab__toolbar-subtitle num-mono">
              {runningCount} running · {activeBots.length} bot{activeBots.length === 1 ? '' : 's'} · {activeSymbol}
            </span>
          </div>
        </div>
        <div className="algo-tab__toolbar-meta">
          {isLive ? (
            <Badge variant="live" className="header-mode-badge header-mode-badge--live px-2 py-0.5 text-[0.58rem] font-extrabold tracking-wider">
              LIVE
            </Badge>
          ) : (
            <Badge variant="secondary" className="header-mode-badge px-2 py-0.5 text-[0.58rem] font-bold">
              SIM
            </Badge>
          )}
          {liveBotsBlocked && (
            <Badge variant="outline" className="algo-tab__toolbar-warn px-2 py-0.5 text-[0.58rem]">
              Exec locked
            </Badge>
          )}
        </div>
      </header>

      <div className="algo-tab__workspace">
      {liveBotsBlocked && (
        <Alert className="algo-tab__banner border-trading-warn/40 bg-trading-warn/10 text-trading-warn xl:col-span-3">
          <ShieldAlert aria-hidden />
          <AlertDescription className="text-xs leading-relaxed">
            Bots run in <strong>{terminalMode}</strong> but live execution is off.
            Set <code className="algo-inline-code">ALLOW_LIVE_BOTS=true</code> in
            server <code className="algo-inline-code">.env</code> to deploy on live feeds.
            Backtest still works.
          </AlertDescription>
        </Alert>
      )}

      {isLive && allowLiveBots && (
        <Alert className="algo-tab__banner border-trading-up/30 bg-trading-up/5 xl:col-span-3">
          <Activity aria-hidden />
          <AlertDescription className="text-xs leading-relaxed">
            <strong>Live bots enabled</strong> on {terminalMode}
            {distributed ? ` · role=${terminalRole} (distributed via Redis)` : ''}.
            Indicator warm-up uses archive when buffer &lt; {botMinCandles} bars.
            Signals fire on closed 1m bars — do not resend ambiguous orders.
          </AlertDescription>
        </Alert>
      )}

      {isLive && ambiguousOrders.length > 0 && (
        <section className="algo-tab__panel algo-reconcile-panel xl:col-span-3">
          <header className="algo-tab__panel-header">
            <div className="algo-tab__panel-title">
              <AlertTriangle size={13} className="text-trading-warn" aria-hidden />
              Ambiguous Orders ({ambiguousOrders.length})
            </div>
            <div className="flex gap-1">
              <Button variant="outline" size="sm" className="h-7 text-xs" onClick={refreshReconciliation}>
                <RefreshCw data-icon="inline-start" aria-hidden />
                Refresh
              </Button>
              <Button variant="outline" size="sm" className="h-7 text-xs" onClick={handleAutoReconcile}>
                Auto-reconcile
              </Button>
            </div>
          </header>
          <div className="algo-tab__scroll scroll-panel-y scroll-panel-y-0 max-h-28">
            <table className="terminal-table text-xs w-full">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th className="text-right">Qty</th>
                  <th>Message</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {ambiguousOrders.map((o) => (
                  <tr key={o.id}>
                    <td>{o.symbol}</td>
                    <td>{o.side}</td>
                    <td className="num-mono text-right">{Number(o.quantity).toFixed(4)}</td>
                    <td className="text-muted-foreground truncate max-w-[180px]" title={o.message}>{o.message}</td>
                    <td className="text-right">
                      <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={() => handleDismissAmbiguous(o.id)}>
                        Dismiss
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="algo-tab__panel algo-tab__panel--deploy">
        <header className="algo-tab__panel-header">
          <div className="algo-tab__panel-heading">
            <div className="algo-tab__panel-title">
              <Settings size={13} className="text-primary" aria-hidden />
              Deploy Bot
            </div>
            <span className="algo-tab__panel-subtitle">Strategy · allocation · backtest</span>
          </div>
        </header>
        <div className="algo-tab__scroll scroll-panel-y scroll-panel-y-0 algo-tab__deploy-body">
          <div className="algo-deploy-fields">
            <div className="algo-deploy-field">
              <Label className="algo-field-label">Symbol</Label>
              <Select value={activeSymbol} onValueChange={setActiveSymbol}>
                <SelectTrigger className="h-8 w-full text-xs" aria-label="Bot symbol">
                  <SelectValue placeholder="Select symbol" />
                </SelectTrigger>
                <SelectContent position="popper" className="max-h-56 min-w-[var(--radix-select-trigger-width)]">
                  {symbolsList.map(sym => (
                    <SelectItem key={sym} value={sym} className="text-xs">{sym}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="algo-deploy-field">
              <Label className="algo-field-label">Execution Mode</Label>
              <Select
                value={botExecutionMode}
                onValueChange={(mode) => {
                  setBotExecutionMode(mode);
                  const first = strategyTemplates.find(t => (t.execution_mode || 'BAR_CLOSE') === mode);
                  if (first) selectTemplate(first);
                }}
              >
                <SelectTrigger className="h-8 w-full text-xs" aria-label="Bot execution mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent position="popper">
                  <SelectItem value="BAR_CLOSE" className="text-xs">Bar Close — 1m indicator signals</SelectItem>
                  <SelectItem value="TICK" className="text-xs">Tick — sub-minute microstructure</SelectItem>
                </SelectContent>
              </Select>
              <span className="algo-field-hint">
                Tick bots evaluate every price update with cooldown; bar bots fire on closed 1m candles only.
              </span>
            </div>

            <div className="algo-deploy-field">
              <Label className="algo-field-label">Strategy Templates</Label>
              <div className="algo-template-grid">
                {filteredTemplates.map(t => (
                  <StrategyTemplateCard
                    key={t.id}
                    template={t}
                    active={botStrategy === t.strategy}
                    onSelect={selectTemplate}
                  />
                ))}
              </div>
            </div>

            <div className="algo-deploy-field">
              <Label className="algo-field-label">Take Profit</Label>
              <Select
                value={botConfig?.tp_mode ?? 'percent'}
                onValueChange={(mode) => {
                  if (mode === 'none') {
                    updateBotConfig({ tp_mode: 'none', take_profit_percent: undefined });
                  } else if (mode === 'strategy') {
                    updateBotConfig({ tp_mode: 'strategy', take_profit_percent: undefined });
                  } else {
                    updateBotConfig({
                      tp_mode: 'percent',
                      take_profit_percent: botConfig?.take_profit_percent ?? 3,
                    });
                  }
                }}
              >
                <SelectTrigger className="h-8 w-full text-xs" aria-label="Take profit mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent position="popper">
                  <SelectItem value="percent" className="text-xs">Fixed % from entry</SelectItem>
                  <SelectItem value="strategy" className="text-xs" disabled={botStrategy !== 'BRS_SCALPING'}>
                    Strategy target (BRS mid-band)
                  </SelectItem>
                  <SelectItem value="none" className="text-xs">None — trailing stop only</SelectItem>
                </SelectContent>
              </Select>
              {(botConfig?.tp_mode ?? 'percent') === 'percent' && (
                <InputGroup className="h-8 mt-2">
                  <InputGroupInput
                    type="number"
                    step="any"
                    min="0"
                    value={botConfig?.take_profit_percent ?? ''}
                    onChange={e => updateBotConfig({
                      take_profit_percent: parseFloat(e.target.value) || 0,
                      tp_mode: 'percent',
                    })}
                    className="text-xs"
                    aria-label="Take profit percent"
                  />
                  <InputGroupAddon align="inline-end">
                    <InputGroupText className="text-xs">%</InputGroupText>
                  </InputGroupAddon>
                </InputGroup>
              )}
              <span className="algo-field-hint">
                TP closes the position when price reaches target. Trailing stop still applies.
              </span>
            </div>

            <div className="algo-deploy-field">
              <Label className="algo-field-label">Capital Allocation</Label>
              <InputGroup className="h-8">
                <InputGroupInput
                  type="number"
                  step="any"
                  value={botConfig?.allocation || ''}
                  onChange={e => updateBotConfig({ allocation: parseFloat(e.target.value) || 0 })}
                  className="text-xs"
                  aria-label="Capital allocation"
                />
                <InputGroupAddon align="inline-end">
                  <InputGroupText className="text-xs">$</InputGroupText>
                </InputGroupAddon>
              </InputGroup>
              <span className="algo-field-hint">
                Risk sized at 1% of account balance using ATR-based stops. Signals evaluate on closed 1m bars.
              </span>
            </div>

            {botStrategy === 'CHART_AGENT' && (
              <div className="algo-deploy-field space-y-2">
                <Label className="algo-field-label">Chart Agent Settings</Label>
                <div>
                  <div className="mb-1 flex justify-between text-[0.62rem] text-muted-foreground">
                    <span>Min confidence</span>
                    <span>{Math.round((botConfig?.min_confidence ?? 0.55) * 100)}%</span>
                  </div>
                  <input
                    type="range"
                    min="0.4"
                    max="1"
                    step="0.05"
                    value={botConfig?.min_confidence ?? 0.55}
                    onChange={e => updateBotConfig({ min_confidence: parseFloat(e.target.value) })}
                    className="w-full accent-primary"
                    aria-label="Minimum signal confidence"
                  />
                </div>
                <label className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={Boolean(botConfig?.use_llm)}
                    onChange={e => updateBotConfig({ use_llm: e.target.checked })}
                    className="accent-primary"
                  />
                  Use LLM explanations on strong signals (server must enable AGENT_LLM_ENABLED)
                </label>
              </div>
            )}

            <div className="algo-deploy-field">
              <Label className="algo-field-label">Backtest Range</Label>
              <Select value={backtestDays} onValueChange={setBacktestDays}>
                <SelectTrigger className="h-8 w-full text-xs" aria-label="Backtest history range">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent position="popper">
                  <SelectItem value="7" className="text-xs">7 days (in-memory + archive)</SelectItem>
                  <SelectItem value="30" className="text-xs">30 days (archive 1m)</SelectItem>
                  <SelectItem value="90" className="text-xs">90 days (archive 1m max)</SelectItem>
                </SelectContent>
              </Select>
              <span className="algo-field-hint">
                Uses archived 1m bars when range exceeds live buffer. Scroll the chart left to load older history.
              </span>
            </div>

            {backtestResults && (
              <BacktestResultsPanel
                results={backtestResults}
                backtestDays={backtestDays}
                symbol={activeSymbol}
                strategy={botStrategy}
                recentRuns={backtestRuns}
              />
            )}
          </div>
        </div>
        <footer className="algo-tab__panel-footer">
          <Button
            variant="outline"
            size="sm"
            className="flex-1 text-xs"
            onClick={handleRunBacktest}
            disabled={botExecutionMode === 'TICK'}
            title={botExecutionMode === 'TICK' ? 'Backtest applies to bar-close strategies only' : undefined}
          >
            <Activity data-icon="inline-start" />
            BACKTEST
          </Button>
          <Button
            variant="buy"
            size="sm"
            className="flex-[1.5] text-xs font-bold"
            onClick={() => setDeployOpen(true)}
            disabled={liveBotsBlocked}
            title={liveBotsBlocked ? 'Live bot trading disabled on server' : 'Deploy bot'}
          >
            <Play data-icon="inline-start" />
            DEPLOY
          </Button>
        </footer>
      </section>

      <Dialog open={deployOpen} onOpenChange={setDeployOpen}>
        <DialogContent className="algo-dialog sm:max-w-md" overlayClassName="admin-panel-overlay">
          <DialogHeader>
            <DialogTitle>Deploy trading bot</DialogTitle>
            <DialogDescription className="text-xs leading-relaxed">
              This will start a live bot on the server using your current template and allocation.
            </DialogDescription>
          </DialogHeader>
          <div className="algo-dialog-summary">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground shrink-0">Strategy:</span>
              <StrategyBadge strategy={botStrategy} />
            </div>
            <div><span className="text-muted-foreground">Symbol:</span> <strong>{activeSymbol}</strong></div>
            <div><span className="text-muted-foreground">Allocation:</span> <strong>${botConfig?.allocation?.toLocaleString() ?? 0}</strong></div>
            <div>
              <span className="text-muted-foreground">Stop / TP:</span>{' '}
              <strong>
                SL {botConfig?.trailing_stop_percent ?? botConfig?.stop_loss_percent ?? '—'}%
                {' · '}
                {botConfig?.tp_mode === 'none'
                  ? 'no TP'
                  : botConfig?.tp_mode === 'strategy'
                    ? 'strategy target'
                    : `${botConfig?.take_profit_percent ?? '—'}% TP`}
              </strong>
            </div>
            <div><span className="text-muted-foreground">Timeframe:</span> <strong>{botExecutionMode === 'TICK' ? 'tick (sub-minute)' : '1m (closed-bar signals)'}</strong></div>
          </div>
          <DialogFooter showCloseButton={false}>
            <Button variant="outline" size="sm" onClick={() => setDeployOpen(false)}>Cancel</Button>
            <Button variant="buy" size="sm" onClick={confirmDeploy}>Confirm deploy</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={stopAllOpen} onOpenChange={setStopAllOpen}>
        <DialogContent className="algo-dialog sm:max-w-md" overlayClassName="admin-panel-overlay">
          <DialogHeader>
            <DialogTitle>Stop all bots?</DialogTitle>
            <DialogDescription className="text-xs leading-relaxed">
              Halts every active bot. Does not close open positions.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter showCloseButton={false}>
            <Button variant="ghost" size="sm" onClick={() => setStopAllOpen(false)}>Cancel</Button>
            <Button variant="destructive" size="sm" onClick={confirmStopAll}>Stop all</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <section className="algo-tab__panel algo-tab__panel--bots">
        <header className="algo-tab__panel-header">
          <div className="algo-tab__panel-heading">
            <div className="algo-tab__panel-title">
              <Cpu size={13} className={runningCount > 0 ? 'text-trading-up' : 'text-muted-foreground'} aria-hidden />
              Active Bots
              <Badge variant={runningCount > 0 ? 'buy' : 'secondary'}>{runningCount}</Badge>
            </div>
            <span className="algo-tab__panel-subtitle">Pause · resume · stop · details</span>
          </div>
          <div className="algo-tab__panel-actions">
            {activeBots.length > 0 && (
              <span className="algo-bots-scroll-hint">Scroll ↔</span>
            )}
            {activeBots.length > 0 && (
              <Button
                variant="outline"
                size="xs"
                className="algo-stop-all-btn"
                onClick={handleStopAll}
                title="Stop all bots"
              >
                <OctagonX data-icon="inline-start" />
                STOP ALL
              </Button>
            )}
          </div>
        </header>
        <ScrollTablePanel horizontal className="algo-tab__scroll">
          <table className="terminal-table algo-bots-table m-0">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Strategy</th>
                <th className="text-center">Position</th>
                <th className="text-right">Alloc</th>
                <th className="text-right">Today PnL</th>
                <th>Last signal</th>
                <th className="text-center">Status</th>
                <th className="text-center">Actions</th>
              </tr>
            </thead>
            <tbody>
              {activeBots.length === 0 ? (
                <tr>
                  <td colSpan="8" className="algo-table-empty">
                    No active bots. Pick a template and deploy.
                  </td>
                </tr>
              ) : (
                activeBots.map(bot => {
                  const pos = positions[bot.symbol];
                  const inPosition = pos && Math.abs(pos.size) > 0;
                  return (
                  <tr
                    key={bot.id}
                    className={cn('algo-bot-row', selectedBotId === bot.id && 'row-active')}
                    onClick={() => selectBot(bot.id)}
                  >
                    <td className="font-bold">{bot.symbol}</td>
                    <td className="text-xs">
                      <StrategyBadge strategy={bot.strategy} compact />
                      {bot.execution_mode === 'TICK' && (
                        <Badge variant="outline" className="ml-1 h-4 px-1 text-[0.55rem]">TICK</Badge>
                      )}
                    </td>
                    <td className="text-center">
                      {inPosition ? (
                        <Badge variant={pos.size > 0 ? 'buy' : 'sell'}>
                          {pos.size > 0 ? 'LONG' : 'SHORT'}
                        </Badge>
                      ) : (
                        <span className="text-muted-foreground text-[0.62rem]">FLAT</span>
                      )}
                    </td>
                    <td className="num-mono text-right">${bot.allocation.toLocaleString()}</td>
                    <td className={cn(
                      'num-mono text-right font-semibold',
                      (bot.daily_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down',
                    )}>
                      {(bot.daily_pnl ?? 0) >= 0 ? '+' : ''}{(bot.daily_pnl ?? 0).toFixed(2)}
                    </td>
                    <td className="algo-last-signal" title={bot.last_signal_at || undefined}>
                      <span>{formatLastSignal(bot.last_signal_at)}</span>
                      {bot.strategy === 'CHART_AGENT' && agentInsights[bot.symbol]?.confidence != null && (
                        <span className="ml-1 text-[0.58rem] text-muted-foreground">
                          ({Math.round(agentInsights[bot.symbol].confidence * 100)}% conf)
                        </span>
                      )}
                    </td>
                    <td className="text-center">
                      <Badge variant={statusBadgeVariant(bot.status)}>{bot.status}</Badge>
                    </td>
                    <td className="text-center" onClick={e => e.stopPropagation()}>
                      <div className="algo-bot-actions">
                        {bot.status === 'RUNNING' && (
                          <>
                            <Button variant="outline" size="xs" onClick={() => handlePauseBot(bot.id)} title="Pause bot">
                              <Pause />
                            </Button>
                            <Button
                              variant="outline"
                              size="xs"
                              onClick={() => setChartInteractionMode('edit_sl')}
                              title="Set stop loss on chart"
                            >
                              SL
                            </Button>
                          </>
                        )}
                        {bot.status === 'PAUSED' && (
                          <Button variant="outline" size="xs" onClick={() => handleResumeBot(bot.id)} title="Resume bot">
                            <PlayCircle />
                          </Button>
                        )}
                        {bot.status !== 'STOPPED' && (
                          <Button variant="destructive" size="xs" onClick={() => handleStopBot(bot.id)} title="Stop bot">
                            STOP
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </ScrollTablePanel>
      </section>

      <section className="algo-tab__panel algo-tab__panel--log">
        <header className="algo-tab__panel-header">
          <div className="algo-tab__panel-heading">
            <div className="algo-tab__panel-title">
              <Activity size={13} className="text-muted-foreground" aria-hidden />
              Bot Log
            </div>
            <span className="algo-tab__panel-subtitle">{botLogs.length} entries</span>
          </div>
          <Button variant="ghost" size="icon-sm" onClick={clearBotLogs} title="Clear log" aria-label="Clear bot log">
            <Trash2 />
          </Button>
        </header>

        <div ref={logScrollRef} className="algo-tab__scroll algo-bot-log-scroll scroll-panel-y scroll-panel-y-0">
          <div className="algo-tab__log-list">
            {botLogs.length === 0 ? (
              <WidgetEmpty icon={Cpu} message="Bot console is empty" className="min-h-[80px]" />
            ) : botLogs.map((log, i) => {
              const isSignal = /Entry (BUY|SELL)|signal @/i.test(log);
              const showInsight = isSignal && botStrategy === 'CHART_AGENT' && agentInsights[activeSymbol];
              return (
                <div key={`${i}-${log.slice(0, 24)}`} className={cn(logLineClass(log), showInsight && 'group relative')}>
                  <span>{log}</span>
                  {showInsight && (
                    <button
                      type="button"
                      className="ml-2 text-[0.58rem] text-primary opacity-0 group-hover:opacity-100"
                      onClick={() => window.dispatchEvent(new CustomEvent('dock-tab', { detail: 'analyst' }))}
                    >
                      Why?
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </section>
      </div>
    </div>
  );
}

// ── Main ResizableDock ────────────────────────────────────────────
export default function ResizableDock({ setDockHeight: setParentDockHeight, initialDockHeight }) {
  const positions = useStore(state => state.positions);
  const orders = useStore(state => state.orders);
  const tradeHistory = useStore(state => state.tradeHistory);
  const isBotRunning = useStore(state => state.isBotRunning);
  const botHistory = useStore(state => state.botHistory);
  const agentInsightHistory = useStore(state => state.agentInsightHistory);
  const activeSymbol = useStore(state => state.activeSymbol);
  const ambiguousOrders = useStore(state => state.ambiguousOrders);
  const isLive = useStore(state => state.isLive);
  const selectedBotId = useStore(state => state.selectedBotId);
  const botDrawerOpen = useStore(state => state.botDrawerOpen);
  const setBotDrawerOpen = useStore(state => state.setBotDrawerOpen);
  const workspaceTab = normalizeDockTab(
    useSettingsStore(state => state.settings.workspace?.dockActiveTab || 'positions'),
  );
  const workspaceGroup = useSettingsStore(state => state.settings.workspace?.dockGroup || 'portfolio');
  const dockCollapsed = useSettingsStore(state => state.settings.workspace?.dockCollapsed ?? false);
  const updateWorkspace = useSettingsStore(state => state.updateWorkspace);
  const [activeTab, setActiveTab] = useState(workspaceTab);
  const [activeGroup, setActiveGroup] = useState(
    DOCK_GROUP_CONFIG[workspaceGroup] ? workspaceGroup : dockGroupForTab(workspaceTab),
  );
  const [dockH, setDockH] = useState(() => initialDockHeight || DOCK_DEFAULT);
  const [historyFullscreen, setHistoryFullscreen] = useState(false);
  const isDragging = useRef(false);
  const startY    = useRef(0);
  const startH    = useRef(0);
  const dockHRef  = useRef(dockH);

  useEffect(() => {
    dockHRef.current = dockH;
  }, [dockH]);

  useEffect(() => {
    setActiveTab(workspaceTab);
    setActiveGroup(dockGroupForTab(workspaceTab));
  }, [workspaceTab]);

  useEffect(() => {
    if (DOCK_GROUP_CONFIG[workspaceGroup]) {
      setActiveGroup(workspaceGroup);
    }
  }, [workspaceGroup]);

  // Sync dock height to parent App so CSS variable can update
  useEffect(() => {
    setParentDockHeight(dockH);
  }, [dockH, setParentDockHeight]);

  useEffect(() => {
    const onDockTab = (e) => {
      if (e.detail) {
        const tab = normalizeDockTab(e.detail);
        setActiveTab(tab);
        setActiveGroup(dockGroupForTab(tab));
        updateWorkspace({ dockActiveTab: tab, dockGroup: dockGroupForTab(tab) });
      }
    };
    const onDockGroup = (e) => {
      if (e.detail && DOCK_GROUP_CONFIG[e.detail]) {
        setActiveGroup(e.detail);
        const firstTab = DOCK_GROUP_CONFIG[e.detail].tabs[0];
        setActiveTab(firstTab);
        updateWorkspace({ dockGroup: e.detail, dockActiveTab: firstTab });
      }
    };
    window.addEventListener('dock-tab', onDockTab);
    window.addEventListener('dock-group', onDockGroup);
    return () => {
      window.removeEventListener('dock-tab', onDockTab);
      window.removeEventListener('dock-group', onDockGroup);
    };
  }, [updateWorkspace]);

  const handleTabChange = useCallback((tab) => {
    if (!tab) return;
    const next = normalizeDockTab(tab);
    const group = dockGroupForTab(next);
    setActiveTab(next);
    setActiveGroup(group);
    updateWorkspace({ dockActiveTab: next, dockGroup: group });
  }, [updateWorkspace]);

  const handleGroupChange = useCallback((group) => {
    if (!group || !DOCK_GROUP_CONFIG[group]) return;
    const firstTab = DOCK_GROUP_CONFIG[group].tabs.includes(activeTab)
      ? activeTab
      : DOCK_GROUP_CONFIG[group].tabs[0];
    setActiveGroup(group);
    setActiveTab(firstTab);
    updateWorkspace({ dockGroup: group, dockActiveTab: firstTab });
  }, [activeTab, updateWorkspace]);

  const pendingOrders = orders.filter(o => o.status === 'PENDING').length;
  const posCount = Object.keys(positions).length;

  const onMouseDown = useCallback(e => {
    isDragging.current = true;
    startY.current = e.clientY;
    startH.current = dockH;
    document.body.style.cursor = 'ns-resize';
    document.body.style.userSelect = 'none';
  }, [dockH]);

  useEffect(() => {
    const onMove = e => {
      if (!isDragging.current) return;
      const delta = startY.current - e.clientY;
      const newH = Math.max(DOCK_MIN, Math.min(DOCK_MAX, startH.current + delta));
      setDockH(newH);
    };
    const onUp = () => {
      if (isDragging.current) {
        isDragging.current = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        try { updateWorkspace({ dockHeight: dockHRef.current }); } catch {}
      }
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
  }, [updateWorkspace]);

  const analystBadge = (agentInsightHistory[activeSymbol] ?? []).length || null;
  const scanBadge = useStore((s) => {
    const rows = s.scanResults?.rows ?? [];
    return rows.filter((r) => r.signal && r.signal !== 'NONE').length || null;
  });

  const TABS = [
    { id: 'positions', label: 'Positions', icon: Briefcase, badge: posCount || null, group: 'portfolio' },
    { id: 'orders',    label: 'Orders',    icon: List,     badge: pendingOrders || null, group: 'portfolio' },
    { id: 'balances',  label: 'Balances',  icon: Landmark, group: 'portfolio' },
    { id: 'algo',      label: 'Algo Bot',  icon: Cpu,      group: 'automation' },
    { id: 'scanner',   label: 'Scanner',   icon: Radar,    badge: scanBadge, group: 'intelligence' },
    { id: 'analyst',   label: 'Analyst',   icon: Brain,    badge: analystBadge, group: 'intelligence' },
    { id: 'reconcile', label: 'Reconcile', icon: AlertTriangle, badge: isLive && ambiguousOrders.length ? ambiguousOrders.length : null, group: 'automation' },
    { id: 'bots',      label: 'Bot History', icon: History, badge: botHistory.length || null, group: 'automation' },
    { id: 'ticks',     label: 'Ticks',     icon: Zap,      group: 'data' },
    { id: 'history',   label: 'History',   icon: Activity, badge: tradeHistory.length || null, group: 'data' },
    { id: 'equity',    label: 'Equity Curve', icon: TrendingUp, group: 'data' },
  ];

  const groupTabs = TABS.filter((t) => t.group === activeGroup);
  const groupBadge = (groupId) => {
    const tabs = TABS.filter((t) => t.group === groupId);
    return tabs.reduce((sum, t) => sum + (Number(t.badge) || 0), 0) || null;
  };

  if (dockCollapsed) {
    return (
      <div className="bottom-dock bottom-dock--collapsed flex items-center justify-between px-3" style={{ gridArea: 'dock', height: 36 }}>
        <span className="text-[0.62rem] text-muted-foreground">Dock collapsed</span>
        <Button variant="ghost" size="xs" className="text-xs" onClick={() => updateWorkspace({ dockCollapsed: false })}>
          Expand dock
        </Button>
      </div>
    );
  }

  return (
    <>
      <div
        className="bottom-dock flex flex-col"
        data-compact={dockH < 280 ? '' : undefined}
        style={{ gridArea: 'dock', height: dockH, minHeight: DOCK_MIN }}
      >
        <div className="dock-resize-handle" onMouseDown={onMouseDown} />

        <Tabs value={activeTab} onValueChange={handleTabChange} className="dock-tabs-root gap-0">
          <div className="dock-tab-bar">
            <div className="dock-group-rail">
              {Object.entries(DOCK_GROUP_CONFIG).map(([groupId, cfg]) => (
                <Button
                  key={groupId}
                  variant={activeGroup === groupId ? 'secondary' : 'ghost'}
                  size="xs"
                  className="dock-group-btn h-7 px-2 text-[0.62rem]"
                  onClick={() => handleGroupChange(groupId)}
                >
                  {cfg.label}
                  {groupBadge(groupId) != null && (
                    <Badge variant="secondary" className="ml-1 h-4 min-w-4 px-1 text-[0.55rem]">
                      {groupBadge(groupId)}
                    </Badge>
                  )}
                </Button>
              ))}
            </div>
            <div className="dock-tab-bar-inner scroll-fade-x">
              <TabsList variant="line" className="dock-tab-switch scroll-panel-x no-scrollbar min-w-0 flex-1 justify-start rounded-none border-0 bg-transparent">
                {groupTabs.map(tab => {
                  const Icon = tab.icon;
                  return (
                    <TabsTrigger
                      key={tab.id}
                      value={tab.id}
                      className="dock-tab-trigger shrink-0 px-2 text-xs xl:px-3"
                      title={tab.label}
                    >
                      <Icon data-icon="inline-start" />
                      <span className="header-label">{tab.label}</span>
                      {tab.badge != null && (
                        <Badge variant="secondary" className="h-4 min-w-4 px-1 text-[0.58rem] font-bold">
                          {tab.badge}
                        </Badge>
                      )}
                      {tab.id === 'algo' && isBotRunning && (
                        <span className="dock-algo-pulse" aria-hidden />
                      )}
                    </TabsTrigger>
                  );
                })}
              </TabsList>
            </div>
            <div className="dock-tab-actions">
            {activeGroup === 'intelligence' && (
              <Button variant="outline" size="xs" className="h-6 text-[0.58rem]" onClick={() => window.dispatchEvent(new CustomEvent('insights-hub-open'))}>
                Hub
              </Button>
            )}
            {activeGroup === 'automation' && (
              <Button variant="outline" size="xs" className="h-6 text-[0.58rem]" onClick={() => window.dispatchEvent(new CustomEvent('automation-studio-open'))}>
                Studio
              </Button>
            )}
            <Button variant="ghost" size="xs" className="h-6 text-[0.58rem] text-muted-foreground" onClick={() => updateWorkspace({ dockCollapsed: true })} title="Collapse dock">
              <Minimize2 size={12} />
            </Button>
            {activeTab === 'history' && (
              <Button
                variant="ghost"
                size="icon-sm"
                className="shrink-0"
                onClick={() => setHistoryFullscreen(f => !f)}
                title={historyFullscreen ? 'Collapse' : 'Expand to fullscreen'}
              >
                {historyFullscreen ? <Minimize2 /> : <Maximize2 />}
              </Button>
            )}
            </div>
          </div>

          <TabsContent value="positions" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <ErrorBoundary name="Positions">
              <PositionsTab />
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="orders" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <ErrorBoundary name="Orders">
              <OrdersTab />
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="balances" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <ErrorBoundary name="Balances">
              <BalancesTab />
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="algo" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <ErrorBoundary name="Algo Bot">
              <AlgoTab />
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="scanner" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <ErrorBoundary name="Scanner">
              {activeTab === 'scanner' && (
                <Suspense fallback={<DockTabFallback />}>
                  <ScannerTab />
                </Suspense>
              )}
            </ErrorBoundary>
          </TabsContent>

          <TabsContent value="analyst" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <ErrorBoundary name="Chart Analyst">
              {activeTab === 'analyst' && (
                <Suspense fallback={<DockTabFallback />}>
                  <AnalystTab />
                </Suspense>
              )}
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="reconcile" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <ErrorBoundary name="Reconciliation">
              <ReconciliationTab />
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="bots" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <ErrorBoundary name="Bot History">
              {activeTab === 'bots' && (
                <Suspense fallback={<DockTabFallback />}>
                  <BotHistoryTab />
                </Suspense>
              )}
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="ticks" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <ErrorBoundary name="Ticks">
              {activeTab === 'ticks' && (
                <Suspense fallback={<DockTabFallback />}>
                  <TickViewerTab />
                </Suspense>
              )}
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="equity" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <ErrorBoundary name="Equity curve">
              <EquityCurveTab />
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="history" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <ErrorBoundary name="Trade history">
              {!historyFullscreen && <TradeHistoryContent embedded />}
            </ErrorBoundary>
          </TabsContent>
        </Tabs>
      </div>

      {/* Expanded history sheet */}
      <Sheet open={historyFullscreen && activeTab === 'history'} onOpenChange={setHistoryFullscreen}>
        <SheetContent
          side="bottom"
          showCloseButton={false}
          className="flex h-[85vh] max-h-[85vh] min-h-0 flex-col gap-0 overflow-hidden rounded-t-xl border-t p-0 sm:max-w-full"
        >
          <ErrorBoundary name="Trade history (expanded)">
            <TradeHistoryContent embedded={false} onClose={() => setHistoryFullscreen(false)} />
          </ErrorBoundary>
        </SheetContent>
      </Sheet>

      <ErrorBoundary name="Bot detail">
      <BotDetailDrawer
        open={botDrawerOpen && !!selectedBotId}
        onOpenChange={setBotDrawerOpen}
        onStop={(bot_id) => sendAction(Action.BOT_STOP, { bot_id })}
        onPause={(bot_id) => sendAction(Action.BOT_PAUSE, { bot_id })}
        onResume={(bot_id) => sendAction(Action.BOT_RESUME, { bot_id })}
      />
      </ErrorBoundary>
    </>
  );
}
