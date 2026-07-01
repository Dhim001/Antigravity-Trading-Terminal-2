/**
 * AlgoPanel.jsx — Algo Bot dock tab (extracted from ResizableDock).
 */
import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { toast } from 'sonner';
import { useStore } from '../../store/useStore';
import { sendAction } from '../../api/transport';
import { Action } from '../../api/protocol';
import { fetchBots, withLlmModel } from '../../api/endpoints';
import { getStoreActions } from '../../api/dispatch';
import { selectCashTotal } from '../../store/selectors';
import { useShallow } from 'zustand/react/shallow';
import {
  Cpu, Play, Settings, Trash2, XSquare, ShieldAlert, Pause, PlayCircle, OctagonX,
  RefreshCw, AlertTriangle, Activity, Loader2, Maximize2,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import {
  InputGroup, InputGroupAddon, InputGroupInput, InputGroupText,
} from '@/components/ui/input-group';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Alert, AlertDescription } from '@/components/ui/alert';
import StrategyTemplateCard from '../StrategyTemplateCard';
import StrategyBadge from '../StrategyBadge';
import BacktestResultsPanel from '../BacktestResultsPanel';
import BacktestProgressBar from '../BacktestProgressBar';
import ChartAgentDeployPreview from '../ChartAgentDeployPreview';
import { useVirtualRows } from '../VirtualTableBody';
import { ScrollTablePanel, WidgetEmpty } from '../WidgetShell';
import {
  DataTableRoot,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableHead,
  DataTableCell,
} from '../DataTableShell';
import {
  scheduleBacktestClientTimeout,
  clearBacktestClientTimeout,
  formatBacktestTimeoutLabel,
} from '../../lib/backtestTimeouts';
import { cn } from '@/lib/utils';
import { openBacktestLabResults } from '../../lib/backtestLab';
import { formatLastSignal } from '@/lib/formatTime';
import { BAR_TIMEFRAMES, deployTimeframeSummary, formatBarTimeframeLabel } from '@/lib/barTimeframes';
import { isLiveMassiveMode, isPaperExecutionMode } from '@/lib/massiveMarket';
import { backtestFingerprint } from '@/lib/backtestDisplay';
import { selectAgentInsight } from '@/lib/agentInsights';
import { isSignalLog, logLineClass } from '@/lib/botLogInsight';

// ── Algo Bot Tab ──────────────────────────────────────────────────
export function AlgoTab({ hideToolbar = false }) {
  const {
    activeBots, botStrategy, botExecutionMode, botTimeframe, botConfig, activeSymbol, symbolsList,
    setBotStrategy, setBotExecutionMode, setBotTimeframe, updateBotConfig, clearBotLogs, botLogs,
    strategyTemplates, backtestResults, backtestRuns, backtestRunning, backtestSnapshot,
    setBacktestRunning, setBacktestProgress, setBacktestSnapshot,
    openBacktestLab, setStoreBacktestDays, setStoreBacktestOos,
    setChartInteractionMode,
    isLive, allowLiveBots, allowCustomStrategies, terminalMode, terminalRole, distributed, botMinCandles,
    executionMode,
    setActiveSymbol,
    selectedBotId, setSelectedBotId, setBotDetail, setBotDrawerOpen,
    ambiguousOrders,
  } = useStore(useShallow((s) => ({
    activeBots: s.activeBots,
    botStrategy: s.botStrategy,
    botExecutionMode: s.botExecutionMode,
    botTimeframe: s.botTimeframe,
    botConfig: s.botConfig,
    activeSymbol: s.activeSymbol,
    symbolsList: s.symbolsList,
    setBotStrategy: s.setBotStrategy,
    setBotExecutionMode: s.setBotExecutionMode,
    setBotTimeframe: s.setBotTimeframe,
    updateBotConfig: s.updateBotConfig,
    clearBotLogs: s.clearBotLogs,
    botLogs: s.botLogs,
    strategyTemplates: s.strategyTemplates,
    backtestResults: s.backtestResults,
    backtestRuns: s.backtestRuns,
    backtestRunning: s.backtestRunning,
    backtestSnapshot: s.backtestSnapshot,
    setBacktestRunning: s.setBacktestRunning,
    setBacktestProgress: s.setBacktestProgress,
    setBacktestSnapshot: s.setBacktestSnapshot,
    openBacktestLab: s.openBacktestLab,
    setStoreBacktestDays: s.setBacktestDays,
    setStoreBacktestOos: s.setBacktestOos,
    setChartInteractionMode: s.setChartInteractionMode,
    isLive: s.isLive,
    allowLiveBots: s.allowLiveBots,
    allowCustomStrategies: s.allowCustomStrategies,
    terminalMode: s.terminalMode,
    terminalRole: s.terminalRole,
    distributed: s.distributed,
    botMinCandles: s.botMinCandles,
    executionMode: s.executionMode,
    setActiveSymbol: s.setActiveSymbol,
    selectedBotId: s.selectedBotId,
    setSelectedBotId: s.setSelectedBotId,
    setBotDetail: s.setBotDetail,
    setBotDrawerOpen: s.setBotDrawerOpen,
    ambiguousOrders: s.ambiguousOrders,
  })));
  const positions = useStore((state) => state.positions);
  const agentInsights = useStore((state) => state.agentInsights);
  const tickerPrice = useStore((state) => state.tickerData[state.activeSymbol]?.price);
  const cashTotal = useStore(selectCashTotal);

  const liveBotsBlocked = isLive && !allowLiveBots;
  const paperExecution = isPaperExecutionMode(terminalMode, executionMode);
  const massiveLive = isLiveMassiveMode(terminalMode);
  const runningCount = activeBots.filter(b => b.status === 'RUNNING').length;
  const [deployOpen, setDeployOpen] = useState(false);
  const [stopAllOpen, setStopAllOpen] = useState(false);
  const [backtestDays, setBacktestDaysLocal] = useState('7');
  const [backtestOos, setBacktestOosLocal] = useState(false);
  const [backtestReasoning, setBacktestReasoning] = useState(false);
  const [backtestSimMode, setBacktestSimMode] = useState('live_aligned');
  const [backtestRiskBaseMode, setBacktestRiskBaseMode] = useState('account_snapshot');
  const [portfolioBacktest, setPortfolioBacktest] = useState(false);
  const [logFilter, setLogFilter] = useState('all');
  const agentLlmAvailable = useStore((s) => s.agentLlmAvailable);
  const agentLlmEnabled = useStore((s) => s.agentLlmEnabled);
  const logScrollRef = useRef(null);
  const logCountRef = useRef(0);
  const filteredBotLogs = useMemo(() => {
    if (logFilter === 'agent_skips') {
      return botLogs.filter((l) => {
        const text = l.message ?? l.line ?? '';
        return /CHART_AGENT skipped|reject_reason|filter reject/i.test(text)
          || l.meta?.reject_reason;
      });
    }
    if (logFilter === 'signals') {
      return botLogs.filter((l) => isSignalLog(l));
    }
    return botLogs;
  }, [botLogs, logFilter]);
  const { onScroll: onLogScroll, window: logWindow } = useVirtualRows(filteredBotLogs, {
    rowHeight: 22,
    overscan: 14,
  });

  useEffect(() => {
    if (botLogs.length > logCountRef.current && logScrollRef.current) {
      logScrollRef.current.scrollTop = 0;
    }
    logCountRef.current = botLogs.length;
  }, [botLogs]);

  useEffect(() => {
    fetchBots(getStoreActions()).catch(() => {});
  }, []);

  useEffect(() => () => {
    clearBacktestClientTimeout();
  }, []);

  const setBacktestDays = (days) => {
    setBacktestDaysLocal(days);
    setStoreBacktestDays(days);
  };
  const setBacktestOos = (oos) => {
    setBacktestOosLocal(oos);
    setStoreBacktestOos(oos);
  };

  const handleOpenOptimizer = () => {
    setStoreBacktestDays(backtestDays);
    setStoreBacktestOos(backtestOos);
    openBacktestLab('optimizer');
  };

  const handleRunBacktest = async () => {
    if (!botConfig?.allocation || botConfig.allocation <= 0) {
      toast.error('Set a valid max notional cap before backtesting');
      return;
    }

    const days = parseInt(backtestDays, 10) || 7;
    const isTick = botExecutionMode === 'TICK';
    const snapshot = backtestFingerprint({
      symbol: activeSymbol,
      strategy: botStrategy,
      days: String(days),
      timeframe: isTick ? 'tick' : botTimeframe,
      config: botConfig,
    });

    setBacktestRunning(true);
    setBacktestProgress({ pct: 0, phase: 'resolve', message: 'Starting backtest…' });
    setBacktestSnapshot(snapshot);

    scheduleBacktestClientTimeout({
      reasoning: backtestReasoning,
      days,
      onTimeout: (timeoutMs) => {
        if (useStore.getState().backtestRunning) {
          setBacktestRunning(false);
          setBacktestProgress(null);
          toast.error(
            backtestReasoning
              ? `Backtest timed out after ${formatBacktestTimeoutLabel(timeoutMs)} — increase VITE_BACKTEST_REASONING_TIMEOUT_MS, reduce days, or lower BACKTEST_REASONING_MAX_TRADES`
              : `Backtest timed out after ${formatBacktestTimeoutLabel(timeoutMs)} — try a shorter range or increase VITE_BACKTEST_TIMEOUT_MS`,
          );
        }
      },
    });

    const portfolioSymbols = portfolioBacktest
      ? (symbolsList || []).slice(0, 5).filter(Boolean)
      : undefined;

    const { ok, error } = await sendAction(Action.RUN_BACKTEST, withLlmModel({
      strategy: botStrategy,
      symbol: activeSymbol,
      config: {
        ...botConfig,
        sim_mode: backtestSimMode,
        risk_base_mode: backtestRiskBaseMode,
        ...(cashTotal > 0 ? { risk_base: cashTotal } : {}),
        ...(selectedBotId ? { backtest_bot_id: selectedBotId } : {}),
      },
      days,
      timeframe: isTick ? 'tick' : botTimeframe,
      oos_pct: backtestOos ? 30 : undefined,
      reasoning: backtestReasoning || undefined,
      portfolio_symbols: portfolioSymbols?.length > 1 ? portfolioSymbols : undefined,
    }));

    if (!ok) {
      clearBacktestClientTimeout();
      setBacktestRunning(false);
      setBacktestProgress(null);
      if (error) toast.error(error);
    }
  };

  const handleCancelBacktest = () => {
    const jobId = useStore.getState().backtestJobId;
    sendAction(Action.CANCEL_BACKTEST, jobId ? { job_id: jobId } : {});
  };

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
      toast.error('Enter a valid max notional cap');
      return;
    }

    sendAction(Action.BOT_CREATE, {
      strategy: botStrategy,
      symbol: activeSymbol,
      timeframe: botExecutionMode === 'TICK' ? 'tick' : botTimeframe,
      allocation: botConfig.allocation,
      execution_mode: botExecutionMode,
      config: {
        ...botConfig,
        trailing_stop_percent: botConfig.trailing_stop_percent ?? 2,
        backtest_run_id: useStore.getState().backtestResults?.run_id ?? undefined,
      },
    });
  };

  const filteredTemplates = strategyTemplates.filter(
    (t) => (t.execution_mode || 'BAR_CLOSE') === botExecutionMode
      && (allowCustomStrategies || (t.strategy !== 'CUSTOM' && !t.custom)),
  );

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

  const handleSetBotStopLoss = useCallback((bot) => {
    if (bot.symbol && bot.symbol !== activeSymbol) {
      setActiveSymbol(bot.symbol);
    }
    setChartInteractionMode('edit_sl');
  }, [activeSymbol, setActiveSymbol, setChartInteractionMode]);

  const handleSetBotTakeProfit = useCallback((bot) => {
    if (bot.symbol && bot.symbol !== activeSymbol) {
      setActiveSymbol(bot.symbol);
    }
    setChartInteractionMode('edit_tp');
  }, [activeSymbol, setActiveSymbol, setChartInteractionMode]);

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

  const logLineClassLocal = (log) => logLineClass(log);

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

  return (
    <div className={cn('algo-tab', hideToolbar && 'algo-tab--embedded')}>
      {!hideToolbar ? (
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
              <Badge variant="live" className="header-mode-badge header-mode-badge--live px-2 py-0.5 text-xs font-extrabold tracking-wider">
                LIVE
              </Badge>
            ) : (
              <Badge variant="secondary" className="header-mode-badge px-2 py-0.5 text-xs font-bold">
                SIM
              </Badge>
            )}
            {liveBotsBlocked && (
              <Badge variant="outline" className="algo-tab__toolbar-warn px-2 py-0.5 text-xs">
                Exec locked
              </Badge>
            )}
          </div>
        </header>
      ) : null}

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
            <strong>{massiveLive ? 'Paper execution on Massive data' : 'Live bots enabled'}</strong>
            {massiveLive
              ? ' — instant fills at live prices (no broker routing). 1m BAR_CLOSE via feed bar hooks; higher timeframes via native REST; TICK bots on price updates.'
              : ` on ${terminalMode}`}
            {distributed ? ` · role=${terminalRole} (distributed via Redis)` : ''}.
            {!massiveLive && (
              <>
                {' '}Indicator warm-up uses archive when buffer &lt; {botMinCandles} bars.
                Signals fire on closed {formatBarTimeframeLabel(botTimeframe)} bars — do not resend ambiguous orders.
              </>
            )}
            {massiveLive && (
              <>
                {' '}Indicator warm-up uses Massive REST when the chart buffer is shallow.
              </>
            )}
          </AlertDescription>
        </Alert>
      )}

      {isLive && !paperExecution && ambiguousOrders.length > 0 && (
        <Alert className="algo-tab__banner border-trading-warn/40 bg-trading-warn/5 xl:col-span-3">
          <AlertTriangle className="text-trading-warn" aria-hidden />
          <AlertDescription className="flex flex-wrap items-center gap-2 text-xs leading-relaxed">
            <span>
              <strong>{ambiguousOrders.length} ambiguous order{ambiguousOrders.length === 1 ? '' : 's'}</strong>
              {' '}need review — confirm filled or dismiss in Reconcile (do not resend).
            </span>
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              onClick={() => window.dispatchEvent(new CustomEvent('dock-tab', { detail: 'reconcile' }))}
            >
              Review in Reconcile
            </Button>
          </AlertDescription>
        </Alert>
      )}

      <section className="algo-tab__panel algo-tab__panel--deploy">
        <header className="algo-tab__panel-header">
          <div className="algo-tab__panel-heading">
            <div className="algo-tab__panel-title">
              <Settings size={13} className="text-primary" aria-hidden />
              Deploy Bot
            </div>
            <span className="algo-tab__panel-subtitle">Strategy · caps · backtest</span>
          </div>
        </header>
        <div className="algo-tab__scroll scroll-panel-y scroll-panel-y-0 algo-tab__deploy-body" data-tour="algo-deploy">
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
                  const first = strategyTemplates.find(
                    (t) => (t.execution_mode || 'BAR_CLOSE') === mode
                      && (allowCustomStrategies || (t.strategy !== 'CUSTOM' && !t.custom)),
                  );
                  if (first) selectTemplate(first);
                }}
              >
                <SelectTrigger className="h-8 w-full text-xs" aria-label="Bot execution mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent position="popper">
                  <SelectItem value="BAR_CLOSE" className="text-xs">Bar Close — indicator signals on bar close</SelectItem>
                  <SelectItem value="TICK" className="text-xs">Tick — sub-minute microstructure</SelectItem>
                </SelectContent>
              </Select>
              <span className="algo-field-hint">
                Tick bots evaluate every price update with cooldown; bar bots fire when a {formatBarTimeframeLabel(botTimeframe)} candle closes.
              </span>
            </div>

            {botExecutionMode === 'BAR_CLOSE' && (
            <div className="algo-deploy-field">
              <Label className="algo-field-label">Bar Timeframe</Label>
              <Select value={botTimeframe} onValueChange={setBotTimeframe}>
                <SelectTrigger className="h-8 w-full text-xs" aria-label="Bot bar timeframe">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent position="popper">
                  {BAR_TIMEFRAMES.map((tf) => (
                    <SelectItem key={tf} value={tf} className="text-xs">{tf} bars</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span className="algo-field-hint">
                Strategy evaluates on closed {formatBarTimeframeLabel(botTimeframe)} candles — same resolution as backtest below.
              </span>
            </div>
            )}

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
              <Label className="algo-field-label">Trailing Stop Loss</Label>
              <InputGroup className="h-8">
                <InputGroupInput
                  type="number"
                  step="any"
                  min="0"
                  value={botConfig?.trailing_stop_percent ?? 2}
                  onChange={e => updateBotConfig({
                    trailing_stop_percent: parseFloat(e.target.value) || 0,
                  })}
                  className="text-xs"
                  aria-label="Trailing stop loss percent"
                />
                <InputGroupAddon align="inline-end">
                  <InputGroupText className="text-xs">%</InputGroupText>
                </InputGroupAddon>
              </InputGroup>
              <span className="algo-field-hint">
                Exits when price retraces this % from the best price since entry. Applied on every new position.
              </span>
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
              <Label className="algo-field-label">Max notional cap</Label>
              <InputGroup className="h-8">
                <InputGroupInput
                  type="number"
                  step="any"
                  value={botConfig?.allocation || ''}
                  onChange={e => updateBotConfig({ allocation: parseFloat(e.target.value) || 0 })}
                  className="text-xs"
                  aria-label="Max notional cap"
                />
                <InputGroupAddon align="inline-end">
                  <InputGroupText className="text-xs">$</InputGroupText>
                </InputGroupAddon>
              </InputGroup>
              <span className="algo-field-hint">
                Hard limit on position size per trade. Risk is sized at 1% of account balance using ATR-based stops. Signals evaluate on closed {formatBarTimeframeLabel(botTimeframe)} bars.
              </span>
            </div>

            {botStrategy === 'CHART_AGENT' && (
              <div className="algo-deploy-field space-y-2">
                <Label className="algo-field-label">Chart Agent Settings</Label>
                <ChartAgentDeployPreview
                  symbol={activeSymbol}
                  timeframe={botTimeframe}
                  agentInsights={agentInsights}
                  allocation={botConfig?.allocation}
                  tickerPrice={tickerPrice}
                />
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
                    checked={botConfig?.use_vol_sizing !== false}
                    onChange={e => updateBotConfig({ use_vol_sizing: e.target.checked })}
                    className="accent-primary"
                  />
                  Scale size by risk sub-report (volatility factor)
                </label>
                <label className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={Boolean(botConfig?.require_trend_alignment)}
                    onChange={e => updateBotConfig({ require_trend_alignment: e.target.checked })}
                    className="accent-primary"
                  />
                  Require trend alignment (BUY ≥ +1, SELL ≤ −1)
                </label>
                <label className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={Boolean(botConfig?.block_elevated_vol)}
                    onChange={e => updateBotConfig({ block_elevated_vol: e.target.checked })}
                    className="accent-primary"
                  />
                  Block entries when ATR regime is elevated
                </label>
                <div>
                  <Label className="text-[0.62rem] text-muted-foreground">Min score (optional)</Label>
                  <InputGroup className="mt-1 h-8">
                    <InputGroupInput
                      type="number"
                      min={0}
                      step={1}
                      className="text-xs"
                      placeholder="Any"
                      value={botConfig?.min_score ?? ''}
                      onChange={(e) => updateBotConfig({
                        min_score: e.target.value === '' ? undefined : parseInt(e.target.value, 10) || 0,
                      })}
                    />
                  </InputGroup>
                </div>
                <div>
                  <Label className="text-[0.62rem] text-muted-foreground">Confirm timeframe</Label>
                  <Select
                    value={botConfig?.confirm_timeframe || '__none__'}
                    onValueChange={(v) => updateBotConfig({
                      confirm_timeframe: v === '__none__' ? '' : v,
                    })}
                  >
                    <SelectTrigger className="mt-1 h-8 w-full text-xs" aria-label="Higher timeframe confirmation">
                      <SelectValue placeholder="Disabled" />
                    </SelectTrigger>
                    <SelectContent position="popper">
                      <SelectItem value="__none__" className="text-xs">Disabled</SelectItem>
                      {BAR_TIMEFRAMES.filter((tf) => tf !== botTimeframe).map((tf) => (
                        <SelectItem key={tf} value={tf} className="text-xs">{tf} trend confirm</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <span className="algo-field-hint">Higher-TF trend must agree before entry.</span>
                </div>
                <label className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={Boolean(botConfig?.use_llm)}
                    onChange={e => updateBotConfig({ use_llm: e.target.checked })}
                    className="accent-primary"
                  />
                  Use LLM explanations on strong signals (Ollama local or OpenRouter when enabled)
                </label>
              </div>
            )}

            <div className="algo-deploy-field">
              <Label className="algo-field-label">Backtest Timeframe</Label>
              <Select value={botTimeframe} onValueChange={setBotTimeframe} disabled={botExecutionMode === 'TICK'}>
                <SelectTrigger className="h-8 w-full text-xs" aria-label="Backtest bar timeframe">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent position="popper">
                  {BAR_TIMEFRAMES.map((tf) => (
                    <SelectItem key={tf} value={tf} className="text-xs">{tf} bars</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span className="algo-field-hint">
                {massiveLive
                  ? 'Shared with deploy timeframe — backtest uses archive; live Massive bots use native HT REST where available.'
                  : 'Shared with deploy timeframe — resampled from archived 1m data.'}
              </span>
            </div>

            <div className="algo-deploy-field">
              <Label className="algo-field-label">Execution costs</Label>
              <div className="grid grid-cols-2 gap-2">
                <InputGroup className="h-8">
                  <InputGroupInput
                    type="number"
                    min={0}
                    step={1}
                    className="text-xs"
                    placeholder="Slip bps"
                    value={botConfig?.slippage_bps ?? ''}
                    onChange={(e) => updateBotConfig({
                      slippage_bps: e.target.value === '' ? undefined : parseFloat(e.target.value) || 0,
                    })}
                  />
                </InputGroup>
                <InputGroup className="h-8">
                  <InputGroupInput
                    type="number"
                    min={0}
                    step={1}
                    className="text-xs"
                    placeholder="Fee bps"
                    value={botConfig?.fee_bps ?? ''}
                    onChange={(e) => updateBotConfig({
                      fee_bps: e.target.value === '' ? undefined : parseFloat(e.target.value) || 0,
                    })}
                  />
                </InputGroup>
              </div>
              <span className="algo-field-hint">Applied per fill in backtest (basis points).</span>
            </div>

            <label className="algo-backtest-oos flex items-center gap-2 text-[0.62rem] text-muted-foreground cursor-pointer">
              <input
                type="checkbox"
                className="size-3.5 accent-primary"
                checked={backtestOos}
                onChange={(e) => setBacktestOos(e.target.checked)}
              />
              Hold-out test (last 30%) — test on last 30% of range only
            </label>

            <label className="flex items-center gap-2 text-[0.62rem] text-muted-foreground cursor-pointer">
              <input
                type="checkbox"
                className="size-3.5 accent-primary"
                checked={portfolioBacktest}
                onChange={(e) => setPortfolioBacktest(e.target.checked)}
              />
              Portfolio backtest — run same strategy on top 5 watchlist symbols
            </label>

            {agentLlmAvailable ? (
              <label className="flex items-center gap-2 text-[0.62rem] text-muted-foreground cursor-pointer">
                <input
                  type="checkbox"
                  className="size-3.5 accent-primary"
                  checked={backtestReasoning}
                  onChange={(e) => setBacktestReasoning(e.target.checked)}
                />
                Generate trade explanations after backtest (LLM post-hoc, rules unchanged)
              </label>
            ) : (
              <p className="text-[0.62rem] text-muted-foreground">
                LLM unavailable — start Ollama or configure OpenRouter to enable post-backtest trade explanations.
              </p>
            )}

            <div className="algo-deploy-field">
              <Label className="algo-field-label">Risk base (backtest)</Label>
              <Select value={backtestRiskBaseMode} onValueChange={setBacktestRiskBaseMode}>
                <SelectTrigger className="h-8 w-full text-xs" aria-label="Backtest risk base mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent position="popper">
                  <SelectItem value="account_snapshot" className="text-xs">
                    Account snapshot{cashTotal > 0 ? ` ($${cashTotal.toLocaleString()} cash)` : ''}
                  </SelectItem>
                  <SelectItem value="simulated_equity" className="text-xs">Simulated equity (compounding)</SelectItem>
                </SelectContent>
              </Select>
              <span className="algo-field-hint">
                Matches live sizing: 1% of account cash at run time, or 1% of running backtest equity.
              </span>
            </div>

            <div className="algo-deploy-field">
              <Label className="algo-field-label">Simulation mode</Label>
              <Select value={backtestSimMode} onValueChange={setBacktestSimMode}>
                <SelectTrigger className="h-8 w-full text-xs" aria-label="Backtest simulation mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent position="popper">
                  <SelectItem value="live_aligned" className="text-xs">Live-aligned (risk gates)</SelectItem>
                  <SelectItem value="research" className="text-xs">Research (shorts + no risk gates)</SelectItem>
                </SelectContent>
              </Select>
              {backtestSimMode === 'research' && (
                <p className="algo-field-hint text-[10px] text-muted-foreground mt-1">
                  SELL signals open short positions; SL/TP apply to shorts.
                </p>
              )}
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
                {botTimeframe === '1m'
                  ? 'Uses archived 1m bars when range exceeds live buffer.'
                  : 'Ranges above 90d are capped to 1m archive retention for accurate resampling.'}
              </span>
            </div>

            {backtestRunning && <BacktestProgressBar compact />}

            {backtestResults && (
              <BacktestResultsPanel
                results={backtestResults}
                backtestDays={backtestDays}
                backtestTimeframe={botTimeframe}
                symbol={activeSymbol}
                strategy={botStrategy}
                recentRuns={backtestRuns}
                snapshot={backtestSnapshot}
                oosPct={backtestOos ? 30 : null}
                reasoningPending={backtestReasoning && backtestRunning}
                showReasoningSection={agentLlmAvailable}
                advisorBotId={selectedBotId}
                agentLlmAvailable={agentLlmAvailable}
              />
            )}
          </div>
        </div>
        <footer className="algo-tab__panel-footer algo-deploy-actions">
          <div className="algo-deploy-actions__rail">
            <Button
              variant="ghost"
              size="sm"
              className="algo-deploy-actions__btn"
              onClick={handleRunBacktest}
              disabled={backtestRunning}
            >
              {backtestRunning ? (
                <Loader2 className="size-3.5 animate-spin" data-icon="inline-start" />
              ) : (
                <Activity data-icon="inline-start" />
              )}
              {backtestRunning ? 'RUNNING…' : 'BACKTEST'}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="algo-deploy-actions__btn"
              onClick={handleOpenOptimizer}
              disabled={backtestRunning}
              title="Open Backtest Lab optimizer with current symbol, strategy, and config"
            >
              OPTIMIZE
            </Button>
            {backtestRunning && (
              <Button
                variant="ghost"
                size="sm"
                className="algo-deploy-actions__btn algo-deploy-actions__btn--cancel"
                onClick={handleCancelBacktest}
                title="Cancel running backtest"
              >
                <XSquare size={14} />
              </Button>
            )}
            {backtestResults && (
              <Button
                variant="ghost"
                size="sm"
                className="algo-deploy-actions__btn algo-deploy-actions__btn--utility"
                onClick={() => openBacktestLabResults()}
                title="Open Backtest Lab → Results tab"
              >
                <Maximize2 size={14} data-icon="inline-start" />
                LAB
              </Button>
            )}
            <Button
              variant="buy"
              size="sm"
              className="algo-deploy-actions__btn algo-deploy-actions__btn--deploy"
              onClick={() => setDeployOpen(true)}
              disabled={liveBotsBlocked}
              title={liveBotsBlocked ? 'Live bot trading disabled on server' : 'Deploy bot'}
            >
              <Play data-icon="inline-start" />
              DEPLOY
            </Button>
          </div>
        </footer>
      </section>

      <Dialog open={deployOpen} onOpenChange={setDeployOpen}>
        <DialogContent className="algo-dialog sm:max-w-md" overlayClassName="admin-panel-overlay">
          <DialogHeader>
            <DialogTitle>Deploy trading bot</DialogTitle>
            <DialogDescription className="text-xs leading-relaxed">
              This will start a live bot on the server using your current template and max notional cap.
            </DialogDescription>
          </DialogHeader>
          <div className="algo-dialog-summary">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground shrink-0">Strategy:</span>
              <StrategyBadge strategy={botStrategy} />
            </div>
            <div><span className="text-muted-foreground">Symbol:</span> <strong>{activeSymbol}</strong></div>
            <div><span className="text-muted-foreground">Max cap:</span> <strong>${botConfig?.allocation?.toLocaleString() ?? 0}</strong></div>
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
            <div><span className="text-muted-foreground">Timeframe:</span> <strong>{deployTimeframeSummary(botExecutionMode, botTimeframe)}</strong></div>
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
          <DataTableRoot variant="dock" className="algo-bots-table m-0">
            <DataTableHeader>
              <tr>
                <DataTableHead>Symbol</DataTableHead>
                <DataTableHead>Strategy</DataTableHead>
                <DataTableHead align="center">TF</DataTableHead>
                <DataTableHead align="center">Position</DataTableHead>
                <DataTableHead align="right">Cap</DataTableHead>
                <DataTableHead align="right">Today PnL</DataTableHead>
                <DataTableHead>Last signal</DataTableHead>
                <DataTableHead align="center">Status</DataTableHead>
                <DataTableHead align="center">Actions</DataTableHead>
              </tr>
            </DataTableHeader>
            <DataTableBody>
              {activeBots.length === 0 ? (
                <DataTableRow rowVariant="dock">
                  <DataTableCell colSpan={9} className="algo-table-empty">
                    No active bots. Pick a template and deploy.
                  </DataTableCell>
                </DataTableRow>
              ) : (
                activeBots.map(bot => {
                  const pos = positions[bot.symbol];
                  const inPosition = pos && Math.abs(pos.size) > 0;
                  return (
                  <DataTableRow
                    key={bot.id}
                    rowVariant="dock"
                    deferred
                    className={cn('algo-bot-row cursor-pointer', selectedBotId === bot.id && 'row-active')}
                    onClick={() => selectBot(bot.id)}
                  >
                    <DataTableCell className="font-bold">{bot.symbol}</DataTableCell>
                    <DataTableCell className="text-xs">
                      <StrategyBadge strategy={bot.strategy} compact />
                      {bot.execution_mode === 'TICK' && (
                        <Badge variant="outline" className="ml-1 h-4 px-1 text-[0.65rem]">TICK</Badge>
                      )}
                    </DataTableCell>
                    <DataTableCell align="center" className="text-xs num-mono text-muted-foreground">
                      {bot.execution_mode === 'TICK' ? 'tick' : formatBarTimeframeLabel(bot.timeframe)}
                    </DataTableCell>
                    <DataTableCell align="center">
                      {inPosition ? (
                        <Badge variant={pos.size > 0 ? 'buy' : 'sell'}>
                          {pos.size > 0 ? 'LONG' : 'SHORT'}
                        </Badge>
                      ) : (
                        <span className="text-secondary-foreground text-xs">FLAT</span>
                      )}
                    </DataTableCell>
                    <DataTableCell numeric align="right">${bot.allocation.toLocaleString()}</DataTableCell>
                    <DataTableCell
                      numeric
                      align="right"
                      className={cn(
                        'font-semibold',
                        (bot.daily_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down',
                      )}
                    >
                      {(bot.daily_pnl ?? 0) >= 0 ? '+' : ''}{(bot.daily_pnl ?? 0).toFixed(2)}
                    </DataTableCell>
                    <DataTableCell className="algo-last-signal">
                      <span title={bot.last_signal_at || undefined}>{formatLastSignal(bot.last_signal_at)}</span>
                      {bot.strategy === 'CHART_AGENT' && (() => {
                        const insight = selectAgentInsight(
                          agentInsights,
                          bot.symbol,
                          bot.execution_mode === 'TICK' ? '1m' : bot.timeframe,
                        );
                        return insight?.confidence != null ? (
                          <span className="ml-1 text-xs text-muted-foreground">
                            ({Math.round(insight.confidence * 100)}% conf)
                          </span>
                        ) : null;
                      })()}
                    </DataTableCell>
                    <DataTableCell align="center">
                      <Badge variant={statusBadgeVariant(bot.status)}>{bot.status}</Badge>
                    </DataTableCell>
                    <DataTableCell align="center" onClick={e => e.stopPropagation()}>
                      <div className="algo-bot-actions">
                        {bot.status === 'RUNNING' && (
                          <Button variant="outline" size="xs" onClick={() => handlePauseBot(bot.id)} title="Pause bot">
                            <Pause />
                          </Button>
                        )}
                        {bot.status === 'PAUSED' && (
                          <Button variant="outline" size="xs" onClick={() => handleResumeBot(bot.id)} title="Resume bot">
                            <PlayCircle />
                          </Button>
                        )}
                        {bot.status !== 'STOPPED' && (
                          <Button
                            variant="outline"
                            size="xs"
                            onClick={() => handleSetBotStopLoss(bot)}
                            title="Set stop loss on chart"
                          >
                            SL
                          </Button>
                        )}
                        {bot.status !== 'STOPPED' && (
                          <Button
                            variant="outline"
                            size="xs"
                            onClick={() => handleSetBotTakeProfit(bot)}
                            title="Set take profit on chart"
                          >
                            TP
                          </Button>
                        )}
                        {bot.status !== 'STOPPED' && (
                          <Button variant="destructive" size="xs" onClick={() => handleStopBot(bot.id)} title="Stop bot">
                            STOP
                          </Button>
                        )}
                      </div>
                    </DataTableCell>
                  </DataTableRow>
                  );
                })
              )}
            </DataTableBody>
          </DataTableRoot>
        </ScrollTablePanel>
      </section>

      <section className="algo-tab__panel algo-tab__panel--log">
        <header className="algo-tab__panel-header">
          <div className="algo-tab__panel-heading">
            <div className="algo-tab__panel-title">
              <Activity size={13} className="text-muted-foreground" aria-hidden />
              Bot Log
            </div>
            <span className="algo-tab__panel-subtitle">{filteredBotLogs.length} entries</span>
          </div>
          <div className="flex items-center gap-1">
            <Select value={logFilter} onValueChange={setLogFilter}>
              <SelectTrigger className="h-7 w-[7.5rem] text-xs" aria-label="Log filter">
                <SelectValue />
              </SelectTrigger>
              <SelectContent position="popper">
                <SelectItem value="all" className="text-xs">All logs</SelectItem>
                <SelectItem value="signals" className="text-xs">Signals only</SelectItem>
                <SelectItem value="agent_skips" className="text-xs">Agent skips</SelectItem>
              </SelectContent>
            </Select>
            <Button variant="ghost" size="icon-sm" onClick={clearBotLogs} title="Clear log" aria-label="Clear bot log">
              <Trash2 />
            </Button>
          </div>
        </header>

        <div
          ref={logScrollRef}
          className="algo-tab__scroll algo-bot-log-scroll scroll-panel-y scroll-panel-y-0"
          onScroll={onLogScroll}
        >
          <div className="algo-tab__log-list">
            {filteredBotLogs.length === 0 ? (
              <WidgetEmpty icon={Cpu} message="Bot console is empty" className="min-h-[80px]" />
            ) : (
              <>
                <div style={{ height: logWindow.topPad }} aria-hidden />
                {logWindow.slice.map((log, i) => {
                  const idx = logWindow.start + i;
                  const hasInsightMeta = Boolean(
                    log.meta?.insight_id
                    || log.meta?.sub_reports
                    || (log.meta?.reasons?.length > 0),
                  );
                  const showInsight = isSignalLog(log) && (
                    hasInsightMeta
                    || log.meta?.bar_time != null
                    || /signal @/i.test(log.message || log.line || '')
                  );
                  const display = log.line ?? log.message ?? String(log);
                  const openInsight = () => {
                    window.dispatchEvent(new CustomEvent('signal-insight-open', { detail: { log } }));
                  };
                  return (
                    <div
                      key={log.id ?? `${idx}-${display.slice(0, 24)}`}
                      className={cn(
                        logLineClassLocal(log),
                        showInsight && 'group relative cursor-pointer hover:bg-muted/30',
                      )}
                      role={showInsight ? 'button' : undefined}
                      tabIndex={showInsight ? 0 : undefined}
                      onClick={showInsight ? openInsight : undefined}
                      onKeyDown={showInsight ? (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          openInsight();
                        }
                      } : undefined}
                    >
                      <span>{display}</span>
                      {showInsight && (
                        <button
                          type="button"
                          className="ml-2 text-xs text-primary opacity-70 group-hover:opacity-100"
                          onClick={(e) => {
                            e.stopPropagation();
                            openInsight();
                          }}
                        >
                          Explain
                        </button>
                      )}
                    </div>
                  );
                })}
                <div style={{ height: logWindow.bottomPad }} aria-hidden />
              </>
            )}
          </div>
        </div>
      </section>
      </div>
    </div>
  );
}

