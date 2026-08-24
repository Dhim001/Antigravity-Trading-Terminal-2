/**
 * AlgoPanel.jsx — Algo Bot dock tab (extracted from ResizableDock).
 */
import React, { useState, useRef, useEffect, useCallback, useMemo, useSyncExternalStore } from 'react';
import { toast } from 'sonner';
import { useStore } from '../../store/useStore';
import { useResearchStore } from '../../store/useResearchStore';
import { sendAction } from '../../api/transport';
import { Action } from '../../api/protocol';
import { fetchBots, withLlmModel } from '../../api/endpoints';
import { getStoreActions } from '../../api/dispatch';
import { selectCashTotal } from '../../store/selectors';
import { useShallow } from 'zustand/react/shallow';
import {
  Cpu, Play, Settings, Trash2, XSquare, ShieldAlert, Pause, PlayCircle, OctagonX,
  RefreshCw, AlertTriangle, Activity, Loader2, Maximize2, Bot, BrainCircuit,
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
import MlModelStatusBadge, { isMlStrategy } from '../MlModelStatusBadge';
import MlModelVersionSelect from '../MlModelVersionSelect';
import BacktestResultsPanel from '../BacktestResultsPanel';
import BacktestProgressBar from '../BacktestProgressBar';
import ChartAgentDeployPreview from '../ChartAgentDeployPreview';
import { pickDeployConfig, confidenceRangeForStrategy } from '@/lib/botConfigDisplay';
import { openModelTrainingDock } from '@/lib/workspaceNav';
import { ScrollTablePanel, WidgetEmpty } from '../WidgetShell';
import { usePinnedPrependScroll } from '@/hooks/usePinnedPrependScroll';
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
  backtestTimeoutHint,
} from '../../lib/backtestTimeouts';
import { isDeferredBacktestStillAlive, stopBacktestJobPolling } from '../../lib/backtestPolling';
import PortfolioBacktestPicker from '../PortfolioBacktestPicker';
import { canRunPortfolioBacktest } from '../../lib/portfolioBacktest';
import { formatRunEstimate } from '../../lib/backtestRunEstimate';
import BacktestWorkflowPresets, { applyWorkflowPreset } from '../BacktestWorkflowPresets';
import BacktestStaleBanner from '../BacktestStaleBanner';
import BacktestErrorRecovery from '../BacktestErrorRecovery';
import { slimBacktestForDock } from '../../lib/backtestSlim';
import { cn } from '@/lib/utils';
import { openBacktestLabResults } from '../../lib/backtestLab';
import { formatLastSignal } from '@/lib/formatTime';
import { BAR_TIMEFRAMES, deployTimeframeSummary, formatBarTimeframeLabel } from '@/lib/barTimeframes';
import { useEffectiveRiskHold, botRuntimeActivityHint } from '@/lib/botRiskHold';
import { getBotOwnedPositionView, normalizeBotStatus } from '@/lib/botAttribution';
import { DIRECTION_MODE_OPTIONS, formatDirectionModeLabel } from '@/lib/botConfigDisplay';
import { isLiveMassiveMode, isPaperExecutionMode, usesNativeHtCharts } from '@/lib/massiveMarket';
import { backtestContextMismatch, backtestFingerprint, resolveBacktestResultIdentity } from '@/lib/backtestDisplay';
import { backtestResultsMatchTarget, buildDeployPayload } from '@/lib/deployGate';
import { evaluateAndMaybeDeploy } from '@/lib/pipelineAutoGate';
import {
  advancePipeline,
  completePipeline,
  failPipeline,
  getMlPipeline,
  setPipelineBacktestResult,
  setPipelineGateResult,
  subscribeMlPipeline,
} from '@/lib/mlPipeline';
import { postMlLabRequest } from '@/lib/mlLabRequests';
import { ALGO_OPEN_DEPLOY_EVENT } from '@/lib/pipelineNav';
import DeployGatePanel from '../DeployGatePanel';
import ActiveBotRow from './ActiveBotRow';
import AgentActionsPanel from './AgentActionsPanel';
import BotReasoningPanel from './BotReasoningPanel';
import { selectAgentInsight } from '@/lib/agentInsights';
import { isSignalLog, logLineClass } from '@/lib/botLogInsight';
import { getCachedModelStatus } from '@/lib/mlTrainingSession';
import {
  ML_BACKTEST_RANGE_HOLDOUT,
  ML_FREE_RANGE_DAYS,
  TA_BACKTEST_RANGE_DAYS,
  coerceBacktestDaysForStrategy,
  mlBacktestRangeHint,
  resolveHoldoutDaysFromStatus,
  resolveMlBacktestDaysPayload,
} from '@/lib/mlBacktestRange';

function statusBadgeVariant(status) {
  if (status === 'RUNNING') return 'buy';
  if (status === 'PAUSED') return 'secondary';
  if (status === 'ERROR') return 'destructive';
  return 'sell';
}

function activityHintVariant(kind) {
  if (kind === 'cooling_off') return 'outline';
  if (kind === 'held') return 'secondary';
  return 'outline';
}

// ── Algo Bot Tab ──────────────────────────────────────────────────
export function AlgoTab({ hideToolbar = false }) {
  const {
    activeBots, botStrategy, botExecutionMode, botTimeframe, botConfig, activeSymbol, symbolsList,
    setBotStrategy, setBotExecutionMode, setBotTimeframe, updateBotConfig, replaceBotConfig, clearBotLogs, botLogs,
    strategyTemplates,
    setChartInteractionMode,
    isLive, allowLiveBots, allowCustomStrategies, terminalMode, terminalRole, distributed, botMinCandles,
    executionMode,
    setActiveSymbol,
    selectedBotId, setSelectedBotId, setBotDetail, setBotDrawerOpen,
    ambiguousOrders,
    safeMode, setSafeMode,
    systemStats,
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
    replaceBotConfig: s.replaceBotConfig,
    clearBotLogs: s.clearBotLogs,
    botLogs: s.botLogs,
    strategyTemplates: s.strategyTemplates,
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
    safeMode: s.safeMode,
    setSafeMode: s.setSafeMode,
    systemStats: s.systemStats,
  })));
  const {
    backtestResults, backtestRuns, backtestRunning, backtestSnapshot,
    backtestLabOpen, backtestLastError, backtestLastRequest, backtestJobId,
    setBacktestRunning, setBacktestProgress, setBacktestSnapshot, beginBacktestRun,
    upsertBacktestJobSlot,
    openBacktestLab, setStoreBacktestDays, setStoreBacktestOos,
    clearBacktestLastError, setBacktestLastError,
  } = useResearchStore(useShallow((s) => ({
    backtestResults: s.backtestResults,
    backtestRuns: s.backtestRuns,
    backtestRunning: s.backtestRunning,
    backtestSnapshot: s.backtestSnapshot,
    backtestLabOpen: s.backtestLabOpen,
    backtestLastError: s.backtestLastError,
    backtestLastRequest: s.backtestLastRequest,
    backtestJobId: s.backtestJobId,
    setBacktestRunning: s.setBacktestRunning,
    setBacktestProgress: s.setBacktestProgress,
    setBacktestSnapshot: s.setBacktestSnapshot,
    beginBacktestRun: s.beginBacktestRun,
    upsertBacktestJobSlot: s.upsertBacktestJobSlot,
    openBacktestLab: s.openBacktestLab,
    setStoreBacktestDays: s.setBacktestDays,
    setStoreBacktestOos: s.setBacktestOos,
    clearBacktestLastError: s.clearBacktestLastError,
    setBacktestLastError: s.setBacktestLastError,
  })));
  const positions = useStore((state) => state.positions);
  const balances = useStore((state) => state.balances);
  const agentInsights = useResearchStore((state) => state.agentInsights);
  const tickerPrice = useStore((state) => state.tickerData[state.activeSymbol]?.price);
  const cashTotal = useStore(selectCashTotal);
  /** Per-symbol quote bucket for risk_base (USDT crypto / USD equity). */
  const quoteCashForSymbol = useMemo(() => {
    const preferred = String(activeSymbol || '').includes('USDT') ? 'USDT' : 'USD';
    const alt = preferred === 'USDT' ? 'USD' : 'USDT';
    const quote = balances?.[preferred] != null
      ? preferred
      : (balances?.[alt] != null ? alt : preferred);
    const row = balances?.[quote];
    if (!row) return cashTotal;
    return Math.round((row.balance ?? 0) - (row.locked ?? 0));
  }, [activeSymbol, balances, cashTotal]);

  const liveBotsBlocked = isLive && !allowLiveBots;
  const paperExecution = isPaperExecutionMode(terminalMode, executionMode);
  const massiveLive = isLiveMassiveMode(terminalMode);
  const nativeHtLive = usesNativeHtCharts(terminalMode);
  const alpacaLive = terminalMode === 'LIVE_ALPACA';
  const safeModeActive = Boolean(safeMode?.active);
  const runningCount = activeBots.filter((b) => normalizeBotStatus(b.status) === 'RUNNING').length;
  const pausedCount = activeBots.filter((b) => normalizeBotStatus(b.status) === 'PAUSED').length;
  const mlPipeline = useSyncExternalStore(
    subscribeMlPipeline,
    getMlPipeline,
    getMlPipeline,
  );
  const pipelineBtStartedRef = useRef(null);
  /** Set only after a pipeline-kicked backtest actually enters running. */
  const pipelineBtSawRunningRef = useRef(null);
  const pipelineGateDoneRef = useRef(null);
  const [deployOpen, setDeployOpen] = useState(false);
  const [forceDeploy, setForceDeploy] = useState(false);
  const [deployGate, setDeployGate] = useState(null);
  const [pipelineDeployPending, setPipelineDeployPending] = useState(null);
  const [stopAllOpen, setStopAllOpen] = useState(false);
  const [backtestDays, setBacktestDaysLocal] = useState('7');
  const [backtestOos, setBacktestOosLocal] = useState(false);
  const [backtestReasoning, setBacktestReasoning] = useState(false);
  const [backtestSimMode, setBacktestSimMode] = useState('live_aligned');
  const [backtestLiveParity, setBacktestLiveParity] = useState(true);
  const [backtestRiskBaseMode, setBacktestRiskBaseMode] = useState('account_snapshot');
  const [portfolioBacktest, setPortfolioBacktest] = useState(false);
  const [portfolioSymbols, setPortfolioSymbols] = useState([]);
  const [metaLabelWalkForward, setMetaLabelWalkForward] = useState(false);
  const [activeWorkflowPreset, setActiveWorkflowPreset] = useState(null);
  const [logFilter, setLogFilter] = useState('all');
  const agentLlmAvailable = useStore((s) => s.agentLlmAvailable);
  const agentLlmEnabled = useStore((s) => s.agentLlmEnabled);
  const [botCategoryTab, setBotCategoryTab] = useState('normal');
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
  const { ref: logScrollRef, onScroll: onLogScroll } = usePinnedPrependScroll(filteredBotLogs);

  useEffect(() => {
    fetchBots(getStoreActions()).catch(() => {});
    // Keep safe-mode flag fresh when the Algo panel mounts (session hydrate may be stale).
    sendAction(Action.ADMIN_GET_STATS, {});
  }, []);

  useEffect(() => {
    const onOpenDeploy = () => {
      setForceDeploy(false);
      setDeployOpen(true);
    };
    window.addEventListener(ALGO_OPEN_DEPLOY_EVENT, onOpenDeploy);
    return () => window.removeEventListener(ALGO_OPEN_DEPLOY_EVENT, onOpenDeploy);
  }, []);

  useEffect(() => () => {
    // Keep the client timeout alive across Algo tab unmounts while a job runs
    // (workspace remounts / flexlayout). Completion paths clear it explicitly.
    if (!useResearchStore.getState().backtestRunning) {
      clearBacktestClientTimeout();
    }
  }, []);

  const handleConfirmSafeMode = useCallback(() => {
    sendAction(Action.ADMIN_CONFIRM_SAFE_MODE, {});
    setSafeMode({ active: false });
    toast.success('Safe mode cleared — resume bots when ready');
    sendAction(Action.ADMIN_GET_STATS, {});
  }, [setSafeMode]);

  useEffect(() => {
    if (backtestSimMode === 'research') {
      setBacktestLiveParity(false);
    } else if (backtestSimMode === 'live_aligned') {
      setBacktestLiveParity(true);
    }
  }, [backtestSimMode]);

  const portfolioList = portfolioBacktest && canRunPortfolioBacktest(portfolioSymbols)
    ? portfolioSymbols
    : undefined;
  const portfolioSymbolCount = portfolioList?.length ?? 0;

  const mlStrategySelected = isMlStrategy(botStrategy);
  const mlHoldoutDays = useMemo(() => {
    if (!mlStrategySelected) return 14;
    const status = getCachedModelStatus(
      activeSymbol,
      botStrategy,
      botExecutionMode === 'TICK' ? '1m' : (botTimeframe || '1m'),
    );
    return resolveHoldoutDaysFromStatus(status, botConfig);
  }, [mlStrategySelected, activeSymbol, botStrategy, botTimeframe, botExecutionMode, botConfig]);

  useEffect(() => {
    const next = coerceBacktestDaysForStrategy(backtestDays, { isMl: mlStrategySelected });
    if (next !== String(backtestDays)) {
      setBacktestDaysLocal(next);
      setStoreBacktestDays(next);
    }
  }, [mlStrategySelected, botStrategy]); // eslint-disable-line react-hooks/exhaustive-deps -- coerce on strategy class change only

  const resolvedBtDays = useMemo(
    () => resolveMlBacktestDaysPayload(backtestDays, mlHoldoutDays, { isMl: mlStrategySelected }),
    [backtestDays, mlHoldoutDays, mlStrategySelected],
  );

  const runEstimate = useMemo(() => formatRunEstimate({
    days: resolvedBtDays.days,
    portfolioSymbols: portfolioList,
    portfolioSymbolCount,
    reasoning: backtestReasoning,
    metaLabelWalkForward: botStrategy === 'CHART_AGENT' && metaLabelWalkForward,
    walkForward: false,
    strategy: botStrategy,
    deferred: portfolioSymbolCount >= 2
      || resolvedBtDays.days >= 30
      || backtestReasoning
      || (botStrategy === 'CHART_AGENT' && metaLabelWalkForward),
  }), [
    resolvedBtDays.days, portfolioList, portfolioSymbolCount, backtestReasoning,
    botStrategy, metaLabelWalkForward,
  ]);

  const dockPreview = useMemo(
    () => (backtestLabOpen && backtestResults ? slimBacktestForDock(backtestResults) : null),
    [backtestLabOpen, backtestResults],
  );

  const backtestIdentity = useMemo(
    () => resolveBacktestResultIdentity(backtestResults, {
      symbol: activeSymbol,
      strategy: botStrategy,
      timeframe: botTimeframe,
      days: backtestDays,
    }),
    [backtestResults, activeSymbol, botStrategy, botTimeframe, backtestDays],
  );

  const resultContextMismatch = useMemo(
    () => backtestContextMismatch(backtestResults, {
      symbol: activeSymbol,
      strategy: botStrategy,
    }),
    [backtestResults, activeSymbol, botStrategy],
  );

  const setBacktestDays = (days) => {
    const next = coerceBacktestDaysForStrategy(days, { isMl: mlStrategySelected });
    setBacktestDaysLocal(next);
    setStoreBacktestDays(next);
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

  const handleWorkflowPreset = (presetId) => {
    const ok = applyWorkflowPreset(presetId, {
      activeSymbol,
      symbolsList,
      botStrategy,
      botTimeframe,
      setBacktestDays,
      setBacktestOos,
      setBacktestReasoning,
      setPortfolioBacktest,
      setPortfolioSymbols,
      setBacktestSimMode,
      setBacktestLiveParity,
      setMetaLabelWalkForward,
      openBacktestLab,
      setOptimizerPreset: useResearchStore.getState().setOptimizerPreset,
      onMlPipelineTrain: ({ pipelineId, strategy, symbol, timeframe, mode }) => {
        openModelTrainingDock();
        // Forward pipelineId so the Lab reuses the preset-started run instead
        // of starting (and orphaning) a second one.
        postMlLabRequest('ml-lab-run-pipeline', { pipelineId, strategy, symbol, timeframe, mode });
      },
      openBatchTrainDialog: () => {
        // applyWorkflowPreset posts the ml-lab-open-batch request itself.
        openModelTrainingDock();
      },
    });
    if (ok) {
      setActiveWorkflowPreset(presetId);
      const mlPresets = ['ml_full_pipeline', 'ml_retrain_validate', 'ml_batch_train'];
      const opensLabOnly = ['wf_optimize', 'wf_rigorous', 'meta_label_sweep', 'portfolio_optimize', ...mlPresets].includes(presetId);
      if (!opensLabOnly) {
        toast.message('Preset applied — review settings then RUN');
      } else if (mlPresets.includes(presetId)) {
        toast.message(presetId === 'ml_batch_train' ? 'Batch train opened' : 'ML pipeline started');
      }
    } else {
      toast.error('Preset not available for this strategy');
    }
  };

  const buildBacktestRequest = useCallback((patch = {}) => {
    const { days, ml_backtest_range: rangeMode } = resolveMlBacktestDaysPayload(
      backtestDays,
      mlHoldoutDays,
      { isMl: isMlStrategy(botStrategy) },
    );
    const isTick = botExecutionMode === 'TICK';
    const list = portfolioBacktest && canRunPortfolioBacktest(portfolioSymbols)
      ? portfolioSymbols
      : undefined;
    const multiAsset = Boolean(list && list.length > 1);
    // Portfolio spans both ledgers → total quote cash; single symbol → its quote bucket.
    const riskBase = multiAsset ? cashTotal : quoteCashForSymbol;
    return {
      strategy: botStrategy,
      symbol: activeSymbol,
      config: {
        ...botConfig,
        sim_mode: backtestSimMode,
        live_parity: backtestLiveParity,
        risk_base_mode: backtestRiskBaseMode,
        ...(riskBase > 0 ? { risk_base: riskBase } : {}),
        ...(selectedBotId ? { backtest_bot_id: selectedBotId } : {}),
        ...(botStrategy === 'CHART_AGENT' && metaLabelWalkForward
          ? { meta_label_walk_forward: true }
          : {}),
        ...(rangeMode ? { ml_backtest_range: rangeMode } : {}),
      },
      days: patch.days != null ? patch.days : days,
      timeframe: isTick ? 'tick' : botTimeframe,
      oos_pct: patch.oos_pct != null
        ? patch.oos_pct
        : (backtestOos ? 30 : undefined),
      reasoning: patch.reasoning != null ? patch.reasoning : (backtestReasoning || undefined),
      portfolio_symbols: patch.portfolio_symbols !== undefined
        ? patch.portfolio_symbols
        : (list?.length > 1 ? list : undefined),
      ...patch,
    };
  }, [
    backtestDays, mlHoldoutDays, botExecutionMode, portfolioBacktest, portfolioSymbols, botStrategy,
    activeSymbol, botConfig, backtestSimMode, backtestLiveParity, backtestRiskBaseMode,
    cashTotal, quoteCashForSymbol, selectedBotId, metaLabelWalkForward, botTimeframe, backtestOos, backtestReasoning,
  ]);

  const handleRetryBacktest = async (request) => {
    clearBacktestLastError();
    if (request?.config?.ml_backtest_range === 'holdout') {
      setBacktestDays(ML_BACKTEST_RANGE_HOLDOUT);
    } else if (request?.days) {
      setBacktestDays(String(request.days));
    }
    if (request?.reasoning === false) setBacktestReasoning(false);
    if (request?.portfolio_symbols === undefined && portfolioBacktest) {
      setPortfolioBacktest(false);
    }
    beginBacktestRun();
    setBacktestRunning(true);
    setBacktestProgress({ pct: 0, phase: 'resolve', message: 'Retrying backtest…' });
    scheduleBacktestClientTimeout({
      reasoning: request?.reasoning,
      metaLabelWalkForward: request?.config?.meta_label_walk_forward,
      strategy: request?.strategy,
      days: request?.days,
      portfolioSymbolCount: request?.portfolio_symbols?.length ?? 0,
      onTimeout: (timeoutMs) => {
        const state = useResearchStore.getState();
        if (!state.backtestRunning) return;
        // Parity with Optimizer / TaOptimizerPanel — deferred jobs keep running.
        if (isDeferredBacktestStillAlive(state)) {
          toast.warning('Retry is slower than estimated — still running in the background.', {
            action: {
              label: 'Open Jobs',
              onClick: () => useResearchStore.getState().openBacktestLab('jobs'),
            },
          });
          return;
        }
        stopBacktestJobPolling();
        setBacktestRunning(false);
        setBacktestProgress(null);
        toast.error(`Backtest timed out after ${Math.round(timeoutMs / 60000)} min`);
      },
    });
    const { ok, error } = await sendAction(Action.RUN_BACKTEST, withLlmModel(request));
    if (!ok) {
      clearBacktestClientTimeout();
      setBacktestRunning(false);
      setBacktestProgress(null);
      setBacktestLastError(error || 'Retry failed', request);
      if (error) toast.error(error);
    }
  };

  const handleRunBacktest = async () => {
    if (!botConfig?.allocation || botConfig.allocation <= 0) {
      toast.error('Set a valid max notional cap before backtesting');
      return;
    }

    const { days } = resolvedBtDays;
    const isTick = botExecutionMode === 'TICK';
    const snapshot = backtestFingerprint({
      symbol: activeSymbol,
      strategy: botStrategy,
      days: String(backtestDays),
      timeframe: isTick ? 'tick' : botTimeframe,
      config: botConfig,
      simMode: backtestSimMode,
    });

    beginBacktestRun();
    setBacktestRunning(true);
    setBacktestProgress({
      pct: 0,
      phase: 'resolve',
      message: 'Starting backtest — contacting server…',
      symbol: activeSymbol,
      strategy: botStrategy,
    });
    setBacktestSnapshot(snapshot);
    clearBacktestLastError();

    scheduleBacktestClientTimeout({
      reasoning: backtestReasoning,
      metaLabelWalkForward: botStrategy === 'CHART_AGENT' && metaLabelWalkForward,
      strategy: botStrategy,
      days,
      portfolioSymbolCount,
      onTimeout: (timeoutMs) => {
        const state = useResearchStore.getState();
        if (!state.backtestRunning) return;
        // A deferred job owns its own lifecycle (poller + Jobs tab). Exceeding
        // the client estimate is not a failure — keep watching instead of
        // dropping a run that is still making progress server-side.
        if (isDeferredBacktestStillAlive(state)) {
          toast.warning('Backtest is slower than estimated — still running in the background.', {
            action: {
              label: 'Open Jobs',
              onClick: () => useResearchStore.getState().openBacktestLab('jobs'),
            },
          });
          return;
        }
        stopBacktestJobPolling();
        setBacktestRunning(false);
        setBacktestProgress(null);
        toast.error(
          backtestTimeoutHint({
            reasoning: backtestReasoning,
            metaLabelWalkForward: botStrategy === 'CHART_AGENT' && metaLabelWalkForward,
            strategy: botStrategy,
            portfolioSymbolCount,
            timeoutMs,
          }),
        );
      },
    });

    const request = buildBacktestRequest();
    const { ok, error } = await sendAction(Action.RUN_BACKTEST, withLlmModel(request));

    if (!ok) {
      clearBacktestClientTimeout();
      setBacktestRunning(false);
      setBacktestProgress(null);
      setBacktestLastError(error || 'Backtest request failed', request);
      if (error) toast.error(error);
    }
  };

  const handleCancelBacktest = async () => {
    stopBacktestJobPolling();
    clearBacktestClientTimeout();
    setBacktestRunning(false);
    setBacktestProgress(null);
    const jobId = useResearchStore.getState().backtestJobId;
    if (jobId) {
      upsertBacktestJobSlot(jobId, { status: 'cancelled', running: false });
    }
    const { ok, error } = await sendAction(Action.CANCEL_BACKTEST, jobId ? { job_id: jobId } : {});
    if (!ok) {
      toast.error(error || 'Cancel request could not be delivered — the run may still be going');
    }
  };

  const confirmDeploy = () => {
    if (deployGate?.blocking && !forceDeploy) {
      toast.error(deployGate.block_reason || 'Deploy gate blocked — run backtest or confirm bypass');
      return;
    }
    setDeployOpen(false);
    handleCreateBot();
  };

  const handleCreateBot = (opts = {}) => {
    if (liveBotsBlocked) {
      toast.error('Live bot trading is disabled. Set ALLOW_LIVE_BOTS=true on the server.');
      return;
    }
    if (!botConfig.allocation || botConfig.allocation <= 0) {
      toast.error('Enter a valid max notional cap');
      return;
    }

    // Auto-deploy must never inherit a prior manual force-deploy bypass.
    const force = opts.forceDeploy != null ? Boolean(opts.forceDeploy) : forceDeploy;
    const days = backtestDays;
    const payload = buildDeployPayload({
      strategy: botStrategy,
      symbol: activeSymbol,
      timeframe: botExecutionMode === 'TICK' ? 'tick' : botTimeframe,
      allocation: botConfig.allocation,
      executionMode: botExecutionMode,
      config: botConfig,
      results: useResearchStore.getState().backtestResults,
      snapshot: backtestSnapshot,
      days,
      forceDeploy: force,
    });
    sendAction(Action.BOT_CREATE, payload);
    const pipe = getMlPipeline();
    if (pipe.pipelineId && !pipe.ownedByServer && (pipe.stage === 'READY_TO_DEPLOY' || pipe.stage === 'GATE_CHECK')) {
      if (pipe.stage === 'GATE_CHECK') advancePipeline(pipe.pipelineId);
      advancePipeline(pipe.pipelineId);
      completePipeline(pipe.pipelineId);
      setPipelineDeployPending(null);
      toast.success('Deployed from pipeline');
    }
  };

  // Pipeline: when stage hits BACKTESTING, align config and run holdout BT.
  useEffect(() => {
    if (mlPipeline.stage !== 'BACKTESTING' || !mlPipeline.pipelineId) return;
    if (mlPipeline.ownedByServer) return;
    if (pipelineBtStartedRef.current === mlPipeline.pipelineId) return;
    if (backtestRunning) return;

    const strat = mlPipeline.strategy;
    const sym = mlPipeline.symbol;
    const tf = mlPipeline.timeframe;
    if (!strat || !sym) return;

    pipelineBtStartedRef.current = mlPipeline.pipelineId;
    pipelineBtSawRunningRef.current = null;
    if (strat !== botStrategy) setBotStrategy(strat);
    if (sym !== activeSymbol) setActiveSymbol(sym);
    if (tf && tf !== 'tick' && tf !== botTimeframe) setBotTimeframe(tf);
    setBacktestDays(ML_BACKTEST_RANGE_HOLDOUT);

    if (!botConfig?.allocation || botConfig.allocation <= 0) {
      failPipeline(mlPipeline.pipelineId, {
        stage: 'BACKTESTING',
        error: 'Set a valid max notional cap before backtesting',
      });
      toast.error('Pipeline backtest blocked — set a valid max notional cap');
      return;
    }

    const t = window.setTimeout(() => {
      void handleRunBacktest();
    }, 100);
    return () => window.clearTimeout(t);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional pipeline kickoff
  }, [mlPipeline.stage, mlPipeline.pipelineId, backtestRunning]);

  // Mark that the pipeline-initiated backtest actually started (blocks stale gate/deploy).
  useEffect(() => {
    if (mlPipeline.stage !== 'BACKTESTING' || !mlPipeline.pipelineId) return;
    if (!backtestRunning) return;
    if (pipelineBtStartedRef.current !== mlPipeline.pipelineId) return;
    pipelineBtSawRunningRef.current = mlPipeline.pipelineId;
  }, [mlPipeline.stage, mlPipeline.pipelineId, backtestRunning]);

  // Pipeline: after backtest finishes during BACKTESTING → gate → maybe deploy.
  useEffect(() => {
    if (mlPipeline.stage !== 'BACKTESTING' || !mlPipeline.pipelineId) return;
    if (mlPipeline.ownedByServer) return;
    if (backtestRunning) return;
    if (!backtestResults) return;
    if (pipelineGateDoneRef.current === mlPipeline.pipelineId) return;
    // Require a run that started after this pipeline entered BACKTESTING.
    // Without this, stale store results (often lacking finished_at) auto-gate/deploy.
    if (pipelineBtSawRunningRef.current !== mlPipeline.pipelineId) return;
    // Wait until store results match this pipeline's symbol/strategy. Clearing
    // backtestRunning before async result apply previously gated a prior run.
    const pipeSym = mlPipeline.symbol || activeSymbol;
    const pipeStrat = mlPipeline.strategy || botStrategy;
    if (!backtestResultsMatchTarget(backtestResults, {
      symbol: pipeSym,
      strategy: pipeStrat,
    })) {
      return;
    }

    pipelineGateDoneRef.current = mlPipeline.pipelineId;
    setPipelineBacktestResult(mlPipeline.pipelineId, backtestResults);
    advancePipeline(mlPipeline.pipelineId, { result: backtestResults });

    const pipeId = mlPipeline.pipelineId;
    const outcome = evaluateAndMaybeDeploy({
      backtestResults,
      config: botConfig,
      autoDeployMode: mlPipeline.autoDeployMode || 'paper',
      executionMode,
      terminalMode,
      symbol: pipeSym,
      strategy: pipeStrat,
      timeframe: mlPipeline.timeframe || botTimeframe,
      days: backtestDays,
      snapshot: backtestSnapshot,
      onGatePassed: (gate) => {
        setPipelineGateResult(pipeId, gate);
        setDeployGate(gate);
        advancePipeline(pipeId, { result: gate });
      },
      onGateFailed: (gate) => {
        setPipelineGateResult(pipeId, gate);
        setDeployGate(gate);
        failPipeline(pipeId, { stage: 'GATE_CHECK', error: gate.block_reason || 'Gate blocked' });
        toast.error(gate.block_reason || 'Pipeline deploy gate blocked');
      },
      onApprovalNeeded: (gate) => {
        setPipelineGateResult(pipeId, gate);
        setDeployGate(gate);
        setPipelineDeployPending({ pipelineId: pipeId, gate });
        toast.message('Pipeline ready — approve deploy to continue', {
          action: {
            label: 'Deploy Now',
            onClick: () => {
              setForceDeploy(false);
              setDeployOpen(true);
            },
          },
          duration: 20_000,
        });
      },
      onAutoDeploy: (gate) => {
        setPipelineGateResult(pipeId, gate);
        setDeployGate(gate);
        handleCreateBot({ forceDeploy: false });
      },
    });

    if (outcome.deployed) {
      toast.success(outcome.reason);
    } else if (outcome.reason && mlPipeline.autoDeployMode === 'paper' && !outcome.gateResult?.blocking) {
      toast.message(outcome.reason, {
        action: {
          label: 'Deploy',
          onClick: () => setDeployOpen(true),
        },
      });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mlPipeline.stage, mlPipeline.pipelineId, backtestRunning, backtestResults]);

  // Drop approval-pending state tied to a pipeline that ended or was replaced.
  useEffect(() => {
    if (!pipelineDeployPending) return;
    if (pipelineDeployPending.pipelineId !== mlPipeline.pipelineId) {
      setPipelineDeployPending(null);
      return;
    }
    if (mlPipeline.stage !== 'READY_TO_DEPLOY') setPipelineDeployPending(null);
  }, [pipelineDeployPending, mlPipeline.pipelineId, mlPipeline.stage]);

  const filteredTemplates = useMemo(() => {
    const list = strategyTemplates.filter(
      (t) => (t.execution_mode || 'BAR_CLOSE') === botExecutionMode
        && (allowCustomStrategies || (t.strategy !== 'CUSTOM' && !t.custom))
        && (botCategoryTab === 'agentic' ? t.category === 'agent'
            : botCategoryTab === 'ml' ? t.category === 'ml'
            : t.category !== 'agent' && t.category !== 'ml'),
    );
    if (botCategoryTab !== 'normal') return list;
    // Bar/TA first; tick strategies last so Normal isn't dominated by TICK_* cards.
    return [...list].sort((a, b) => {
      const aTick = a.category === 'tick' || a.execution_mode === 'TICK' ? 1 : 0;
      const bTick = b.category === 'tick' || b.execution_mode === 'TICK' ? 1 : 0;
      if (aTick !== bTick) return aTick - bTick;
      return String(a.name || '').localeCompare(String(b.name || ''));
    });
  }, [strategyTemplates, botExecutionMode, allowCustomStrategies, botCategoryTab]);

  const selectTemplate = (template) => {
    setBotStrategy(template.strategy);
    if (template.execution_mode) {
      setBotExecutionMode(template.execution_mode);
    }
    replaceBotConfig({
      ...pickDeployConfig(template.strategy, template.config || {}),
      allocation: template.allocation ?? 1000,
    });
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
                {runningCount} running{pausedCount ? ` · ${pausedCount} paused` : ''} · {activeBots.length} bot{activeBots.length === 1 ? '' : 's'} · {activeSymbol}
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
            {safeModeActive && (
              <Badge variant="destructive" className="px-2 py-0.5 text-xs font-extrabold tracking-wider">
                SAFE MODE
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
      {safeModeActive && (
        <Alert
          variant="destructive"
          className="algo-tab__banner border-destructive/40 bg-destructive/10 xl:col-span-3"
        >
          <ShieldAlert aria-hidden />
          <AlertDescription className="flex flex-wrap items-center gap-2 text-xs leading-relaxed">
            <span>
              <strong>Safe mode active</strong>
              {' — '}
              {safeMode?.reason || 'Unclean shutdown or unresolved fills detected.'}
              {' '}
              Bot evaluation is blocked (RUNNING bots will not trade) until you confirm.
            </span>
            <Button
              variant="outline"
              size="sm"
              className="h-7 shrink-0 border-destructive/50 text-xs"
              onClick={handleConfirmSafeMode}
            >
              Confirm &amp; clear safe mode
            </Button>
          </AlertDescription>
        </Alert>
      )}

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
            <strong>
              {massiveLive
                ? 'Paper execution on Massive data'
                : alpacaLive && paperExecution
                  ? 'Paper execution on Alpaca data'
                  : alpacaLive
                    ? 'Live bots on Alpaca'
                    : 'Live bots enabled'}
            </strong>
            {massiveLive
              ? ' — instant fills at live prices (no broker routing). 1m BAR_CLOSE via feed bar hooks; higher timeframes via native REST; TICK bots on price updates.'
              : alpacaLive && paperExecution
                ? ' — app SimulatedOMS fills on live Alpaca quotes (ALPACA_OMS_ENABLED=false). Set true + restart for broker routing.'
                : alpacaLive
                  ? ' — real Alpaca OMS (paper/live URL). 1m BAR_CLOSE via feed hooks; higher TFs via Alpaca REST; TICK bots on price updates.'
                  : ` on ${terminalMode}`}
            {distributed ? ` · role=${terminalRole} (distributed via Redis)` : ''}.
            {!nativeHtLive && (
              <>
                {' '}Indicator warm-up uses archive when buffer &lt; {botMinCandles} bars.
                Signals fire on closed {formatBarTimeframeLabel(botTimeframe)} bars — do not resend ambiguous orders.
              </>
            )}
            {nativeHtLive && (
              <>
                {' '}Indicator warm-up uses {alpacaLive ? 'Alpaca' : 'Massive'} REST when the chart buffer is shallow.
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

      <div className="algo-tab__main">
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
                  const nextTab = mode === 'TICK' ? 'normal' : botCategoryTab;
                  if (mode === 'TICK' && botCategoryTab !== 'normal') {
                    setBotCategoryTab('normal');
                  }
                  const first = strategyTemplates.find(
                    (t) => (t.execution_mode || 'BAR_CLOSE') === mode
                      && (allowCustomStrategies || (t.strategy !== 'CUSTOM' && !t.custom))
                      && (nextTab === 'agentic' ? t.category === 'agent'
                          : nextTab === 'ml' ? t.category === 'ml'
                          : t.category !== 'agent' && t.category !== 'ml'),
                  ) || strategyTemplates.find(
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
              <div className="flex items-center justify-between mb-2">
                <Label className="algo-field-label mb-0">Strategy Templates</Label>
                <div className="flex bg-slate-950/50 rounded-md p-0.5 border border-slate-800/60">
                  <button
                    className={`px-3 py-1 text-xs font-medium rounded-sm transition-colors ${botCategoryTab === 'normal' ? 'bg-slate-800 text-slate-100 shadow-sm' : 'text-slate-400 hover:text-slate-200'}`}
                    onClick={() => {
                      setBotCategoryTab('normal');
                      const first = strategyTemplates.find(
                        (t) => (t.execution_mode || 'BAR_CLOSE') === botExecutionMode
                          && (allowCustomStrategies || (t.strategy !== 'CUSTOM' && !t.custom))
                          && t.category !== 'agent' && t.category !== 'ml',
                      );
                      if (first) selectTemplate(first);
                    }}
                    type="button"
                  >
                    Normal
                  </button>
                  <button
                    className={`px-3 py-1 text-xs font-medium rounded-sm transition-colors flex items-center gap-1.5 ${botCategoryTab === 'ml' ? 'bg-slate-800 text-slate-100 shadow-sm' : 'text-slate-400 hover:text-slate-200'}`}
                    onClick={() => {
                      setBotCategoryTab('ml');
                      if (botExecutionMode !== 'BAR_CLOSE') setBotExecutionMode('BAR_CLOSE');
                      const mode = 'BAR_CLOSE';
                      const first = strategyTemplates.find(
                        (t) => (t.execution_mode || 'BAR_CLOSE') === mode
                          && t.category === 'ml'
                          && (allowCustomStrategies || (t.strategy !== 'CUSTOM' && !t.custom)),
                      );
                      if (first) selectTemplate(first);
                    }}
                    type="button"
                  >
                    <BrainCircuit size={12} />
                    ML / AI
                  </button>
                  <button
                    className={`px-3 py-1 text-xs font-medium rounded-sm transition-colors flex items-center gap-1.5 ${botCategoryTab === 'agentic' ? 'bg-slate-800 text-slate-100 shadow-sm' : 'text-slate-400 hover:text-slate-200'}`}
                    onClick={() => {
                      setBotCategoryTab('agentic');
                      if (botExecutionMode !== 'BAR_CLOSE') setBotExecutionMode('BAR_CLOSE');
                      const mode = 'BAR_CLOSE';
                      const first = strategyTemplates.find(
                        (t) => (t.execution_mode || 'BAR_CLOSE') === mode
                          && t.category === 'agent'
                          && (allowCustomStrategies || (t.strategy !== 'CUSTOM' && !t.custom)),
                      );
                      if (first) selectTemplate(first);
                    }}
                    type="button"
                  >
                    <Bot size={12} />
                    Agentic
                  </button>
                </div>
              </div>
              <div className="algo-template-grid">
                {filteredTemplates.length === 0 ? (
                  <p className="algo-field-hint col-span-full text-muted-foreground py-3">
                    {botExecutionMode === 'TICK' && (botCategoryTab === 'ml' || botCategoryTab === 'agentic')
                      ? 'ML and Agentic strategies require Bar Close execution — switch mode or open the Normal tab.'
                      : 'No templates in this category for the current execution mode.'}
                  </p>
                ) : (
                  filteredTemplates.map(t => (
                    <StrategyTemplateCard
                      key={t.id}
                      template={t}
                      active={botStrategy === t.strategy}
                      onSelect={selectTemplate}
                    />
                  ))
                )}
              </div>
            </div>

            {isMlStrategy(botStrategy) && (
              <div className="algo-deploy-field">
                <MlModelVersionSelect
                  strategy={botStrategy}
                  symbol={activeSymbol}
                  timeframe={botExecutionMode === 'TICK' ? '1m' : botTimeframe}
                  value={botConfig?.model_version || ''}
                  onChange={(v) => updateBotConfig({ model_version: v || undefined })}
                />
              </div>
            )}

            <div className="algo-deploy-field">
              <Label className="algo-field-label">Trade direction</Label>
              <Select
                value={botConfig?.direction_mode ?? 'LONG_ONLY'}
                onValueChange={(mode) => updateBotConfig({ direction_mode: mode })}
              >
                <SelectTrigger className="h-8 w-full text-xs" aria-label="Trade direction">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent position="popper">
                  {DIRECTION_MODE_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value} className="text-xs">
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span className="algo-field-hint">
                {isMlStrategy(botStrategy) || botStrategy === 'CHART_AGENT' || botStrategy === 'ABSORPTION_AGENT' || botStrategy === 'REGIME_STRATEGY_AGENT'
                  ? 'Live risk gate: LONG_ONLY blocks short entries; BOTH allows BUY and SELL signal strategies to open both sides.'
                  : 'Live risk gate: LONG_ONLY blocks short entries; BOTH allows long and short entries when the strategy emits them.'}
              </span>
            </div>

            {isMlStrategy(botStrategy) && (
              <p className="algo-field-hint text-muted-foreground -mt-1 mb-2">
                Train and validate models in{' '}
                <button
                  type="button"
                  className="text-primary underline-offset-2 hover:underline"
                  onClick={() => openModelTrainingDock()}
                >
                  ML Training
                </button>
                {' '}— deploy pins a model version above; it does not retrain.
              </p>
            )}

            {isMlStrategy(botStrategy) && (
              <div className="algo-deploy-field space-y-2">
                <Label className="algo-field-label">
                  {botStrategy === 'VAE_REGIME_DETECTOR'
                    ? 'VAE regime thresholds'
                    : botStrategy === 'TCN_MULTI_HORIZON'
                      ? 'TCN forecast gates'
                      : botStrategy === 'GNN_CROSS_ASSET'
                        ? 'GNN signal gate'
                        : 'ML signal gate'}
                </Label>
                {botStrategy !== 'VAE_REGIME_DETECTOR' && (() => {
                  const bounds = confidenceRangeForStrategy(botStrategy);
                  const raw = parseFloat(botConfig?.min_confidence);
                  const current = Number.isFinite(raw) ? raw : bounds.defaultValue;
                  const valueLabel = bounds.max <= 0.1
                    ? current.toFixed(4)
                    : `${Math.round(current * 100)}%`;
                  return (
                    <div>
                      <div className="mb-1 flex justify-between text-[0.62rem] text-muted-foreground">
                        <span>
                          {botStrategy === 'TCN_MULTI_HORIZON'
                            ? 'Min avg |return| (confidence)'
                            : botStrategy === 'RL_PPO_AGENT'
                              ? 'Min policy confidence'
                              : 'Min confidence'}
                        </span>
                        <span className="num-mono">{valueLabel}</span>
                      </div>
                      <input
                        type="range"
                        min={bounds.min}
                        max={bounds.max}
                        step={bounds.step}
                        value={current}
                        onChange={(e) => updateBotConfig({
                          min_confidence: parseFloat(e.target.value),
                        })}
                        className="w-full accent-primary"
                        aria-label="Minimum signal confidence"
                      />
                    </div>
                  );
                })()}
                {botStrategy === 'TCN_MULTI_HORIZON' && (
                  <div>
                    <Label className="text-[0.62rem] text-muted-foreground">
                      Min return (decimal, e.g. 0.002 = 0.2%)
                    </Label>
                    <InputGroup className="mt-1 h-8">
                      <InputGroupInput
                        type="number"
                        min={0}
                        step="0.0005"
                        className="text-xs num-mono"
                        value={botConfig?.min_return ?? 0.002}
                        onChange={(e) => updateBotConfig({
                          min_return: parseFloat(e.target.value) || 0,
                        })}
                        aria-label="Minimum forecast return (decimal)"
                      />
                    </InputGroup>
                    <span className="algo-field-hint">
                      Horizon-agreement magnitude gate — separate from the avg-|return| confidence slider above.
                    </span>
                  </div>
                )}
                {botStrategy === 'VAE_REGIME_DETECTOR' && (
                  <>
                    <div>
                      <Label className="text-[0.62rem] text-muted-foreground">Anomaly threshold</Label>
                      <InputGroup className="mt-1 h-8">
                        <InputGroupInput
                          type="number"
                          min={0}
                          step="0.1"
                          className="text-xs num-mono"
                          value={botConfig?.anomaly_threshold ?? 2}
                          onChange={(e) => updateBotConfig({
                            anomaly_threshold: parseFloat(e.target.value) || 0,
                          })}
                          aria-label="VAE anomaly threshold"
                        />
                      </InputGroup>
                    </div>
                    <div>
                      <Label className="text-[0.62rem] text-muted-foreground">Suppress threshold</Label>
                      <InputGroup className="mt-1 h-8">
                        <InputGroupInput
                          type="number"
                          min={0}
                          step="0.1"
                          className="text-xs num-mono"
                          value={botConfig?.suppress_threshold ?? 3}
                          onChange={(e) => updateBotConfig({
                            suppress_threshold: parseFloat(e.target.value) || 0,
                          })}
                          aria-label="VAE suppress threshold"
                        />
                      </InputGroup>
                    </div>
                    <span className="algo-field-hint">
                      Reconstruction-error levels: anomaly flags a regime shift; suppress blocks entries above this.
                    </span>
                  </>
                )}
                {botStrategy === 'GNN_CROSS_ASSET' && (
                  <>
                    <div>
                      <Label className="text-[0.62rem] text-muted-foreground">Min correlation</Label>
                      <InputGroup className="mt-1 h-8">
                        <InputGroupInput
                          type="number"
                          min={0}
                          max={1}
                          step="0.05"
                          className="text-xs num-mono"
                          value={botConfig?.min_corr ?? 0.5}
                          onChange={(e) => updateBotConfig({
                            min_corr: parseFloat(e.target.value) || 0,
                          })}
                          aria-label="Minimum cross-asset correlation"
                        />
                      </InputGroup>
                    </div>
                    <div>
                      <Label className="text-[0.62rem] text-muted-foreground">Basket ID</Label>
                      <InputGroup className="mt-1 h-8">
                        <InputGroupInput
                          type="text"
                          className="text-xs num-mono"
                          placeholder="e.g. crypto_majors"
                          value={botConfig?.basket_id ?? ''}
                          onChange={(e) => updateBotConfig({
                            basket_id: e.target.value.trim() || undefined,
                          })}
                          aria-label="Correlated asset basket ID"
                        />
                      </InputGroup>
                      <span className="algo-field-hint">
                        Optional basket key for the cross-asset graph (defaults from training metadata when empty).
                      </span>
                    </div>
                  </>
                )}
              </div>
            )}

            {botStrategy === 'RL_PPO_AGENT' ? (
              <>
                <div className="algo-deploy-field">
                  <Label className="algo-field-label">Risk per trade</Label>
                  <InputGroup className="h-8">
                    <InputGroupInput
                      type="number"
                      step="1"
                      min="15"
                      max="25"
                      value={botConfig?.risk_per_trade_usd ?? 20}
                      onChange={e => updateBotConfig({
                        risk_per_trade_usd: Math.max(15, Math.min(25, parseFloat(e.target.value) || 20)),
                      })}
                      className="text-xs"
                      aria-label="Risk dollars per trade"
                    />
                    <InputGroupAddon align="inline-end">
                      <InputGroupText className="text-xs">USD</InputGroupText>
                    </InputGroupAddon>
                  </InputGroup>
                  <span className="algo-field-hint">
                    Dollar risk at the ATR×1.5 stop. Clamped to $15–25 — not 2% of the $3k book.
                  </span>
                </div>
                <div className="algo-deploy-field">
                  <Label className="algo-field-label">ATR stop / TP</Label>
                  <div className="text-xs text-muted-foreground">
                    Stop ATR×{botConfig?.atr_stop_mult ?? 1.5} · target {botConfig?.take_profit_r ?? 1.5}R
                    · chandelier trail. Percent 2%/3% stops are disabled for RL.
                  </div>
                </div>
              </>
            ) : (
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
            )}

            <div className="algo-deploy-field">
              <Label className="algo-field-label">Take Profit</Label>
              <Select
                value={botConfig?.tp_mode ?? (botStrategy === 'RL_PPO_AGENT' ? 'strategy' : 'percent')}
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
                  <SelectItem value="percent" className="text-xs" disabled={botStrategy === 'RL_PPO_AGENT'}>
                    Fixed % from entry
                  </SelectItem>
                  <SelectItem
                    value="strategy"
                    className="text-xs"
                    disabled={botStrategy !== 'BRS_SCALPING' && botStrategy !== 'RL_PPO_AGENT'}
                  >
                    {botStrategy === 'RL_PPO_AGENT' ? 'ATR 1.5R target' : 'Strategy target (BRS mid-band)'}
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
                Hard limit on position size per trade. {botStrategy === 'RL_PPO_AGENT'
                  ? 'RL risks $15–25 per trade at the ATR×1.5 stop.'
                  : 'Risk is sized at 1% of account balance using ATR-based stops.'}
                {botExecutionMode === 'TICK'
                  ? ' Tick strategies evaluate on each trade print (not closed bars).'
                  : ` Signals evaluate on closed ${formatBarTimeframeLabel(botTimeframe)} bars.`}
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

            {botStrategy === 'REGIME_STRATEGY_AGENT' && (
              <div className="algo-deploy-field space-y-2">
                <Label className="algo-field-label">Regime Strategy Agent Settings</Label>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <Label className="text-[0.62rem] text-muted-foreground">ATR elevated ratio</Label>
                    <InputGroup className="mt-1 h-8">
                      <InputGroupInput
                        type="number"
                        min={1}
                        max={5}
                        step={0.1}
                        className="text-xs num-mono"
                        value={botConfig?.atr_ratio_elevated ?? 1.5}
                        onChange={(e) => updateBotConfig({
                          atr_ratio_elevated: parseFloat(e.target.value) || 1.5,
                        })}
                        aria-label="ATR elevated ratio"
                      />
                    </InputGroup>
                  </div>
                  <div>
                    <Label className="text-[0.62rem] text-muted-foreground">ADX trend</Label>
                    <InputGroup className="mt-1 h-8">
                      <InputGroupInput
                        type="number"
                        min={5}
                        max={60}
                        step={1}
                        className="text-xs num-mono"
                        value={botConfig?.adx_trend ?? 25}
                        onChange={(e) => updateBotConfig({
                          adx_trend: parseFloat(e.target.value) || 25,
                        })}
                        aria-label="ADX trend threshold"
                      />
                    </InputGroup>
                  </div>
                  <div>
                    <Label className="text-[0.62rem] text-muted-foreground">Hysteresis bars</Label>
                    <InputGroup className="mt-1 h-8">
                      <InputGroupInput
                        type="number"
                        min={1}
                        max={30}
                        step={1}
                        className="text-xs num-mono"
                        value={botConfig?.regime_hysteresis_bars ?? 3}
                        onChange={(e) => updateBotConfig({
                          regime_hysteresis_bars: parseInt(e.target.value, 10) || 3,
                        })}
                        aria-label="Regime hysteresis bars"
                      />
                    </InputGroup>
                  </div>
                  <div>
                    <Label className="text-[0.62rem] text-muted-foreground">Min hold bars</Label>
                    <InputGroup className="mt-1 h-8">
                      <InputGroupInput
                        type="number"
                        min={0}
                        max={200}
                        step={1}
                        className="text-xs num-mono"
                        value={botConfig?.regime_min_hold_bars ?? 15}
                        onChange={(e) => updateBotConfig({
                          regime_min_hold_bars: parseInt(e.target.value, 10) || 0,
                        })}
                        aria-label="Regime min hold bars"
                      />
                    </InputGroup>
                  </div>
                </div>
                <span className="algo-field-hint">
                  elevated_vol→VWAP · trending→Supertrend · ranging→BRS. Open positions keep original SL/TP on switch.
                </span>
              </div>
            )}

            {botStrategy === 'ABSORPTION_AGENT' && (
              <div className="algo-deploy-field space-y-2">
                <Label className="algo-field-label">Absorption Agent Settings</Label>
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
                    onChange={(e) => updateBotConfig({ min_confidence: parseFloat(e.target.value) })}
                    className="w-full accent-primary"
                    aria-label="Absorption minimum confidence"
                  />
                </div>
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
                      aria-label="Absorption minimum score"
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
                    <SelectTrigger className="mt-1 h-8 w-full text-xs" aria-label="Absorption HTF confirmation">
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
                    checked={Boolean(botConfig?.calibration_gate_enabled)}
                    onChange={(e) => updateBotConfig({ calibration_gate_enabled: e.target.checked })}
                    className="accent-primary"
                  />
                  Calibration gate (Wilson win-rate buckets)
                </label>
                {!botConfig?.calibration_gate_enabled
                  && ['gbm', 'hybrid'].includes(String(botConfig?.meta_label_model_mode || '').toLowerCase()) && (
                  <p className="text-[0.65rem] text-amber-600 dark:text-amber-400">
                    Meta-label mode is {botConfig.meta_label_model_mode} but ignored until Calibration gate is enabled.
                  </p>
                )}
                {Boolean(botConfig?.calibration_gate_enabled) && (
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <Label className="text-[0.62rem] text-muted-foreground">Min samples</Label>
                      <InputGroup className="mt-1 h-8">
                        <InputGroupInput
                          type="number"
                          min={1}
                          step={1}
                          className="text-xs num-mono"
                          value={botConfig?.calibration_min_samples ?? 5}
                          onChange={(e) => updateBotConfig({
                            calibration_min_samples: parseInt(e.target.value, 10) || 0,
                          })}
                          aria-label="Calibration min samples"
                        />
                      </InputGroup>
                    </div>
                    <div>
                      <Label className="text-[0.62rem] text-muted-foreground">Min Wilson</Label>
                      <InputGroup className="mt-1 h-8">
                        <InputGroupInput
                          type="number"
                          min={0}
                          max={1}
                          step={0.05}
                          className="text-xs num-mono"
                          value={botConfig?.calibration_min_wilson ?? 0.45}
                          onChange={(e) => updateBotConfig({
                            calibration_min_wilson: parseFloat(e.target.value) || 0,
                          })}
                          aria-label="Calibration min Wilson bound"
                        />
                      </InputGroup>
                    </div>
                  </div>
                )}
              </div>
            )}

            <BacktestWorkflowPresets
              activePreset={activeWorkflowPreset}
              botStrategy={botStrategy}
              disabled={backtestRunning}
              onSelect={handleWorkflowPreset}
              className="mb-2"
            />

            <BacktestStaleBanner
              snapshot={backtestSnapshot}
              symbol={activeSymbol}
              strategy={botStrategy}
              days={backtestDays}
              timeframe={botExecutionMode === 'TICK' ? 'tick' : botTimeframe}
              config={botConfig}
              simMode={backtestSimMode}
              onRerun={handleRunBacktest}
              className="algo-backtest-stale-banner py-2 mb-2"
            />

            <BacktestErrorRecovery
              error={backtestLastError}
              lastRequest={backtestLastRequest}
              onRetry={handleRetryBacktest}
              onDismiss={clearBacktestLastError}
              className="mb-2"
            />

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
                {nativeHtLive
                  ? `Shared with deploy timeframe — backtest uses archive; live ${alpacaLive ? 'Alpaca' : 'Massive'} bots use native HT REST where available.`
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
                disabled={metaLabelWalkForward}
                onChange={(e) => {
                  setBacktestOos(e.target.checked);
                  if (e.target.checked) setMetaLabelWalkForward(false);
                }}
              />
              Hold-out test (last 30%) — test on last 30% of range only
            </label>

            {botStrategy === 'CHART_AGENT' && (
              <label
                className={cn(
                  'flex items-center gap-2 text-[0.62rem] cursor-pointer',
                  backtestOos ? 'text-muted-foreground/50' : 'text-muted-foreground',
                )}
                title={backtestOos ? 'Disable hold-out test first — walk-forward needs the full candle range' : undefined}
              >
                <input
                  type="checkbox"
                  className="size-3.5 accent-primary"
                  checked={metaLabelWalkForward}
                  disabled={backtestOos}
                  onChange={(e) => {
                    setMetaLabelWalkForward(e.target.checked);
                    if (e.target.checked) setBacktestOos(false);
                  }}
                />
                Meta-label walk-forward — train GBM on in-sample, compare OOS vs baseline
              </label>
            )}

            <PortfolioBacktestPicker
              enabled={portfolioBacktest}
              onEnabledChange={setPortfolioBacktest}
              selectedSymbols={portfolioSymbols}
              onSelectedChange={setPortfolioSymbols}
              watchlist={symbolsList}
              activeSymbol={activeSymbol}
              oos={backtestOos}
              walkForward={botStrategy === 'CHART_AGENT' && metaLabelWalkForward}
              runEstimate={portfolioBacktest ? runEstimate : null}
            />

            <label className="flex items-center gap-2 text-[0.62rem] text-muted-foreground cursor-pointer">
              <input
                type="checkbox"
                className="size-3.5 accent-primary"
                checked={backtestLiveParity}
                disabled={backtestSimMode === 'research'}
                onChange={(e) => setBacktestLiveParity(e.target.checked)}
              />
              Live parity — simulate HTF confirm + filter_strategy gates (matches deployed bot)
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
                    Account snapshot{quoteCashForSymbol > 0
                      ? ` ($${quoteCashForSymbol.toLocaleString()} ${String(activeSymbol || '').includes('USDT') ? 'USDT' : 'USD'})`
                      : ''}
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
                  Research mode allows shorts without live risk gates — results may not match a deployed bot.
                  Use live-aligned + matching trade direction before deploy.
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
                  {mlStrategySelected ? (
                    <>
                      <SelectItem value={ML_BACKTEST_RANGE_HOLDOUT} className="text-xs">
                        {`Locked holdout (${mlHoldoutDays}d from train calendar)`}
                      </SelectItem>
                      {ML_FREE_RANGE_DAYS.map((d) => (
                        <SelectItem key={d} value={d} className="text-xs">
                          {d === '14'
                            ? '14 days (free window)'
                            : d === '180' || d === '365'
                              ? `${d} days (broker REST if needed)`
                              : `${d} days`}
                        </SelectItem>
                      ))}
                    </>
                  ) : (
                    TA_BACKTEST_RANGE_DAYS.map((d) => (
                      <SelectItem key={d} value={d} className="text-xs">
                        {d === '7'
                          ? '7 days (local buffer + archive)'
                          : d === '14'
                            ? '14 days'
                            : d === '180' || d === '365'
                              ? `${d} days (broker REST if needed)`
                              : `${d} days`}
                      </SelectItem>
                    ))
                  )}
                </SelectContent>
              </Select>
              <span className="algo-field-hint">
                {mlStrategySelected
                  ? (mlBacktestRangeHint({
                    isMl: true,
                    backtestDays,
                    holdoutDays: mlHoldoutDays,
                  }) || 'ML holdout-aware range')
                  : botTimeframe === '1m'
                    ? `Long ranges fill older 1m gaps from ${alpacaLive ? 'Alpaca' : 'Massive/broker'} REST when local archive is short.`
                    : `Higher TF long ranges use ${alpacaLive ? 'Alpaca' : 'Massive'} native bars (not limited to local 1m retention).`}
              </span>
            </div>

            {backtestRunning && <BacktestProgressBar compact />}

            {backtestRunning && backtestJobId && (
              <p className="text-[0.58rem] text-muted-foreground mb-2">
                Background job {String(backtestJobId).slice(0, 8)} — safe to switch tabs;
                open Jobs for status. Toast on completion.
              </p>
            )}

            {!backtestRunning && (Number(systemStats?.ml_jobs_queued) > 0 || Number(systemStats?.ml_jobs_active) > 0) && (
              <p className="text-[0.58rem] text-amber-600/90 dark:text-amber-400/90 mb-2">
                ML Lab busy: {Number(systemStats?.ml_jobs_active) || 0} running
                {Number(systemStats?.ml_jobs_queued) > 0
                  ? ` · ${Number(systemStats.ml_jobs_queued)} queued`
                  : ''}
                {' '}— heavy backtests may wait for a free worker.
              </p>
            )}

            {dockPreview ? (
              <div className="algo-backtest-dock-summary mb-2 rounded border border-border/50 p-2 text-xs">
                <p className="font-medium mb-1">Full report open in Lab</p>
                <p className="text-muted-foreground num-mono mb-2">
                  ${Number(dockPreview.total_pnl ?? 0).toFixed(2)} · {dockPreview.trade_count ?? 0} trades
                </p>
                <Button type="button" variant="outline" size="xs" onClick={() => openBacktestLab('results')}>
                  Focus Lab
                </Button>
              </div>
            ) : backtestResults ? (
              <>
                {resultContextMismatch && (
                  <Alert variant="default" className="algo-backtest-stale-banner py-2 mb-2">
                    <AlertTriangle data-icon="inline-start" className="size-3.5" />
                    <AlertDescription className="text-xs space-y-2">
                      <p className="m-0">
                        Mini results below are for{' '}
                        <strong className="num-mono">{resultContextMismatch.identity.symbol}</strong>
                        {resultContextMismatch.identity.strategy
                          ? <> · {resultContextMismatch.identity.strategy}</>
                          : null}
                        {' '}— not the current selection{' '}
                        <strong className="num-mono">{activeSymbol}</strong>
                        {botStrategy ? <> · {botStrategy}</> : null}.
                        Parameters and metrics were not re-run for the new ticker.
                      </p>
                      <div className="flex flex-wrap gap-1.5">
                        {resultContextMismatch.identity.symbol && (
                          <Button
                            type="button"
                            variant="outline"
                            size="xs"
                            className="h-6"
                            onClick={() => setActiveSymbol(resultContextMismatch.identity.symbol)}
                          >
                            Switch to {resultContextMismatch.identity.symbol}
                          </Button>
                        )}
                        <Button
                          type="button"
                          variant="outline"
                          size="xs"
                          className="h-6"
                          onClick={handleRunBacktest}
                          disabled={backtestRunning}
                        >
                          Backtest {activeSymbol}
                        </Button>
                      </div>
                    </AlertDescription>
                  </Alert>
                )}
                <BacktestResultsPanel
                  results={backtestResults}
                  backtestDays={backtestIdentity.days ?? backtestDays}
                  backtestTimeframe={backtestIdentity.timeframe || botTimeframe}
                  symbol={backtestIdentity.symbol || activeSymbol}
                  strategy={backtestIdentity.strategy || botStrategy}
                  recentRuns={backtestRuns}
                  snapshot={backtestSnapshot}
                  oosPct={backtestOos ? 30 : null}
                  reasoningPending={backtestReasoning && backtestRunning}
                  showReasoningSection={agentLlmAvailable}
                  advisorBotId={selectedBotId}
                  agentLlmAvailable={agentLlmAvailable}
                />
              </>
            ) : null}
          </div>
        </div>
        <footer className="algo-tab__panel-footer algo-deploy-actions">
          <div className="algo-deploy-actions__rail">
            <Button
              variant="ghost"
              size="sm"
              className="algo-deploy-actions__btn algo-deploy-actions__btn--backtest"
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
              className="algo-deploy-actions__btn algo-deploy-actions__btn--optimize"
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

      <Dialog open={deployOpen} onOpenChange={(open) => {
        setDeployOpen(open);
        if (!open) setForceDeploy(false);
      }}>
        <DialogContent className="algo-dialog sm:max-w-md" overlayClassName="admin-panel-overlay">
          <DialogHeader>
            <DialogTitle>Deploy trading bot</DialogTitle>
            <DialogDescription className="text-xs leading-relaxed">
              Forward-test workflow: validate backtest OOS before allocating capital.
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
            <div>
              <span className="text-muted-foreground">Trade direction:</span>{' '}
              <strong>{formatDirectionModeLabel(botConfig?.direction_mode)}</strong>
            </div>
            <div><span className="text-muted-foreground">Timeframe:</span> <strong>{deployTimeframeSummary(botExecutionMode, botTimeframe)}</strong></div>
          </div>
          <DeployGatePanel
            results={backtestResults}
            symbol={activeSymbol}
            strategy={botStrategy}
            timeframe={botExecutionMode === 'TICK' ? 'tick' : botTimeframe}
            days={backtestDays}
            config={botConfig}
            backtestConfig={backtestLastRequest?.config}
            snapshot={backtestSnapshot}
            onGateChange={setDeployGate}
            forceDeploy={forceDeploy}
            onForceDeployChange={setForceDeploy}
          />
          <DialogFooter showCloseButton={false}>
            <Button variant="outline" size="sm" onClick={() => setDeployOpen(false)}>Cancel</Button>
            {(pipelineDeployPending || mlPipeline.stage === 'READY_TO_DEPLOY') && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  setForceDeploy(false);
                  confirmDeploy();
                }}
                disabled={liveBotsBlocked || (deployGate?.blocking && !forceDeploy)}
                title="Deploy using pipeline metadata"
              >
                Deploy from Pipeline
              </Button>
            )}
            <Button
              variant="buy"
              size="sm"
              onClick={confirmDeploy}
              disabled={liveBotsBlocked || (deployGate?.blocking && !forceDeploy)}
            >
              Confirm deploy
            </Button>
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

      <div className="algo-tab__stack">
      <section className="algo-tab__panel algo-tab__panel--bots">
        <header className="algo-tab__panel-header">
          <div className="algo-tab__panel-heading">
            <div className="algo-tab__panel-title">
              <Cpu size={13} className={runningCount > 0 ? 'text-trading-up' : 'text-muted-foreground'} aria-hidden />
              Active Bots
              <Badge variant={runningCount > 0 ? 'buy' : 'secondary'}>{runningCount} running</Badge>
              {pausedCount > 0 && (
                <Badge variant="outline">{pausedCount} paused</Badge>
              )}
              {safeModeActive && (
                <Badge variant="destructive" className="text-[10px] font-bold tracking-wide">
                  SAFE MODE
                </Badge>
              )}
            </div>
            <span className="algo-tab__panel-subtitle">
              {safeModeActive
                ? 'Evaluation blocked until safe mode is cleared'
                : pausedCount > 0 && runningCount === 0
                  ? 'Paused — not evaluating. Resume to continue.'
                  : 'Pause · resume · stop · details'}
            </span>
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
                activeBots.map((bot) => (
                  <ActiveBotRow
                    key={bot.id}
                    bot={bot}
                    ownedPos={getBotOwnedPositionView(bot.id, bot.symbol, positions)}
                    selected={selectedBotId === bot.id}
                    agentInsights={agentInsights}
                    safeModeActive={safeModeActive}
                    onSelect={selectBot}
                    onPause={handlePauseBot}
                    onResume={handleResumeBot}
                    onStop={handleStopBot}
                    onSetStopLoss={handleSetBotStopLoss}
                    onSetTakeProfit={handleSetBotTakeProfit}
                  />
                ))
              )}
            </DataTableBody>
          </DataTableRoot>
        </ScrollTablePanel>
      </section>

      <BotReasoningPanel botId={selectedBotId} />
      </div>

      <div className="algo-tab__rail">
      <AgentActionsPanel />

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
              filteredBotLogs.map((log, idx) => {
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
                    key={log.id ?? `log-${idx}-${display.slice(0, 24)}`}
                    data-scroll-anchor-id={log.id ?? `log-${idx}`}
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
              })
            )}
          </div>
        </div>
      </section>
      </div>
      </div>
      </div>
    </div>
  );
}

