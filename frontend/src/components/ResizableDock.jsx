/**
 * ResizableDock.jsx
 * Bottom docked panel with tabs:
 *   Positions | Orders | Balances | Algo Bot | Bot History | Ticks | History | Equity Curve
 *
 * Features:
 *  - Drag-to-resize via top handle (persists to localStorage)
 *  - History tab can be expanded to full-screen overlay
 *  - Badge counts on Positions and Orders tabs
 */
import React, { useState, useRef, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import {
  Briefcase, List, Landmark, Cpu, Activity, TrendingUp,
  Play, Settings, Trash2, XSquare, Maximize2, Minimize2, ShieldAlert, Pause, PlayCircle, OctagonX,
  RefreshCw, AlertTriangle, Zap, History,
} from 'lucide-react';
import EquityCurveTab from './EquityCurveTab';
import TradeHistoryContent from './TradeHistoryPanel';
import BacktestResultsPanel from './BacktestResultsPanel';
import BotDetailDrawer from './BotDetailDrawer';
import TickViewerTab from './TickViewerTab';
import BotHistoryTab from './BotHistoryTab';
import ReconciliationTab from './ReconciliationTab';
import ErrorBoundary from './ErrorBoundary';
import StrategyTemplateCard from './StrategyTemplateCard';
import StrategyBadge from './StrategyBadge';
import { WidgetEmpty, ScrollTablePanel } from './WidgetShell';
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

const DOCK_MIN = 200;
const DOCK_MAX = 560;
const DOCK_DEFAULT = 320;
const STORAGE_KEY = 'terminal_dock_height';

// ── Tiny formatters ───────────────────────────────────────────────
const priceDecimals = (sym, price) =>
  (sym?.includes('XRP') || sym?.includes('ADA') || sym?.includes('DOGE') || (price != null && price < 2.0)) ? 4 : 2;

const fmtP = (n, d = 2) =>
  n == null ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

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
  const activeBots = useStore(state => state.activeBots);
  const tradeHistory = useStore(state => state.tradeHistory);
  const entries = Object.entries(positions);

  if (entries.length === 0) {
    return <WidgetEmpty icon={Briefcase} message="No open positions" />;
  }

  const botCtx = { activeBots, tradeHistory };

  return (
    <table className="terminal-table min-w-[880px]">
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
  );
}

// ── Orders Tab ────────────────────────────────────────────────────
function OrdersTab() {
  const orders = useStore(state => state.orders);
  const activeBots = useStore(state => state.activeBots);
  const { byId } = buildBotLookup(activeBots);
  const active = orders.filter(o => o.status === 'PENDING');

  if (active.length === 0) {
    return <WidgetEmpty icon={List} message="No pending orders" />;
  }

  return (
    <table className="terminal-table min-w-[640px]">
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
  );
}

// ── Balances Tab ──────────────────────────────────────────────────
function BalancesTab() {
  const balances = useStore(state => state.balances);
  const entries = Object.entries(balances);

  if (entries.length === 0) {
    return <WidgetEmpty message="Loading balances…" />;
  }

  return (
    <table className="terminal-table">
      <thead>
        <tr>
          <th>Asset</th>
          <th className="text-right">Total Balance</th>
          <th className="text-right">Locked</th>
          <th className="text-right">Available</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([asset, bal]) => {
          const avail = bal.balance - bal.locked;
          const dec = asset === 'USD' || asset === 'USDT' ? 2 : 6;
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
            </tr>
          );
        })}
      </tbody>
    </table>
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
          <div className="algo-tab__panel-title">
            <Settings size={13} className="text-primary" aria-hidden />
            Deploy Bot
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
        <DialogContent className="sm:max-w-md">
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
        <DialogContent className="sm:max-w-md">
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
          <div className="algo-tab__panel-title">
            <Cpu size={13} className={runningCount > 0 ? 'text-trading-up' : 'text-muted-foreground'} aria-hidden />
            Active Bots
            <Badge variant={runningCount > 0 ? 'buy' : 'secondary'}>{runningCount}</Badge>
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
                      {formatLastSignal(bot.last_signal_at)}
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
          <div className="algo-tab__panel-title">
            <Activity size={13} className="text-muted-foreground" aria-hidden />
            Bot Log
          </div>
          <Button variant="ghost" size="icon-sm" onClick={clearBotLogs} title="Clear log" aria-label="Clear bot log">
            <Trash2 />
          </Button>
        </header>

        <div ref={logScrollRef} className="algo-tab__scroll algo-bot-log-scroll scroll-panel-y scroll-panel-y-0">
          <div className="algo-tab__log-list">
            {botLogs.length === 0 ? (
              <WidgetEmpty icon={Cpu} message="Bot console is empty" className="min-h-[80px]" />
            ) : botLogs.map((log, i) => (
              <div key={`${i}-${log.slice(0, 24)}`} className={logLineClass(log)}>{log}</div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

// ── Main ResizableDock ────────────────────────────────────────────
export default function ResizableDock({ setDockHeight: setParentDockHeight }) {
  const positions = useStore(state => state.positions);
  const orders = useStore(state => state.orders);
  const tradeHistory = useStore(state => state.tradeHistory);
  const isBotRunning = useStore(state => state.isBotRunning);
  const botHistory = useStore(state => state.botHistory);
  const ambiguousOrders = useStore(state => state.ambiguousOrders);
  const isLive = useStore(state => state.isLive);
  const selectedBotId = useStore(state => state.selectedBotId);
  const botDrawerOpen = useStore(state => state.botDrawerOpen);
  const setBotDrawerOpen = useStore(state => state.setBotDrawerOpen);
  const [activeTab, setActiveTab] = useState('positions');
  const [dockH, setDockH]   = useState(() => {
    try { return parseInt(localStorage.getItem(STORAGE_KEY)) || DOCK_DEFAULT; }
    catch { return DOCK_DEFAULT; }
  });
  const [historyFullscreen, setHistoryFullscreen] = useState(false);
  const isDragging = useRef(false);
  const startY    = useRef(0);
  const startH    = useRef(0);

  // Sync dock height to parent App so CSS variable can update
  useEffect(() => {
    setParentDockHeight(dockH);
  }, [dockH, setParentDockHeight]);

  useEffect(() => {
    const onDockTab = (e) => {
      if (e.detail) setActiveTab(e.detail);
    };
    window.addEventListener('dock-tab', onDockTab);
    return () => window.removeEventListener('dock-tab', onDockTab);
  }, []);

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
        try { localStorage.setItem(STORAGE_KEY, String(dockH)); } catch {}
      }
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
  }, [dockH]);

  const TABS = [
    { id: 'positions', label: 'Positions', icon: Briefcase, badge: posCount || null },
    { id: 'orders',    label: 'Orders',    icon: List,     badge: pendingOrders || null },
    { id: 'balances',  label: 'Balances',  icon: Landmark  },
    { id: 'algo',      label: 'Algo Bot',  icon: Cpu       },
    { id: 'reconcile', label: 'Reconcile', icon: AlertTriangle, badge: isLive && ambiguousOrders.length ? ambiguousOrders.length : null },
    { id: 'bots',      label: 'Bot History', icon: History, badge: botHistory.length || null },
    { id: 'ticks',     label: 'Ticks',     icon: Zap       },
    { id: 'history',   label: 'History',   icon: Activity, badge: tradeHistory.length || null },
    { id: 'equity',    label: 'Equity Curve', icon: TrendingUp },
  ];

  return (
    <ErrorBoundary name="Trading dock">
    <>
      <div
        className="bottom-dock flex flex-col"
        data-compact={dockH < 280 ? '' : undefined}
        style={{ gridArea: 'dock', height: dockH, minHeight: DOCK_MIN }}
      >
        <div className="dock-resize-handle" onMouseDown={onMouseDown} />

        <Tabs value={activeTab} onValueChange={setActiveTab} className="dock-tabs-root gap-0">
          <div className="dock-tab-bar">
            <div className="dock-tab-bar-inner scroll-fade-x">
              <TabsList variant="line" className="dock-tab-switch scroll-panel-x no-scrollbar min-w-0 flex-1 justify-start rounded-none border-0 bg-transparent">
                {TABS.map(tab => {
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
            <ScrollTablePanel>
              <PositionsTab />
            </ScrollTablePanel>
          </TabsContent>
          <TabsContent value="orders" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <ScrollTablePanel>
              <OrdersTab />
            </ScrollTablePanel>
          </TabsContent>
          <TabsContent value="balances" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <ScrollTablePanel>
              <BalancesTab />
            </ScrollTablePanel>
          </TabsContent>
          <TabsContent value="algo" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <ErrorBoundary name="Algo Bot">
              <AlgoTab />
            </ErrorBoundary>
          </TabsContent>
          <TabsContent value="reconcile" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <ReconciliationTab />
          </TabsContent>
          <TabsContent value="bots" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <BotHistoryTab />
          </TabsContent>
          <TabsContent value="ticks" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <TickViewerTab />
          </TabsContent>
          <TabsContent value="equity" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            <EquityCurveTab />
          </TabsContent>
          <TabsContent value="history" className="dock-tab-body mt-0 overflow-hidden data-[state=inactive]:hidden">
            {!historyFullscreen && <TradeHistoryContent embedded />}
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
          <TradeHistoryContent embedded={false} onClose={() => setHistoryFullscreen(false)} />
        </SheetContent>
      </Sheet>

      <BotDetailDrawer
        open={botDrawerOpen && !!selectedBotId}
        onOpenChange={setBotDrawerOpen}
        onStop={(bot_id) => sendAction(Action.BOT_STOP, { bot_id })}
        onPause={(bot_id) => sendAction(Action.BOT_PAUSE, { bot_id })}
        onResume={(bot_id) => sendAction(Action.BOT_RESUME, { bot_id })}
      />
    </>
    </ErrorBoundary>
  );
}
