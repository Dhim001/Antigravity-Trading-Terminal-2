import React, { useState } from 'react';
import { useStore } from '../store/useStore';
import { sendWebSocketAction } from '../services/websocket';
import { cn } from '@/lib/utils';
import {
  Briefcase, List, Landmark, XSquare, Cpu, Play, Square, Trash2, Settings,
} from 'lucide-react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { ScrollArea } from '@/components/ui/scroll-area';
import { WidgetEmpty } from './WidgetShell';

const priceDecimals = (sym, price) =>
  (sym?.includes('XRP') || sym?.includes('ADA') || sym?.includes('DOGE') || (price != null && price < 2.0)) ? 4 : 2;

export default function PositionManagerWidget() {
  const {
    positions, orders, balances, tickerData, activeSymbol,
    isBotRunning, botStrategy, botConfig, botLogs,
    startBot, stopBot, setBotStrategy, updateBotConfig, clearBotLogs,
  } = useStore();

  const [activeTab, setActiveTab] = useState('positions');

  const handleCancelOrder = (orderId) => {
    sendWebSocketAction('cancel_order', { order_id: orderId });
  };

  const activeOrders = orders.filter(o => o.status === 'PENDING');
  const positionEntries = Object.entries(positions);

  return (
    <div className="widget-card flex h-full flex-col overflow-hidden">
      <Tabs value={activeTab} onValueChange={setActiveTab} className="flex min-h-0 flex-1 flex-col gap-0">
        <TabsList variant="line" className="h-10 shrink-0 justify-start rounded-none border-b border-border bg-muted/20 px-2">
          <TabsTrigger value="positions" className="gap-1.5 px-3 text-xs">
            <Briefcase data-icon="inline-start" />
            Positions
            <Badge variant="secondary" className="h-4 min-w-4 px-1 text-[0.58rem]">
              {positionEntries.length}
            </Badge>
          </TabsTrigger>
          <TabsTrigger value="orders" className="gap-1.5 px-3 text-xs">
            <List data-icon="inline-start" />
            Orders
            <Badge variant="secondary" className="h-4 min-w-4 px-1 text-[0.58rem]">
              {activeOrders.length}
            </Badge>
          </TabsTrigger>
          <TabsTrigger value="balances" className="gap-1.5 px-3 text-xs">
            <Landmark data-icon="inline-start" />
            Balances
          </TabsTrigger>
          <TabsTrigger value="algo" className="gap-1.5 px-3 text-xs">
            <Cpu data-icon="inline-start" className={isBotRunning ? 'text-trading-up' : ''} />
            Algo Trading
            {isBotRunning && (
              <span className="size-1.5 rounded-full bg-trading-up shadow-[0_0_5px_var(--color-up)]" />
            )}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="positions" className="mt-0 min-h-0 flex-1 overflow-y-auto">
          {positionEntries.length === 0 ? (
            <WidgetEmpty icon={Briefcase} message="No active positions" />
          ) : (
            <table className="terminal-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th className="text-right">Size</th>
                  <th className="text-right">Avg Price</th>
                  <th className="text-right">Mark Price</th>
                  <th className="text-right">Unrealized PnL</th>
                </tr>
              </thead>
              <tbody>
                {positionEntries.map(([symbol, pos]) => {
                  const markPrice = tickerData[symbol]?.price || pos.avg_price;
                  const uPnl = pos.size * (markPrice - pos.avg_price);
                  const isLong = pos.size >= 0;
                  const dec = priceDecimals(symbol, Math.max(markPrice, pos.avg_price));

                  return (
                    <tr key={symbol}>
                      <td className="font-semibold">{symbol}</td>
                      <td>
                        <Badge variant={isLong ? 'buy' : 'sell'}>{isLong ? 'LONG' : 'SHORT'}</Badge>
                      </td>
                      <td className="num-mono text-right">
                        {Math.abs(pos.size).toLocaleString(undefined, { minimumFractionDigits: 4 })}
                      </td>
                      <td className="num-mono text-right">
                        <div>{pos.avg_price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}</div>
                        {(pos.stop_loss_price || pos.take_profit_price) && (
                          <div className="mt-0.5 flex justify-end gap-2 text-[0.68rem] text-muted-foreground">
                            {pos.stop_loss_price && (
                              <span className="text-trading-down">
                                SL: {pos.stop_loss_price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
                              </span>
                            )}
                            {pos.take_profit_price && (
                              <span className="text-trading-up">
                                TP: {pos.take_profit_price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
                              </span>
                            )}
                          </div>
                        )}
                      </td>
                      <td className="num-mono text-right">
                        {markPrice.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
                      </td>
                      <td className={cn(
                        'num-mono text-right font-semibold',
                        uPnl >= 0 ? 'text-trading-up' : 'text-trading-down',
                      )}>
                        {uPnl >= 0 ? '+' : ''}{uPnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </TabsContent>

        <TabsContent value="orders" className="mt-0 min-h-0 flex-1 overflow-y-auto">
          {activeOrders.length === 0 ? (
            <WidgetEmpty icon={List} message="No active pending orders" />
          ) : (
            <table className="terminal-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Type</th>
                  <th>Side</th>
                  <th className="text-right">Price</th>
                  <th className="text-right">Quantity</th>
                  <th className="text-center">Action</th>
                </tr>
              </thead>
              <tbody>
                {activeOrders.map((order) => {
                  const dec = priceDecimals(order.symbol, order.price);
                  const qtyDecimals = order.symbol.includes('USDT') || order.symbol.includes('USD') ? 4 : 2;
                  const isBuy = order.side === 'BUY';

                  return (
                    <tr key={order.id}>
                      <td className="font-semibold">{order.symbol}</td>
                      <td className="text-xs text-secondary-foreground">{order.type}</td>
                      <td><Badge variant={isBuy ? 'buy' : 'sell'}>{order.side}</Badge></td>
                      <td className="num-mono text-right">
                        {order.price ? order.price.toLocaleString(undefined, { minimumFractionDigits: dec }) : 'MKT'}
                      </td>
                      <td className="num-mono text-right">{order.quantity.toFixed(qtyDecimals)}</td>
                      <td className="text-center">
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => handleCancelOrder(order.id)}
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
          )}
        </TabsContent>

        <TabsContent value="balances" className="mt-0 min-h-0 flex-1 overflow-y-auto">
          {Object.keys(balances).length === 0 ? (
            <WidgetEmpty message="Loading balances…" />
          ) : (
            <table className="terminal-table">
              <thead>
                <tr>
                  <th>Asset</th>
                  <th className="text-right">Total Balance</th>
                  <th className="text-right">Locked in Orders</th>
                  <th className="text-right">Available</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(balances).map(([asset, bal]) => {
                  const available = bal.balance - bal.locked;
                  const decimalPlaces = asset === 'USD' || asset === 'USDT' ? 2 : 4;

                  return (
                    <tr key={asset}>
                      <td className="font-semibold">{asset}</td>
                      <td className="num-mono text-right">
                        {bal.balance.toLocaleString(undefined, { minimumFractionDigits: decimalPlaces, maximumFractionDigits: decimalPlaces })}
                      </td>
                      <td className="num-mono text-right text-muted-foreground">
                        {bal.locked.toLocaleString(undefined, { minimumFractionDigits: decimalPlaces, maximumFractionDigits: decimalPlaces })}
                      </td>
                      <td className={cn(
                        'num-mono text-right font-semibold',
                        available > 0 ? 'text-foreground' : 'text-muted-foreground',
                      )}>
                        {available.toLocaleString(undefined, { minimumFractionDigits: decimalPlaces, maximumFractionDigits: decimalPlaces })}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </TabsContent>

        <TabsContent value="algo" className="mt-0 min-h-0 flex-1 overflow-hidden p-3">
          <div className="grid h-full min-h-0 grid-cols-[320px_1fr] gap-3 overflow-hidden">
            <Card size="sm" className="flex min-h-0 flex-col overflow-y-auto rounded-lg py-3 shadow-none">
              <CardHeader className="border-b border-border pb-2">
                <CardTitle className="flex items-center gap-2 text-xs uppercase tracking-wide">
                  <Settings size={14} className="text-primary" />
                  Bot Parameters
                </CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-3 px-3">
                <div className="space-y-1.5">
                  <Label className="text-[0.7rem]">Select Strategy</Label>
                  <Select value={botStrategy} onValueChange={setBotStrategy} disabled={isBotRunning}>
                    <SelectTrigger size="sm" className="w-full text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="EMA_CROSS">EMA Crossover (9/21)</SelectItem>
                      <SelectItem value="RSI_MEAN_REV">RSI Mean Reversion (14)</SelectItem>
                      <SelectItem value="MACD_TREND">MACD Trend Follower</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-1.5">
                  <Label className="text-[0.7rem]">Order Size (Quantity)</Label>
                  <Input
                    type="number"
                    step="any"
                    value={botConfig?.quantity || ''}
                    disabled={isBotRunning}
                    onChange={e => updateBotConfig({ quantity: parseFloat(e.target.value) || 0 })}
                    className="h-8 text-xs"
                  />
                </div>

                <div className="space-y-1.5">
                  <Label className="text-[0.7rem]">Auto Stop Loss (%)</Label>
                  <Input
                    type="number"
                    step="0.1"
                    value={botConfig?.stopLossPercent || ''}
                    disabled={isBotRunning}
                    onChange={e => {
                      const val = parseFloat(e.target.value) || 0;
                      updateBotConfig({ stopLossPercent: val });
                      if (positions[activeSymbol]?.size) {
                        sendWebSocketAction('update_position_sl_tp', {
                          symbol: activeSymbol,
                          stop_loss_percent: val,
                          take_profit_percent: botConfig.takeProfitPercent,
                        });
                      }
                    }}
                    className="h-8 text-xs"
                  />
                </div>

                <div className="space-y-1.5">
                  <Label className="text-[0.7rem]">Auto Take Profit (%)</Label>
                  <Input
                    type="number"
                    step="0.1"
                    value={botConfig?.takeProfitPercent || ''}
                    disabled={isBotRunning}
                    onChange={e => {
                      const val = parseFloat(e.target.value) || 0;
                      updateBotConfig({ takeProfitPercent: val });
                      if (positions[activeSymbol]?.size) {
                        sendWebSocketAction('update_position_sl_tp', {
                          symbol: activeSymbol,
                          stop_loss_percent: botConfig.stopLossPercent,
                          take_profit_percent: val,
                        });
                      }
                    }}
                    className="h-8 text-xs"
                  />
                </div>

                <Button
                  variant={isBotRunning ? 'destructive' : 'buy'}
                  className="mt-auto w-full font-bold"
                  onClick={() => (isBotRunning ? stopBot() : startBot())}
                >
                  {isBotRunning ? (
                    <><Square data-icon="inline-start" fill="currentColor" /> STOP ALGO BOT</>
                  ) : (
                    <><Play data-icon="inline-start" fill="currentColor" /> START ALGO BOT</>
                  )}
                </Button>
              </CardContent>
            </Card>

            <Card size="sm" className="flex min-h-0 flex-col overflow-hidden rounded-lg bg-background/80 py-3 shadow-none">
              <div className="mb-2 flex shrink-0 items-center justify-between border-b border-border px-3 pb-2">
                <div className="flex items-center gap-2">
                  <Cpu size={14} className={isBotRunning ? 'text-trading-up' : 'text-muted-foreground'} />
                  <span className="text-xs font-bold uppercase tracking-wide">Bot Operation Log</span>
                  <Badge variant={isBotRunning ? 'buy' : 'secondary'}>
                    {isBotRunning ? `SCANNING ${activeSymbol}` : 'IDLE'}
                  </Badge>
                </div>
                <Button variant="ghost" size="icon-sm" onClick={clearBotLogs} title="Clear console">
                  <Trash2 />
                </Button>
              </div>

              <ScrollArea className="min-h-0 flex-1 px-3">
                <div className="flex flex-col-reverse gap-1 font-mono text-[0.72rem]">
                  {botLogs.length === 0 ? (
                    <WidgetEmpty icon={Cpu} message="Bot console is empty. Activate the bot to see logs." className="min-h-[100px]" />
                  ) : botLogs.map((log, idx) => {
                    let c = 'text-muted-foreground';
                    if (log.includes('BUY') || log.includes('Profit') || log.includes('Success')) c = 'text-trading-up';
                    else if (log.includes('SELL') || log.includes('Loss') || log.includes('Stop Loss') || log.includes('Error')) c = 'text-trading-down';
                    else if (log.includes('Running') || log.includes('Config')) c = 'text-primary';
                    return <div key={idx} className={cn(c, 'whitespace-pre-wrap leading-snug')}>{log}</div>;
                  })}
                </div>
              </ScrollArea>
            </Card>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
