import React, { useState, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { useIsOperator } from '../lib/operator';
import { sendAction, invokeHttpAction } from '../api/transport';
import { Action } from '../api/protocol';
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Slider } from '@/components/ui/slider';
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
import { FieldGroup } from '@/components/ui/field';
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
  InputGroupText,
} from '@/components/ui/input-group';
import {
  AdminSection,
  AdminFieldRow,
  AdminDangerZone,
  AdminLockedOverlay,
} from './AdminPanelShell';
import { StatCard } from './StatCard';
import {
  Activity,
  Briefcase,
  Cpu,
  Database,
  DollarSign,
  RefreshCw,
  Loader2,
  ShieldAlert,
  Sliders,
  Zap,
} from 'lucide-react';

const ADMIN_TABS = [
  { id: 'simulation', label: 'Market Simulation', icon: Sliders, title: 'Market simulation controls' },
  { id: 'account', label: 'Account Admin', icon: DollarSign, title: 'Account seeding and reset' },
  { id: 'diagnostics', label: 'Diagnostics', icon: Database, title: 'System diagnostics' },
];

const TICK_SPEEDS = [
  { val: 1.0, label: '1s', sub: 'Slow' },
  { val: 0.5, label: '500ms', sub: 'Medium' },
  { val: 0.25, label: '250ms', sub: 'Normal' },
  { val: 0.1, label: '100ms', sub: 'Fast' },
];

function extractOrderResult(envelope) {
  if (envelope?.data?.status) return envelope.data;
  const msgs = envelope?.messages;
  if (Array.isArray(msgs)) {
    const hit = msgs.find((m) => m.type === 'order_result');
    if (hit?.data) return hit.data;
  }
  return null;
}

export default function SystemControlPanel({ isOpen, onClose }) {
  const systemStats = useStore((state) => state.systemStats);
  const activeSymbol = useStore((state) => state.activeSymbol);
  const isLive = useStore((state) => state.isLive);
  const terminalMode = useStore((state) => state.terminalMode);
  const symbolsList = useStore((state) => state.symbolsList);
  const archiveParquetEnabled = useStore((state) => state.archiveParquetEnabled);
  const isOperator = useIsOperator();
  const [activeTab, setActiveTab] = useState('simulation');
  const parquetReason = !isOperator
    ? 'Operator build required'
    : !archiveParquetEnabled
      ? 'Set ARCHIVE_PARQUET_ENABLED'
      : null;

  const archiveIngestion = systemStats?.archive?.ingestion ?? {};
  const brokerSource = archiveIngestion.broker_source ?? 'none';
  const brokerAvailable = archiveIngestion.broker_available ?? brokerSource !== 'none';
  const ingestShortfall = archiveIngestion.symbols_shortfall ?? [];
  const ingestTargetDays = archiveIngestion.target_days ?? 90;
  const ingestShortfallLabel = ingestShortfall.length > 0
    ? ingestShortfall.slice(0, 8).join(', ') + (ingestShortfall.length > 8 ? '…' : '')
    : 'Full coverage';
  const brokerIngestDisabledReason = !brokerAvailable
    ? 'No broker API — set MASSIVE_API_KEY or Alpaca/Binance credentials'
    : null;

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
  const [archivePending, setArchivePending] = useState(null);

  const runArchiveAction = async (action, payload, { pendingKey, startLabel, timeoutMs }) => {
    if (archivePending) return;
    setArchivePending(pendingKey);
    const toastId = toast.loading(startLabel);
    try {
      const envelope = await invokeHttpAction(action, payload, { timeoutMs });
      const result = extractOrderResult(envelope);
      const status = result?.status ?? (envelope?.ok === false ? 'error' : 'success');
      const message = result?.message
        ?? (status === 'success' ? 'Archive operation complete' : 'Archive operation failed');
      if (status === 'error') {
        toast.error(message, { id: toastId });
      } else {
        toast.success(message, { id: toastId, duration: 6000 });
      }
      sendAction(Action.ADMIN_GET_STATS);
    } catch (err) {
      const msg = err?.message || 'Archive operation failed';
      if (/abort/i.test(msg)) {
        toast.error('Archive operation timed out — it may still be running on the server. Refresh diagnostics in a minute.', { id: toastId, duration: 8000 });
      } else {
        toast.error(msg, { id: toastId });
      }
    } finally {
      setArchivePending(null);
    }
  };

  const handleArchiveBackfill = () => runArchiveAction(
    Action.ADMIN_ARCHIVE_BACKFILL,
    { force: true },
    {
      pendingKey: 'backfill',
      startLabel: 'Importing seed parquet and feed buffer into archive…',
      timeoutMs: 120000,
    },
  );

  const handleArchiveIngest = () => runArchiveAction(
    Action.ADMIN_ARCHIVE_INGEST,
    { days: ingestTargetDays, include_seed: true },
    {
      pendingKey: 'ingest',
      startLabel: `Broker ingest started (${ingestTargetDays}d target) — this may take several minutes…`,
      timeoutMs: 600000,
    },
  );

  const handleArchiveExport = () => runArchiveAction(
    Action.ADMIN_ARCHIVE_EXPORT,
    { days: 90, interval: 'auto' },
    {
      pendingKey: 'export',
      startLabel: 'Exporting archive to Parquet…',
      timeoutMs: 180000,
    },
  );

  const tickRate = (1.0 / (systemStats.tick_interval || tickInterval || 0.25)).toFixed(1);

  useEffect(() => {
    if (systemStats) {
      if (systemStats.volatility_multiplier !== undefined) setVolatility(systemStats.volatility_multiplier);
      if (systemStats.tick_interval !== undefined) setTickInterval(systemStats.tick_interval);
    }
  }, [systemStats]);

  useEffect(() => {
    if (isOpen) sendAction(Action.ADMIN_GET_STATS);
  }, [isOpen, activeTab]);

  const resetTimerRef = useRef(null);
  useEffect(() => () => {
    if (resetTimerRef.current) clearTimeout(resetTimerRef.current);
  }, []);

  const handleUpdateSimulation = (updates = {}) => {
    if (isLive) return;
    sendAction(Action.ADMIN_SET_SIMULATION, {
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
    sendAction(Action.ADMIN_SEED_BALANCE, { asset: seedAsset, amount });
    toast.success(`Credited ${amount} ${seedAsset}`);
  };

  const handleNuclearReset = () => {
    setIsResetting(true);
    sendAction(Action.ADMIN_RESET_SYSTEM);
    toast.info('System reset initiated…');
    if (resetTimerRef.current) clearTimeout(resetTimerRef.current);
    resetTimerRef.current = setTimeout(() => {
      resetTimerRef.current = null;
      setIsResetting(false);
      onClose();
    }, 1500);
  };

  const handleEmergencyStop = () => {
    sendAction(Action.ADMIN_EMERGENCY_STOP);
    toast.warning('Emergency liquidation executed');
    onClose();
  };

  const handleRefreshStats = () => {
    sendAction(Action.ADMIN_GET_STATS);
    toast.success('Diagnostics refreshed');
  };

  const safeMode = systemStats.runtime?.safe_mode;
  const safeModeActive = Boolean(safeMode?.active);

  const handleConfirmSafeMode = () => {
    sendAction(Action.ADMIN_CONFIRM_SAFE_MODE, {});
    toast.success('Safe mode cleared — resume bots when ready');
    handleRefreshStats();
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        className="admin-panel gap-0 overflow-hidden p-0 sm:max-w-[580px]"
        overlayClassName="admin-panel-overlay"
        closeButtonClassName="admin-panel-close"
        showCloseButton
        aria-busy={isResetting}
        aria-describedby="admin-panel-desc"
      >
        <DialogHeader className="admin-panel-header">
          <div className="admin-panel-header__top">
            <div className="admin-panel-header__lead">
              <div className="admin-panel-header__icon" aria-hidden>
                <Cpu size={16} />
              </div>
              <div className="admin-panel-header__copy">
                <DialogTitle className="admin-panel-header__title">System Admin</DialogTitle>
                <p className="admin-panel-header__subtitle">
                  Market simulation · account · diagnostics
                </p>
              </div>
            </div>
            <div className="admin-panel-header__meta">
              {isLive ? (
                <Badge
                  variant="live"
                  className="header-mode-badge header-mode-badge--live icon-label shrink-0 px-2 py-0.5 text-[0.62rem] font-extrabold tracking-wider"
                  aria-label={`Live trading mode: ${terminalMode}`}
                >
                  <span className="size-1.5 rounded-full bg-current" aria-hidden />
                  LIVE · {terminalMode}
                </Badge>
              ) : (
                <Badge
                  variant="secondary"
                  className="header-mode-badge shrink-0 px-2 py-0.5 text-[0.62rem] font-bold tracking-wide"
                  aria-label="Simulated trading mode"
                >
                  SIMULATED
                </Badge>
              )}
              <kbd className="admin-panel-kbd admin-panel-kbd--esc">esc</kbd>
            </div>
          </div>
          <DialogDescription id="admin-panel-desc" className="sr-only">
            Configure market simulation, account balances, and view system diagnostics.
            Press Escape to close.
          </DialogDescription>
        </DialogHeader>

        <Tabs value={activeTab} onValueChange={setActiveTab} className="gap-0">
          <div className="admin-panel-tabs-wrap">
            <span className="admin-panel-tabs-label">Sections</span>
            <div className="admin-panel-tabs-scroll scroll-panel-x no-scrollbar">
            <TabsList variant="line" className="admin-panel-tabs" aria-label="Admin panel sections">
              {ADMIN_TABS.map(tab => {
                const Icon = tab.icon;
                return (
                  <TabsTrigger
                    key={tab.id}
                    value={tab.id}
                    className="flex-none shadow-none"
                    title={tab.title}
                    aria-label={tab.label}
                  >
                    <Icon className="admin-tab-icon" strokeWidth={2} aria-hidden />
                    <span className="admin-tab-label">{tab.label}</span>
                  </TabsTrigger>
                );
              })}
            </TabsList>
            </div>
          </div>

          <div className="admin-panel-body scroll-panel-y scroll-panel-y-0 px-4 py-4">
            <TabsContent value="simulation" className="mt-0">
              {isLive && (
                <Alert variant="destructive" className="mb-4">
                  <ShieldAlert data-icon="inline-start" />
                  <AlertDescription className="text-xs leading-relaxed">
                    Simulation drift controls are locked in live trading. Volatility and tick rates are governed entirely by real-time exchange feeds.
                  </AlertDescription>
                </Alert>
              )}

              <AdminLockedOverlay
                locked={isLive}
                message="Simulation controls locked — live broker feed active"
              >
                <FieldGroup className="gap-4">
                  <AdminSection
                    title="Price Drift"
                    description={`Bias override for ${activeSymbol} in the simulated feed.`}
                  >
                    <ToggleGroup
                      type="single"
                      value={bias}
                      onValueChange={(v) => { if (v) { setBias(v); handleUpdateSimulation({ bias: v }); } }}
                      className="admin-toggle-grid admin-toggle-grid-3"
                      spacing={0}
                      aria-label="Price drift bias"
                    >
                      <ToggleGroupItem value="UP" variant="buy" className="h-8 text-xs font-bold">
                        Bullish
                      </ToggleGroupItem>
                      <ToggleGroupItem value="DOWN" variant="sell" className="h-8 text-xs font-bold">
                        Bearish
                      </ToggleGroupItem>
                      <ToggleGroupItem value="RANDOM" variant="outline" className="h-8 text-xs font-bold">
                        Random
                      </ToggleGroupItem>
                    </ToggleGroup>
                  </AdminSection>

                  <AdminSection
                    title="Volatility Multiplier"
                    description="Scales random price movement amplitude across all symbols."
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="admin-section-desc">Current multiplier</span>
                      <span className="admin-slider-value">{volatility.toFixed(1)}×</span>
                    </div>
                    <Slider
                      className="admin-slider"
                      min={0.2}
                      max={5}
                      step={0.2}
                      value={[volatility]}
                      disabled={isLive}
                      aria-label="Volatility multiplier"
                      aria-valuetext={`${volatility.toFixed(1)} times normal volatility`}
                      onValueChange={([val]) => {
                        setVolatility(val);
                        handleUpdateSimulation({ volatility: val });
                      }}
                    />
                    <div className="admin-slider-labels">
                      <span>0.2× Stable</span>
                      <span>1.0× Normal</span>
                      <span>5.0× Volatile</span>
                    </div>
                  </AdminSection>

                  <AdminSection
                    title="Tick Broadcast Speed"
                    description="How often market_update payloads are pushed to clients."
                  >
                    <ToggleGroup
                      type="single"
                      value={String(tickInterval)}
                      onValueChange={(v) => {
                        if (!v) return;
                        const val = parseFloat(v);
                        setTickInterval(val);
                        handleUpdateSimulation({ tickInterval: val });
                      }}
                      className="admin-toggle-grid admin-toggle-grid-speed"
                      spacing={0}
                      aria-label="Tick broadcast interval"
                    >
                      {TICK_SPEEDS.map(speed => (
                        <ToggleGroupItem
                          key={speed.val}
                          value={String(speed.val)}
                          className="flex h-9 flex-col gap-0 py-1 text-[0.68rem] font-semibold leading-tight"
                        >
                          <span className="num-mono">{speed.label}</span>
                          <span className="text-[0.58rem] font-normal text-muted-foreground">{speed.sub}</span>
                        </ToggleGroupItem>
                      ))}
                    </ToggleGroup>
                  </AdminSection>
                </FieldGroup>
              </AdminLockedOverlay>
            </TabsContent>

            <TabsContent value="account" className="mt-0 flex flex-col gap-5">
              {isLive ? (
                <>
                  <Alert className="border-trading-warn/30 bg-trading-warn/10">
                    <ShieldAlert data-icon="inline-start" className="text-trading-warn" />
                    <AlertDescription className="text-xs leading-relaxed text-foreground/90">
                      Manual balance seeding and database resets are disabled in live trading mode. Balances and positions are synced from the broker account.
                    </AlertDescription>
                  </Alert>

                  <AdminDangerZone
                    title="Emergency Actions"
                    description="Immediately cancel all open orders and close every position at market."
                  >
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button variant="outline" className="admin-danger-btn h-auto py-3">
                          <ShieldAlert data-icon="inline-start" />
                          Emergency Stop — Liquidate All
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
                  </AdminDangerZone>
                </>
              ) : (
                <>
                  <AdminSection
                    title="Credit Account Balance"
                    description="Add funds to a simulated account asset for paper trading."
                  >
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        handleSeedBalance();
                      }}
                    >
                      <AdminFieldRow hint="Credits apply immediately to the selected asset balance.">
                        <div className="admin-credit-row">
                          <Select value={seedAsset} onValueChange={setSeedAsset}>
                            <SelectTrigger aria-label="Asset to credit">
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
                          <InputGroup className="min-w-[120px] flex-1">
                            <InputGroupInput
                              id="admin-seed-amount"
                              type="number"
                              min="0"
                              step="any"
                              value={seedAmount}
                              onChange={e => setSeedAmount(e.target.value)}
                              placeholder="Amount"
                              className="num-mono"
                              aria-label="Credit amount"
                            />
                            <InputGroupAddon align="inline-end">
                              <InputGroupText className="text-xs">{seedAsset}</InputGroupText>
                            </InputGroupAddon>
                          </InputGroup>
                          <Button type="submit">Credit</Button>
                        </div>
                      </AdminFieldRow>
                    </form>
                  </AdminSection>

                  <AdminDangerZone
                    title="Destructive Actions"
                    description="These operations cannot be undone. All positions, orders, and trade history will be wiped."
                  >
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button variant="outline" className="admin-danger-btn" disabled={isResetting}>
                          {isResetting
                            ? <RefreshCw data-icon="inline-start" className="animate-spin" />
                            : <ShieldAlert data-icon="inline-start" />
                          }
                          {isResetting ? 'Resetting System…' : 'Nuclear Reset — Wipe Database'}
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
                  </AdminDangerZone>
                </>
              )}
            </TabsContent>

            <TabsContent value="diagnostics" className="mt-0 flex flex-col gap-4">
              {safeModeActive && (
                <Alert variant="destructive" className="border-destructive/40 bg-destructive/10">
                  <ShieldAlert className="size-4" aria-hidden />
                  <AlertDescription className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <span>
                      <strong>Safe mode active</strong>
                      {' — '}
                      {safeMode.reason || 'Unclean shutdown or unresolved fills detected.'}
                      {' '}
                      All bots are paused until you confirm system state.
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      className="shrink-0 border-destructive/50"
                      onClick={handleConfirmSafeMode}
                    >
                      Confirm &amp; clear safe mode
                    </Button>
                  </AlertDescription>
                </Alert>
              )}

              <AdminSection
                title="Runtime Metrics"
                description="Live server stats from the WebSocket backend."
                action={
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={handleRefreshStats}
                    aria-label="Refresh diagnostics statistics"
                  >
                    <RefreshCw data-icon="inline-start" aria-hidden />
                    Refresh
                  </Button>
                }
              >
                <div className="flex flex-wrap gap-2">
                  <StatCard
                    label="Client Sockets"
                    icon={Activity}
                    value={systemStats.clients ?? 1}
                    tone="accent"
                    sub="Connected WebSocket clients"
                  />
                  <StatCard
                    label="Tick Rate"
                    icon={Zap}
                    value={`${tickRate}/s`}
                    tone="up"
                    sub={`${(systemStats.tick_interval || tickInterval || 0.25) * 1000}ms interval`}
                  />
                  <StatCard
                    label="Open Positions"
                    icon={Briefcase}
                    value={systemStats.positions_count ?? 0}
                    tone="neutral"
                    sub="Active in database"
                  />
                </div>
              </AdminSection>

              {systemStats.portfolio && (
                <AdminSection
                  title="Portfolio Risk"
                  description="Cross-bot gross and correlation-group exposure caps."
                >
                  <div className="flex flex-wrap gap-2">
                    <StatCard
                      label="Gross Exposure"
                      icon={Briefcase}
                      value={`${systemStats.portfolio.gross_exposure_pct ?? 0}%`}
                      tone={
                        (systemStats.portfolio.gross_exposure_pct ?? 0) >=
                        (systemStats.portfolio.max_gross_pct ?? 80) * 0.9
                          ? 'down'
                          : 'neutral'
                      }
                      sub={`$${(systemStats.portfolio.gross_exposure ?? 0).toLocaleString()} / max ${systemStats.portfolio.max_gross_pct ?? 80}%`}
                    />
                    <StatCard
                      label="Account Equity"
                      icon={Activity}
                      value={`$${(systemStats.portfolio.equity ?? 0).toLocaleString()}`}
                      tone="accent"
                      sub="Basis for exposure limits"
                    />
                    {systemStats.portfolio.margin?.enabled !== false && (
                      <StatCard
                        label="Margin Used"
                        icon={Zap}
                        value={`${systemStats.portfolio.margin_utilization_pct ?? systemStats.portfolio.margin?.utilization_pct ?? 0}%`}
                        tone={
                          (systemStats.portfolio.margin_utilization_pct ?? 0) >=
                          (systemStats.portfolio.margin?.max_utilization_pct ?? 85) * 0.9
                            ? 'down'
                            : 'neutral'
                        }
                        sub={`$${(systemStats.portfolio.margin?.margin_used ?? 0).toLocaleString()} / ${systemStats.portfolio.margin?.max_utilization_pct ?? 85}% cap`}
                      />
                    )}
                    <StatCard
                      label="Group Cap"
                      icon={Zap}
                      value={`${systemStats.portfolio.max_group_pct ?? 40}%`}
                      tone="neutral"
                      sub="Per correlation bucket"
                    />
                  </div>
                  {Object.keys(systemStats.portfolio.group_exposure ?? {}).length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5 text-[11px] text-muted-foreground">
                      {Object.entries(systemStats.portfolio.group_exposure).map(([group, notional]) => {
                        const pct = systemStats.portfolio.equity
                          ? ((notional / systemStats.portfolio.equity) * 100).toFixed(1)
                          : '0.0';
                        return (
                          <span
                            key={group}
                            className="rounded border border-border/60 bg-muted/30 px-2 py-0.5 font-mono"
                          >
                            {group}: ${Number(notional).toLocaleString()} ({pct}%)
                          </span>
                        );
                      })}
                    </div>
                  )}
                </AdminSection>
              )}

              <AdminSection title="Market Archive (DB)" description="SQLite or Postgres tables — market_bars_1m / market_bars_1h.">
                <div className="flex flex-wrap gap-2 mb-2">
                  <StatCard
                    label="1m Bars"
                    icon={Activity}
                    value={(systemStats.archive?.bars_1m ?? 0).toLocaleString()}
                    tone="neutral"
                    sub="Full-minute OHLCV"
                  />
                  <StatCard
                    label="1h Bars"
                    icon={Activity}
                    value={(systemStats.archive?.bars_1h ?? 0).toLocaleString()}
                    tone="neutral"
                    sub="Rolled-up archive"
                  />
                  <StatCard
                    label="Broker source"
                    icon={Database}
                    value={brokerSource}
                    tone={brokerAvailable ? 'up' : 'down'}
                    sub={brokerAvailable ? `${ingestTargetDays}d ingest target` : 'API not configured'}
                  />
                  <StatCard
                    label="History shortfall"
                    icon={Activity}
                    value={ingestShortfall.length}
                    tone={ingestShortfall.length > 0 ? 'down' : 'up'}
                    sub={ingestShortfallLabel}
                  />
                  <StatCard
                    label="Est. Size"
                    icon={Database}
                    value={`${systemStats.archive?.est_size_mb ?? 0} MB`}
                    tone="accent"
                    sub="SQLite / Postgres"
                  />
                </div>
                {ingestShortfall.length > 0 && (
                  <Alert variant="default" className="mb-2 py-2">
                    <AlertDescription className="text-xs">
                      {ingestShortfall.length} symbol(s) have less than {ingestTargetDays} days of 1m archive.
                      {brokerAvailable
                        ? ' Run broker ingest or wait for the hourly ingestion cycle.'
                        : ' Configure MASSIVE_API_KEY or broker credentials to backfill.'}
                    </AlertDescription>
                  </Alert>
                )}
                <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  className="text-xs"
                  disabled={Boolean(archivePending)}
                  onClick={handleArchiveBackfill}
                >
                  {archivePending === 'backfill'
                    ? <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />
                    : <RefreshCw data-icon="inline-start" aria-hidden />}
                  Backfill from seed data
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="text-xs"
                  disabled={Boolean(archivePending) || Boolean(brokerIngestDisabledReason)}
                  title={brokerIngestDisabledReason ?? `Fetch up to ${ingestTargetDays}d of 1m bars from broker API and repair gaps`}
                  onClick={handleArchiveIngest}
                >
                  {archivePending === 'ingest'
                    ? <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />
                    : <Database data-icon="inline-start" aria-hidden />}
                  Broker ingest ({ingestTargetDays}d)
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="text-xs"
                  disabled={Boolean(archivePending) || Boolean(parquetReason)}
                  title={parquetReason ?? 'Export 90 days of bars to Parquet'}
                  onClick={handleArchiveExport}
                >
                  {archivePending === 'export'
                    ? <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />
                    : <Database data-icon="inline-start" aria-hidden />}
                  Export Parquet (90d)
                  {parquetReason && <span className="ml-1 text-muted-foreground">· {parquetReason}</span>}
                </Button>
                </div>
                {(systemStats.archive?.ticks ?? 0) > 0 && (
                  <StatCard
                    label="Ticks (24h)"
                    icon={Activity}
                    value={(systemStats.archive?.ticks ?? 0).toLocaleString()}
                    tone="neutral"
                    sub="Sub-minute snapshots"
                  />
                )}
              </AdminSection>

              {systemStats.data_quality && (
                <AdminSection
                  title="Data Quality"
                  description="Feed freshness, spread anomalies, and candle gaps. Severe stale feeds auto-pause bots and resume when the feed recovers."
                >
                  <div className="flex flex-wrap gap-2 mb-2">
                    <StatCard
                      label="Status"
                      icon={Activity}
                      value={systemStats.data_quality.healthy ? 'Healthy' : 'Issues'}
                      tone={systemStats.data_quality.healthy ? 'up' : 'down'}
                      sub={`${systemStats.data_quality.issue_count ?? 0} active issue(s)`}
                    />
                    <StatCard
                      label="Stale (warn)"
                      icon={Zap}
                      value={(systemStats.data_quality.stale_warn ?? []).length}
                      tone="neutral"
                      sub={(systemStats.data_quality.stale_warn ?? []).join(', ') || '—'}
                    />
                    <StatCard
                      label="Stale (critical)"
                      icon={Zap}
                      value={(systemStats.data_quality.stale_severe ?? []).length}
                      tone={
                        (systemStats.data_quality.stale_severe ?? []).length > 0 ? 'down' : 'neutral'
                      }
                      sub={(systemStats.data_quality.stale_severe ?? []).join(', ') || '—'}
                    />
                    {(systemStats.data_quality.spread_alerts ?? []).length > 0 && (
                      <StatCard
                        label="Spread alerts"
                        icon={Activity}
                        value={systemStats.data_quality.spread_alerts.length}
                        tone="down"
                        sub={systemStats.data_quality.spread_alerts.join(', ')}
                      />
                    )}
                    {(systemStats.data_quality.gap_symbols ?? []).length > 0 && (
                      <StatCard
                        label="Candle gaps"
                        icon={Database}
                        value={systemStats.data_quality.gap_symbols.length}
                        tone="neutral"
                        sub={systemStats.data_quality.gap_symbols.join(', ')}
                      />
                    )}
                  </div>
                </AdminSection>
              )}

              {systemStats.altdata && (
                <AdminSection title="Alternative Data" description="Corporate actions and market calendar (Massive/Polygon REST).">
                  <div className="flex flex-wrap gap-2">
                    <StatCard
                      label="Corporate events"
                      icon={Database}
                      value={(systemStats.altdata.corporate_events ?? 0).toLocaleString()}
                      tone="neutral"
                      sub="Dividends & splits"
                    />
                    <StatCard
                      label="Economic events"
                      icon={Database}
                      value={(systemStats.altdata.economic_events ?? 0).toLocaleString()}
                      tone="neutral"
                      sub="Market holidays & macro"
                    />
                  </div>
                </AdminSection>
              )}

              <AdminSection title="Database Row Counts" description="Persistent store record totals.">
                <Card className="admin-panel-card py-0 shadow-none">
                  <CardContent className="p-0">
                    <table className="terminal-table text-xs" aria-label="Database row counts">
                      <caption className="sr-only">Counts of positions, orders, and trades in the database</caption>
                      <tbody>
                        {[
                          ['Open Positions', systemStats.positions_count ?? 0],
                          ['Pending Orders', systemStats.pending_orders_count ?? 0],
                          ['Filled Trades', systemStats.filled_trades_count ?? 0],
                        ].map(([label, count]) => (
                          <tr key={label}>
                            <td className="text-muted-foreground">{label}</td>
                            <td className="num-mono text-right font-semibold">{count}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </CardContent>
                </Card>
              </AdminSection>
            </TabsContent>
          </div>
        </Tabs>

        <DialogFooter className="admin-panel-footer sm:flex-row sm:items-center sm:justify-between">
          <p className="admin-panel-footer__hint">
            {isLive ? `Live broker · ${terminalMode}` : 'Simulated environment'}
            {' · '}
            <kbd className="admin-panel-kbd">Esc</kbd> to close
          </p>
          <Button variant="outline" size="sm" onClick={onClose} disabled={isResetting}>
            Close Panel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
