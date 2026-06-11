import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { sendWebSocketAction } from '../services/websocket';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Slider } from '@/components/ui/slider';
import { Separator } from '@/components/ui/separator';
import { Card, CardContent } from '@/components/ui/card';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Cpu, Database, DollarSign, RefreshCw, ShieldAlert, Sliders } from 'lucide-react';

export default function SystemControlPanel({ isOpen, onClose }) {
  const { systemStats, activeSymbol, isLive, symbolsList } = useStore();
  const [activeTab, setActiveTab] = useState('simulation');

  const getAvailableAssets = () => {
    const assets = new Set(['USD', 'USDT']);
    if (symbolsList && Array.isArray(symbolsList)) {
      symbolsList.forEach(sym => {
        const isCrypto = sym.includes('USDT');
        const asset = isCrypto ? sym.replace('USDT', '') : sym;
        assets.add(asset);
      });
    }
    return Array.from(assets).sort();
  };

  const [volatility, setVolatility] = useState(systemStats.volatility_multiplier || 1.0);
  const [tickInterval, setTickInterval] = useState(systemStats.tick_interval || 0.25);
  const [bias, setBias] = useState('RANDOM');
  const [seedAsset, setSeedAsset] = useState('USD');
  const [seedAmount, setSeedAmount] = useState('10000');
  const [isResetting, setIsResetting] = useState(false);

  useEffect(() => {
    if (systemStats) {
      if (systemStats.volatility_multiplier !== undefined) setVolatility(systemStats.volatility_multiplier);
      if (systemStats.tick_interval !== undefined) setTickInterval(systemStats.tick_interval);
    }
  }, [systemStats]);

  useEffect(() => {
    if (isOpen) sendWebSocketAction('admin_get_stats');
  }, [isOpen, activeTab]);

  const handleUpdateSimulation = (updates = {}) => {
    if (isLive) return;
    sendWebSocketAction('admin_set_simulation', {
      tick_interval: updates.tickInterval !== undefined ? updates.tickInterval : tickInterval,
      volatility_multiplier: updates.volatility !== undefined ? updates.volatility : volatility,
      symbol: activeSymbol,
      bias: updates.bias !== undefined ? updates.bias : bias,
    });
  };

  const handleSeedBalance = () => {
    const amount = parseFloat(seedAmount);
    if (isNaN(amount) || amount <= 0) {
      toast.error('Please enter a valid balance amount');
      return;
    }
    sendWebSocketAction('admin_seed_balance', { asset: seedAsset, amount });
    toast.success(`Credited ${amount} ${seedAsset}`);
  };

  const handleNuclearReset = () => {
    setIsResetting(true);
    sendWebSocketAction('admin_reset_system');
    toast.info('System reset initiated…');
    setTimeout(() => {
      setIsResetting(false);
      onClose();
    }, 1500);
  };

  const handleEmergencyStop = () => {
    sendWebSocketAction('admin_emergency_stop');
    toast.warning('Emergency liquidation executed');
    onClose();
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="gap-0 overflow-hidden p-0 sm:max-w-[540px]" showCloseButton>
        <DialogHeader className="border-b border-border bg-muted/20 px-5 py-4">
          <DialogTitle className="flex items-center gap-2.5 text-sm font-bold tracking-wide">
            <Cpu className="size-4 text-primary" />
            SYSTEM ADMIN CONTROL PANEL
          </DialogTitle>
        </DialogHeader>

        <Tabs value={activeTab} onValueChange={setActiveTab} className="gap-0">
          <TabsList variant="line" className="h-auto w-full justify-start rounded-none border-b border-border bg-transparent px-2">
            <TabsTrigger value="simulation" className="gap-1.5 px-4 py-3 text-xs">
              <Sliders data-icon="inline-start" />
              Market Simulation
            </TabsTrigger>
            <TabsTrigger value="account" className="gap-1.5 px-4 py-3 text-xs">
              <DollarSign data-icon="inline-start" />
              Account Admin
            </TabsTrigger>
            <TabsTrigger value="diagnostics" className="gap-1.5 px-4 py-3 text-xs">
              <Database data-icon="inline-start" />
              Diagnostics
            </TabsTrigger>
          </TabsList>

          <div className="min-h-[260px] px-5 py-6">
            <TabsContent value="simulation" className="mt-0 flex flex-col gap-5">
              {isLive && (
                <Alert variant="destructive">
                  <ShieldAlert data-icon="inline-start" />
                  <AlertDescription className="text-xs leading-relaxed">
                    Simulation drift controls are locked in live trading. Volatility and tick rates are governed entirely by real-time exchange feeds.
                  </AlertDescription>
                </Alert>
              )}

              <div className={isLive ? 'pointer-events-none flex flex-col gap-5 opacity-35' : 'flex flex-col gap-5'}>
                <div>
                  <Label className="mb-2 block text-xs uppercase tracking-wide text-muted-foreground">
                    Drift Override for {activeSymbol}
                  </Label>
                  <ToggleGroup
                    type="single"
                    value={bias}
                    onValueChange={(v) => { if (v) { setBias(v); handleUpdateSimulation({ bias: v }); } }}
                    className="grid w-full grid-cols-3 gap-1"
                    spacing={0}
                  >
                    <ToggleGroupItem value="UP" variant="buy" className="text-xs font-bold">
                      Bullish (Pump)
                    </ToggleGroupItem>
                    <ToggleGroupItem value="DOWN" variant="sell" className="text-xs font-bold">
                      Bearish (Dump)
                    </ToggleGroupItem>
                    <ToggleGroupItem value="RANDOM" variant="outline" className="text-xs font-bold">
                      Random Walk
                    </ToggleGroupItem>
                  </ToggleGroup>
                </div>

                <div>
                  <div className="mb-2 flex justify-between">
                    <Label className="text-xs uppercase tracking-wide text-muted-foreground">Volatility Multiplier</Label>
                    <span className="num-mono text-sm font-bold text-trading-accent">{volatility.toFixed(1)}x</span>
                  </div>
                  <Slider
                    min={0.2}
                    max={5}
                    step={0.2}
                    value={[volatility]}
                    onValueChange={([val]) => {
                      setVolatility(val);
                      handleUpdateSimulation({ volatility: val });
                    }}
                  />
                  <div className="mt-1 flex justify-between text-[0.65rem] text-muted-foreground">
                    <span>0.2x (Stable)</span>
                    <span>1.0x (Normal)</span>
                    <span>5.0x (Highly Volatile)</span>
                  </div>
                </div>

                <div>
                  <Label className="mb-2 block text-xs uppercase tracking-wide text-muted-foreground">
                    Tick Broadcast Speed
                  </Label>
                  <ToggleGroup
                    type="single"
                    value={String(tickInterval)}
                    onValueChange={(v) => {
                      if (!v) return;
                      const val = parseFloat(v);
                      setTickInterval(val);
                      handleUpdateSimulation({ tickInterval: val });
                    }}
                    className="grid w-full grid-cols-4 gap-1"
                    spacing={0}
                  >
                    {[
                      { val: 1.0, label: '1s (Slow)' },
                      { val: 0.5, label: '500ms' },
                      { val: 0.25, label: '250ms' },
                      { val: 0.1, label: '100ms' },
                    ].map(speed => (
                      <ToggleGroupItem key={speed.val} value={String(speed.val)} className="h-8 text-[0.7rem] font-semibold">
                        {speed.label}
                      </ToggleGroupItem>
                    ))}
                  </ToggleGroup>
                </div>
              </div>
            </TabsContent>

            <TabsContent value="account" className="mt-0 flex flex-col gap-6">
              {isLive ? (
                <>
                  <Alert className="border-amber-500/30 bg-amber-500/10">
                    <ShieldAlert data-icon="inline-start" className="text-amber-500" />
                    <AlertDescription className="text-xs leading-relaxed text-amber-200/90">
                      Manual balance seeding and database resets are disabled in live trading mode. Balances and positions are synced from the broker account.
                    </AlertDescription>
                  </Alert>

                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button variant="destructive" className="h-auto w-full py-4 text-xs font-extrabold tracking-wide">
                        <ShieldAlert data-icon="inline-start" />
                        EMERGENCY STOP: LIQUIDATE ALL POSITIONS & CANCEL ORDERS
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Emergency liquidation?</AlertDialogTitle>
                        <AlertDialogDescription>
                          This will immediately cancel all open limit orders and close all active positions with market orders at the exchange.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction variant="destructive" onClick={handleEmergencyStop}>
                          Execute
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </>
              ) : (
                <>
                  <div>
                    <Label className="mb-2 block text-xs uppercase tracking-wide text-muted-foreground">
                      Credit Account Balance
                    </Label>
                    <div className="flex gap-2">
                      <Select value={seedAsset} onValueChange={setSeedAsset}>
                        <SelectTrigger className="w-[100px]">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectGroup>
                            {getAvailableAssets().map(asset => (
                              <SelectItem key={asset} value={asset}>{asset}</SelectItem>
                            ))}
                          </SelectGroup>
                        </SelectContent>
                      </Select>
                      <Input
                        type="number"
                        value={seedAmount}
                        onChange={e => setSeedAmount(e.target.value)}
                        placeholder="Amount to credit…"
                        className="num-mono flex-1"
                      />
                      <Button onClick={handleSeedBalance}>Credit Balance</Button>
                    </div>
                  </div>

                  <Separator />

                  <div>
                    <Label className="mb-2 block text-xs uppercase tracking-wide text-muted-foreground">
                      System Reset Actions
                    </Label>
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button variant="destructive" className="w-full" disabled={isResetting}>
                          {isResetting
                            ? <RefreshCw data-icon="inline-start" className="animate-spin" />
                            : <ShieldAlert data-icon="inline-start" />
                          }
                          {isResetting ? 'RESETTING SYSTEM…' : 'NUCLEAR RESET: WIPE SYSTEM DATABASE'}
                        </Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>Wipe system database?</AlertDialogTitle>
                          <AlertDialogDescription>
                            This will delete all database positions, orders, and trade histories. Default account balances will be re-seeded.
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Cancel</AlertDialogCancel>
                          <AlertDialogAction variant="destructive" onClick={handleNuclearReset}>
                            Wipe Everything
                          </AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                    <p className="mt-2 text-center text-[0.66rem] text-muted-foreground">
                      Warning: Wipes active positions, cancels pending orders, and clears trade blotter logs.
                    </p>
                  </div>
                </>
              )}
            </TabsContent>

            <TabsContent value="diagnostics" className="mt-0 flex flex-col gap-4">
              <div className="grid grid-cols-2 gap-3">
                <Card className="border-border/60 bg-muted/20 py-0">
                  <CardContent className="p-3.5">
                    <p className="mb-1 text-[0.68rem] font-semibold uppercase tracking-wide text-muted-foreground">
                      Active Client Sockets
                    </p>
                    <p className="num-mono text-2xl font-bold text-trading-accent">{systemStats.clients || 1}</p>
                  </CardContent>
                </Card>
                <Card className="border-border/60 bg-muted/20 py-0">
                  <CardContent className="p-3.5">
                    <p className="mb-1 text-[0.68rem] font-semibold uppercase tracking-wide text-muted-foreground">
                      Active Tick Rate
                    </p>
                    <p className="num-mono text-2xl font-bold text-trading-up">
                      {(1.0 / (systemStats.tick_interval || 0.25)).toFixed(1)}
                      <span className="ml-1 text-sm font-normal text-muted-foreground">ticks/sec</span>
                    </p>
                  </CardContent>
                </Card>
              </div>

              <Card className="border-border/60 bg-muted/20 py-0">
                <CardContent className="flex flex-col gap-2.5 p-4">
                  <p className="border-b border-border/60 pb-1.5 text-xs font-bold">Database Row Counts</p>
                  {[
                    ['Open Positions count', systemStats.positions_count || 0],
                    ['Pending Orders count', systemStats.pending_orders_count || 0],
                    ['Filled Trades count', systemStats.filled_trades_count || 0],
                  ].map(([label, count]) => (
                    <div key={label} className="flex justify-between text-sm">
                      <span className="text-muted-foreground">{label}:</span>
                      <span className="num-mono font-semibold">{count}</span>
                    </div>
                  ))}
                </CardContent>
              </Card>
            </TabsContent>
          </div>
        </Tabs>

        <DialogFooter className="border-t border-border bg-muted/20 px-5 py-3">
          <Button variant="outline" size="sm" onClick={onClose}>Close Panel</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
