/**
 * ResizableDock.jsx
 * Bottom docked panel with 6 tabs:
 *   Positions | Orders | Balances | Algo Bot | History | Equity Curve
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
} from 'lucide-react';
import EquityCurveTab from './EquityCurveTab';
import TradeHistoryContent from './TradeHistoryPanel';
import BacktestMiniChart from './BacktestMiniChart';
import BotDetailDrawer from './BotDetailDrawer';
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

const DOCK_MIN = 160;
const DOCK_MAX = 560;
const DOCK_DEFAULT = 320;
const STORAGE_KEY = 'terminal_dock_height';

// ── Tiny formatters ───────────────────────────────────────────────
const priceDecimals = (sym, price) =>
  (sym?.includes('XRP') || sym?.includes('ADA') || sym?.includes('DOGE') || (price != null && price < 2.0)) ? 4 : 2;

const fmtP = (n, d = 2) =>
  n == null ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

// ── Position Row ──────────────────────────────────────────────────
const PositionRow = React.memo(function PositionRow({ sym, pos }) {
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
  const entries = Object.entries(positions);

  if (entries.length === 0) {
    return <WidgetEmpty icon={Briefcase} message="No open positions" />;
  }

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
          <PositionRow key={sym} sym={sym} pos={pos} />
        ))}
      </tbody>
    </table>
  );
}

// ── Orders Tab ────────────────────────────────────────────────────
function OrdersTab() {
  const orders = useStore(state => state.orders);
  const active = orders.filter(o => o.status === 'PENDING');

  if (active.length === 0) {
    return <WidgetEmpty icon={List} message="No pending orders" />;
  }

  return (
    <table className="terminal-table min-w-[640px]">
      <thead>
        <tr>
          <th>Symbol</th>
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
          return (
            <tr key={ord.id}>
              <td className="font-bold">{ord.symbol}</td>
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
    activeBots, botStrategy, botConfig, activeSymbol, symbolsList,
    setBotStrategy, updateBotConfig, clearBotLogs, botLogs,
    strategyTemplates, backtestResults, setChartInteractionMode,
    isLive, allowLiveBots, terminalMode, setActiveSymbol,
    selectedBotId, setSelectedBotId, botDetail, setBotDetail,
  } = useStore();

  const liveBotsBlocked = isLive && !allowLiveBots;
  const runningCount = activeBots.filter(b => b.status === 'RUNNING').length;
  const [deployOpen, setDeployOpen] = useState(false);
  const [stopAllOpen, setStopAllOpen] = useState(false);
  const [botDrawerOpen, setBotDrawerOpen] = useState(false);

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
      timeframe: "1m",
      allocation: botConfig.allocation,
      config: botConfig
    });
  };

  const handleRunBacktest = () => {
    sendAction(Action.RUN_BACKTEST, {
      strategy: botStrategy,
      symbol: activeSymbol,
      config: botConfig
    });
  };

  const selectTemplate = (template) => {
    setBotStrategy(template.strategy);
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
    if (selectedBotId && activeBots.some(b => b.id === selectedBotId)) {
      sendAction(Action.BOT_GET_DETAIL, { bot_id: selectedBotId });
    } else if (selectedBotId && !activeBots.some(b => b.id === selectedBotId)) {
      setSelectedBotId(null);
      setBotDetail(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedBotId, activeBots.length]);

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

      <section className="algo-tab__panel algo-tab__panel--deploy">
        <header className="algo-tab__panel-header">
          <div className="algo-tab__panel-title">
            <Settings size={13} className="text-primary" aria-hidden />
            Deploy Bot
          </div>
        </header>
        <div className="algo-tab__scroll scroll-panel-y scroll-panel-y-0 algo-tab__deploy-body">
          <div className="flex flex-col gap-2.5">
            <div className="space-y-1.5">
              <Label className="algo-field-label">Symbol</Label>
              <Select value={activeSymbol} onValueChange={setActiveSymbol}>
                <SelectTrigger className="h-8 w-full text-xs" aria-label="Bot symbol">
                  <SelectValue placeholder="Select symbol" />
                </SelectTrigger>
                <SelectContent className="max-h-56">
                  {symbolsList.map(sym => (
                    <SelectItem key={sym} value={sym} className="text-xs">{sym}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label className="algo-field-label">Strategy Templates</Label>
              <div className="algo-template-grid">
                {strategyTemplates.map(t => (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => selectTemplate(t)}
                    className={cn(
                      'algo-template-btn',
                      botStrategy === t.strategy && 'algo-template-btn--active',
                    )}
                  >
                    <div className="algo-template-btn__name">{t.name}</div>
                    <div className="algo-template-btn__meta">
                      {t.strategy} · ${t.allocation} · SL {t.config.trailing_stop_percent || 0}%
                    </div>
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
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

            {backtestResults && (
              <div className={cn(
                'algo-backtest-lab',
                (backtestResults.total_pnl ?? 0) < 0 && 'algo-backtest-lab--down',
              )}>
                <div className="algo-backtest-lab__title">7-Day Backtest Lab</div>
                <div className="algo-backtest-metrics">
                  <div>Win Rate: <span className="text-foreground">{backtestResults.win_rate}%</span></div>
                  <div>Est PnL: <span className={backtestResults.total_pnl >= 0 ? 'text-trading-up' : 'text-trading-down'}>${backtestResults.total_pnl}</span></div>
                  <div>Max DD: <span className="text-trading-down">{backtestResults.max_drawdown}%</span></div>
                  <div>Trades: <span className="text-foreground">{backtestResults.trade_count}</span></div>
                </div>
                <BacktestMiniChart
                  equityCurve={backtestResults.equity_curve}
                  totalPnl={backtestResults.total_pnl}
                />
                {backtestResults.trades?.length > 0 && (
                  <div className="algo-backtest-trades scroll-panel-y scroll-panel-y-0 max-h-36">
                    <table className="terminal-table m-0 text-[0.58rem]">
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
                        {backtestResults.trades.filter(t => t.is_exit).slice(-12).reverse().map((t, i) => (
                          <tr key={`${t.time}-${i}`}>
                            <td className="text-muted-foreground">{t.time ? new Date(t.time * 1000).toLocaleString() : '—'}</td>
                            <td>{t.side}{t.is_exit ? ' ↗' : ''}</td>
                            <td className="num-mono text-right">{Number(t.quantity).toFixed(4)}</td>
                            <td className="num-mono text-right">{Number(t.price).toFixed(2)}</td>
                            <td className={cn('num-mono text-right', t.pnl != null && (t.pnl >= 0 ? 'text-trading-up' : 'text-trading-down'))}>
                              {t.pnl != null ? `$${Number(t.pnl).toFixed(2)}` : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
        <footer className="algo-tab__panel-footer">
          <Button variant="outline" size="sm" className="flex-1 text-xs" onClick={handleRunBacktest}>
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
            <div><span className="text-muted-foreground">Strategy:</span> <strong>{botStrategy}</strong></div>
            <div><span className="text-muted-foreground">Symbol:</span> <strong>{activeSymbol}</strong></div>
            <div><span className="text-muted-foreground">Allocation:</span> <strong>${botConfig?.allocation?.toLocaleString() ?? 0}</strong></div>
            <div><span className="text-muted-foreground">Timeframe:</span> <strong>1m (closed-bar signals)</strong></div>
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
                  <td colSpan="7" className="algo-table-empty">
                    No active bots. Pick a template and deploy.
                  </td>
                </tr>
              ) : (
                activeBots.map(bot => (
                  <tr
                    key={bot.id}
                    className={cn('algo-bot-row', selectedBotId === bot.id && 'row-active')}
                    onClick={() => selectBot(bot.id)}
                  >
                    <td className="font-bold">{bot.symbol}</td>
                    <td className="text-xs text-secondary-foreground">{bot.strategy}</td>
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
                ))
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

        <div className="algo-tab__scroll scroll-panel-y scroll-panel-y-0">
          <div className="algo-tab__log-list">
            {botLogs.length === 0 ? (
              <WidgetEmpty icon={Cpu} message="Bot console is empty" className="min-h-[80px]" />
            ) : botLogs.map((log, i) => (
              <div key={i} className={logLineClass(log)}>{log}</div>
            ))}
          </div>
        </div>
      </section>

      <BotDetailDrawer
        open={botDrawerOpen && !!selectedBotId}
        onOpenChange={setBotDrawerOpen}
        onStop={handleStopBot}
        onPause={handlePauseBot}
        onResume={handleResumeBot}
      />
    </div>
  );
}

// ── Main ResizableDock ────────────────────────────────────────────
export default function ResizableDock({ setDockHeight: setParentDockHeight }) {
  const positions = useStore(state => state.positions);
  const orders = useStore(state => state.orders);
  const tradeHistory = useStore(state => state.tradeHistory);
  const isBotRunning = useStore(state => state.isBotRunning);
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
    { id: 'history',   label: 'History',   icon: Activity, badge: tradeHistory.length || null },
    { id: 'equity',    label: 'Equity Curve', icon: TrendingUp },
  ];

  return (
    <>
      <div className="bottom-dock flex flex-col" style={{ gridArea: 'dock', height: dockH }}>
        <div className="dock-resize-handle" onMouseDown={onMouseDown} />

        <Tabs value={activeTab} onValueChange={setActiveTab} className="flex min-h-0 flex-1 flex-col gap-0">
          <div className="flex shrink-0 items-center border-b border-border bg-muted/20 pr-1 pt-1">
          <div className="scroll-fade-x flex min-w-0 flex-1 items-center">
            <TabsList variant="line" className="scroll-panel-x no-scrollbar h-9 min-w-0 flex-1 justify-start rounded-none border-0 bg-transparent px-1">
              {TABS.map(tab => {
                const Icon = tab.icon;
                return (
                  <TabsTrigger key={tab.id} value={tab.id} className="shrink-0 px-2 text-xs xl:px-3" title={tab.label}>
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

          <TabsContent value="positions" className="mt-0 flex min-h-0 flex-1 flex-col overflow-hidden">
            <ScrollTablePanel>
              <PositionsTab />
            </ScrollTablePanel>
          </TabsContent>
          <TabsContent value="orders" className="mt-0 flex min-h-0 flex-1 flex-col overflow-hidden">
            <ScrollTablePanel>
              <OrdersTab />
            </ScrollTablePanel>
          </TabsContent>
          <TabsContent value="balances" className="mt-0 flex min-h-0 flex-1 flex-col overflow-hidden">
            <ScrollTablePanel>
              <BalancesTab />
            </ScrollTablePanel>
          </TabsContent>
          <TabsContent value="algo" className="mt-0 flex min-h-0 flex-1 flex-col overflow-hidden">
            <AlgoTab />
          </TabsContent>
          <TabsContent value="equity" className="mt-0 flex min-h-0 flex-1 flex-col overflow-hidden">
            <EquityCurveTab />
          </TabsContent>
          <TabsContent value="history" className="mt-0 flex min-h-0 flex-1 flex-col overflow-hidden">
            {!historyFullscreen && <TradeHistoryContent embedded />}
          </TabsContent>
        </Tabs>
      </div>

      {/* Expanded history sheet */}
      <Sheet open={historyFullscreen && activeTab === 'history'} onOpenChange={setHistoryFullscreen}>
        <SheetContent
          side="bottom"
          showCloseButton={false}
          className="h-[72vh] max-h-[85vh] gap-0 rounded-t-xl border-t p-0 sm:max-w-full"
        >
          <TradeHistoryContent embedded={false} onClose={() => setHistoryFullscreen(false)} />
        </SheetContent>
      </Sheet>
    </>
  );
}
