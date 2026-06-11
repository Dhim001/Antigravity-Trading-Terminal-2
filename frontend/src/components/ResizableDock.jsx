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
import { sendWebSocketAction } from '../services/websocket';
import {
  Briefcase, List, Landmark, Cpu, Activity, TrendingUp,
  Play, Settings, Trash2, XSquare, Maximize2, Minimize2,
} from 'lucide-react';
import EquityCurveTab from './EquityCurveTab';
import TradeHistoryContent from './TradeHistoryPanel';
import { WidgetEmpty, ScrollTablePanel } from './WidgetShell';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import {
  InputGroup, InputGroupAddon, InputGroupInput, InputGroupText,
} from '@/components/ui/input-group';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';

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
    sendWebSocketAction('place_order', {
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
                  onClick={() => sendWebSocketAction('cancel_order', { order_id: ord.id })}
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
    selectedBotId, setSelectedBotId,
  } = useStore();

  const handleCreateBot = () => {
    if (!botConfig.allocation || botConfig.allocation <= 0) {
      toast.error('Enter a valid capital allocation amount');
      return;
    }
    
    sendWebSocketAction("bot_create", {
      strategy: botStrategy,
      symbol: activeSymbol,
      timeframe: "1m",
      allocation: botConfig.allocation,
      config: botConfig
    });
  };

  const handleRunBacktest = () => {
    sendWebSocketAction("run_backtest", {
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
    sendWebSocketAction("bot_stop", { bot_id });
  };

  return (
    <div className="scroll-panel-y scroll-panel-y-0 grid h-full min-h-0 grid-cols-1 gap-3 p-3 xl:grid-cols-[minmax(240px,320px)_1fr_minmax(220px,300px)] xl:overflow-hidden">
      <Card size="sm" className="flex min-h-0 flex-col gap-2.5 rounded-lg py-3 shadow-none xl:scroll-panel-y xl:scroll-panel-y-0">
        <CardHeader className="border-b border-border pb-2">
          <CardTitle className="icon-label-loose text-xs uppercase tracking-wide">
            <Settings size={13} className="text-primary" aria-hidden />
            Deploy Bot
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2.5 px-3">
        <div className="flex flex-col gap-1.5">
          <Label className="text-[0.62rem] uppercase tracking-wide text-muted-foreground">Strategy Templates</Label>
          <div className="grid grid-cols-1 gap-1.5">
            {strategyTemplates.map(t => (
              <button
                key={t.id}
                type="button"
                onClick={() => selectTemplate(t)}
                className={cn(
                  'rounded-md border px-2.5 py-2 text-left transition-colors',
                  botStrategy === t.strategy
                    ? 'border-primary/50 bg-primary/10'
                    : 'border-border bg-muted/20 hover:bg-muted/40',
                )}
              >
                <div className={cn('text-xs font-bold', botStrategy === t.strategy ? 'text-primary' : 'text-foreground')}>
                  {t.name}
                </div>
                <div className="mt-0.5 text-[0.62rem] text-muted-foreground">
                  Alloc: ${t.allocation} • Trail SL: {t.config.trailing_stop_percent || 0}%
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-1.5">
          <Label className="text-[0.62rem] uppercase tracking-wide text-muted-foreground">Capital Allocation (USD/USDT)</Label>
          <InputGroup className="h-8">
            <InputGroupInput
              type="number"
              step="any"
              value={botConfig?.allocation || ''}
              onChange={e => updateBotConfig({ allocation: parseFloat(e.target.value) || 0 })}
              className="text-xs"
            />
            <InputGroupAddon align="inline-end">
              <InputGroupText className="text-xs">$</InputGroupText>
            </InputGroupAddon>
          </InputGroup>
          <span className="block text-[0.65rem] text-muted-foreground">
            Risk is dynamically managed at 1% of total account balance using ATR stops.
          </span>
        </div>

        {backtestResults && (
          <div className="rounded-md border border-trading-up/25 bg-trading-up/5 p-2 text-[0.62rem]">
            <div className="mb-1 font-bold text-trading-up">7-Day Backtest Preview</div>
            <div className="grid grid-cols-2 gap-1">
              <div>Win Rate: <span className="text-foreground">{backtestResults.win_rate}%</span></div>
              <div>Est PnL: <span className={backtestResults.total_pnl >= 0 ? 'text-trading-up' : 'text-trading-down'}>${backtestResults.total_pnl}</span></div>
              <div>Max DD: <span className="text-trading-down">{backtestResults.max_drawdown}%</span></div>
              <div>Trades: <span className="text-foreground">{backtestResults.trade_count}</span></div>
            </div>
          </div>
        )}

        <div className="mt-auto flex gap-[var(--icon-gap-loose)]">
          <Button variant="outline" size="sm" className="flex-1 text-xs" onClick={handleRunBacktest}>
            <Activity data-icon="inline-start" />
            BACKTEST
          </Button>
          <Button variant="buy" size="sm" className="flex-[1.5] text-xs font-bold" onClick={handleCreateBot}>
            <Play data-icon="inline-start" />
            DEPLOY
          </Button>
        </div>
        </CardContent>
      </Card>

      <Card size="sm" className="flex min-h-[200px] flex-col overflow-hidden rounded-lg py-0 shadow-none xl:min-h-0">
        <ScrollTablePanel>
        <table className="terminal-table m-0 min-w-[560px]">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Strategy</th>
              <th className="text-right">Allocation</th>
              <th className="text-center">Status</th>
              <th className="text-center">Action</th>
            </tr>
          </thead>
          <tbody>
            {activeBots.length === 0 ? (
              <tr>
                <td colSpan="5" className="py-5 text-center text-muted-foreground">
                  No active bots.
                </td>
              </tr>
            ) : (
              activeBots.map(bot => (
                <tr key={bot.id}>
                  <td className="font-bold">{bot.symbol}</td>
                  <td className="text-xs text-secondary-foreground">{bot.strategy}</td>
                  <td className="num-mono text-right">${bot.allocation.toLocaleString()}</td>
                  <td className="text-center">
                    <Badge variant={bot.status === 'RUNNING' ? 'buy' : 'sell'}>{bot.status}</Badge>
                  </td>
                  <td className="text-center">
                    {bot.status === 'RUNNING' && (
                      <div className="flex justify-center gap-1">
                        <Button
                          variant="outline"
                          size="xs"
                          onClick={() => setChartInteractionMode('edit_sl')}
                          title="Click to set SL on chart"
                        >
                          SET SL
                        </Button>
                        <Button variant="destructive" size="xs" onClick={() => handleStopBot(bot.id)}>
                          STOP
                        </Button>
                      </div>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        </ScrollTablePanel>
      </Card>

      <Card size="sm" className="flex min-h-[160px] flex-col overflow-hidden rounded-lg bg-background/80 py-3 shadow-none xl:min-h-0">
        <div className="mb-2 flex shrink-0 items-center justify-between border-b border-border px-3 pb-2">
          <div className="icon-label-loose">
            <Cpu size={13} className={activeBots.length > 0 ? 'text-trading-up' : 'text-muted-foreground'} aria-hidden />
            <span className="text-xs font-bold uppercase tracking-wide">Bot Log</span>
            <Badge variant={activeBots.length > 0 ? 'buy' : 'secondary'}>
              {activeBots.length > 0 ? `${activeBots.length} ACTIVE` : 'IDLE'}
            </Badge>
          </div>
          <Button variant="ghost" size="icon-sm" onClick={clearBotLogs} title="Clear log">
            <Trash2 />
          </Button>
        </div>

        <ScrollArea className="min-h-0 flex-1 px-3">
          <div className="flex flex-col-reverse gap-1 font-mono text-[0.62rem] text-muted-foreground">
            {botLogs.length === 0 ? (
              <WidgetEmpty icon={Cpu} message="Bot console is empty" className="min-h-[80px]" />
            ) : botLogs.map((log, i) => {
              let c = 'text-muted-foreground';
              if (log.includes('BUY') || log.includes('SUCCESS')) c = 'text-trading-up';
              else if (log.includes('SELL') || log.includes('ERROR') || log.includes('STOP')) c = 'text-trading-down';
              else if (log.includes('WARN')) c = 'text-trading-warn';
              else if (log.includes('INFO') || log.includes('started')) c = 'text-primary';
              return <div key={i} className={cn(c, 'whitespace-pre-wrap leading-relaxed')}>{log}</div>;
            })}
          </div>
        </ScrollArea>
      </Card>
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
                      <span className="size-1.5 rounded-full bg-trading-up shadow-[0_0_5px_var(--color-up)]" />
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
