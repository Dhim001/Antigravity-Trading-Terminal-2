/**
 * Model Training Dashboard — layout orchestrator for ML Lab.
 * State/fetchers live in useMlLabState; UI pieces in components/ml-lab/*.
 */
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  BrainCircuit,
  CheckCircle2,
  ExternalLink,
  FlaskConical,
  Layers,
  Loader2,
  PanelLeft,
  Play,
  RefreshCw,
  Workflow,
  XCircle,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import MlAutoTunePanel from '@/components/MlAutoTunePanel';
import PipelineStatusBar from '@/components/PipelineStatusBar';
import PipelineAutoDeploySettings from '@/components/PipelineAutoDeploySettings';
import BatchTrainDialog from '@/components/ml-lab/BatchTrainDialog';
import { MetricChips } from '@/components/ml-lab/MlMetricChips';
import { LossHistoryChart } from '@/components/ml-lab/MlLossChart';
import { DeployReadinessStrip, DataCalendarStrip } from '@/components/ml-lab/MlDeployReadiness';
import { JobProgressBar, JobPollLog, POLL_LOG_PREF_KEY } from '@/components/ml-lab/MlJobProgress';
import { DatasetBrowser } from '@/components/ml-lab/MlDatasetBrowser';
import { MlRetrainQueue } from '@/components/ml-lab/MlRetrainQueue';
import { MlTrainRunsTable } from '@/components/ml-lab/MlTrainRunsTable';
import { MlAdvancedKnobs } from '@/components/ml-lab/MlAdvancedKnobs';
import {
  ML_STRATEGIES,
  DEEP_ML_STRATEGIES,
  TRAINING_WINDOWS,
  TRAINING_TIMEFRAMES,
  defaultAdvancedKnobs,
  parsePositiveInt,
  estimateTrainingBars,
  estimateValidateBars,
  suggestedNFolds,
  fmtMetric,
} from '@/components/ml-lab/MlLabConstants';
import { isAbortError } from '@/api/client';
import { getStrategyMeta, isMlStrategy } from '@/config/strategies';
import { buildChallengerHint } from '@/lib/mlChallengerHint';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import {
  appendMlPollLog,
  clearMlPollLog,
  getMlTrainingSession,
  invalidateMatchingMlBacktests,
  markModelFreshAfterTrain,
  setCachedModelStatus,
  setMlJobId,
  setMlValidation,
} from '@/lib/mlTrainingSession';
import {
  matchesRetrainTarget,
  retrainQueueKey,
} from '@/hooks/mlLabStateHelpers';
import {
  advancePipeline,
  failPipeline,
  getAutoAdvance,
  getAutoDeployMode,
  getMlPipeline,
  setAutoAdvance,
  startPipeline,
  subscribeMlPipeline,
} from '@/lib/mlPipeline';
import {
  formatMlJobBudgetLabel,
  mlJobTimeoutMs,
} from '@/lib/mlJobTimeouts';
import {
  activateMlVersion,
  deleteMlVersion,
  submitMlTrainJob,
  submitMlValidateJob,
} from '@/lib/mlLabApi';
import { clearMlLabRequest, takeMlLabRequest } from '@/lib/mlLabRequests';
import {
  isMlLabStandaloneLocation,
  subscribeMlLabEvents,
} from '@/lib/standalonePanels';
import { useMlLabState } from '@/hooks/useMlLabState';

export default function ModelTrainingDashboard({
  detached = false,
  onDetach,
  onAttach,
} = {}) {
  // Re-render when pipeline stage changes (status bar + action enablement).
  useSyncExternalStore(subscribeMlPipeline, getMlPipeline, getMlPipeline);
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchScope, setBatchScope] = useState('untrained');
  const [autoAdvanceOn, setAutoAdvanceOn] = useState(() => getAutoAdvance());
  const pipelineValidateRef = useRef(null);
  const {

    activeSymbol,
    symbolOptions,
    setActiveSymbol,
    mlSession,
    strategy,
    setStrategy,
    trainingWindow,
    setTrainingWindow,
    trainingTimeframe,
    setTrainingTimeframe,
    advanced,
    setAdvanced,
    status,
    setStatus,
    inventory,
    retrainActions,
    setRetrainActions,
    retrainPending,
    setRetrainPending,
    retrainHistory,
    trainRuns,
    queueTelemetry,
    loading,
    refreshing,
    activatingVersionId,
    setActivatingVersionId,
    deletingVersionId,
    setDeletingVersionId,
    showPollLog,
    setShowPollLog,
    challengerDismissed,
    setChallengerDismissed,
    runNowKey,
    setRunNowKey,
    cancellingJob,
    championOosRef,
    panelScrollRef,
    localJobWaiterRef,
    training,
    validating,
    jobProgress,
    serverProgress,
    pollLog,
    activeJobId,
    validation,
    busyElsewhere,
    sessionTuningHint,
    meta,
    startJobProgress,
    finishJobProgress,
    refreshAll,
    handleManualRefresh,
    pollMlJobUntilDone,
    handleCancelJob,
  } = useMlLabState();

  const handleActivateVersion = async (version) => {
    if (!activeSymbol || !strategy || !version || activatingVersionId) return;
    const pin = version.trained_at || version.version_id;
    if (!pin) {
      toast.error('Version has no trained_at / version_id');
      return;
    }
    setActivatingVersionId(version.version_id || pin);
    try {
      const body = await activateMlVersion({
        symbol: activeSymbol,
        strategy,
        timeframe: trainingTimeframe,
        model_version: pin,
        version_id: version.version_id,
      });
      if (body?.ok) {
        setCachedModelStatus(activeSymbol, strategy, body, trainingTimeframe);
        setStatus(body);
        setChallengerDismissed(true);
        championOosRef.current = null;
        setMlValidation(null);
        invalidateMatchingMlBacktests(strategy, activeSymbol);
        toast.success(
          `Activated ${body.activated_version_id || pin} as current for ${strategy} / ${activeSymbol}`,
        );
        await refreshAll();
      } else {
        toast.error(body?.error || 'Failed to activate version');
      }
    } catch (err) {
      toast.error(err.message || 'Activate request failed');
    } finally {
      setActivatingVersionId(null);
    }
  };

  const handleCopyPin = async (pinValue) => {
    try {
      await navigator.clipboard.writeText(String(pinValue));
      toast.message('Pin copied — paste into bot config → Model version pin');
    } catch {
      toast.message(`Pin: ${pinValue}`);
    }
  };

  const handleDeleteVersion = async (version) => {
    if (!activeSymbol || !strategy || !version || activatingVersionId || deletingVersionId) return;
    if (version.is_current) {
      toast.error('Activate another version before deleting the active one');
      return;
    }
    const pin = version.trained_at || version.version_id;
    if (!pin) {
      toast.error('Version has no trained_at / version_id');
      return;
    }
    const label = version.version_id || pin;
    if (!window.confirm(
      `Delete model version ${label} for ${strategy} / ${activeSymbol}?\n\nThis removes the snapshot from disk and cannot be undone.`,
    )) {
      return;
    }
    setDeletingVersionId(version.version_id || pin);
    try {
      const body = await deleteMlVersion({
        symbol: activeSymbol,
        strategy,
        timeframe: trainingTimeframe,
        model_version: pin,
        version_id: version.version_id,
      });
      if (body?.ok) {
        setCachedModelStatus(activeSymbol, strategy, body, trainingTimeframe);
        setStatus(body);
        toast.success(`Deleted version ${body.deleted_version_id || label}`);
        await refreshAll();
      } else {
        toast.error(body?.error || 'Failed to delete version');
      }
    } catch (err) {
      toast.error(err.message || 'Delete request failed');
    } finally {
      setDeletingVersionId(null);
    }
  };

  // Fail the pipeline when a stage's job cannot even start, so the run never
  // parks at TRAINING/VALIDATING forever (status bar + quick actions unblock).
  const failPipelineAtStage = (stage, strat, symbol, error) => {
    const pipe = getMlPipeline();
    if (
      pipe.pipelineId
      && pipe.stage === stage
      && String(pipe.strategy || '').toUpperCase() === String(strat || '').toUpperCase()
      && String(pipe.symbol || '').toUpperCase() === String(symbol || '').toUpperCase()
    ) {
      failPipeline(pipe.pipelineId, { stage, error });
    }
  };

  const runTrainJob = async (strat, symbol, { fromQueue = false, hyperparams = null, championTrain = false } = {}) => {
    if (training || validating || busyElsewhere || !symbol || !strat) return false;
    // Must match backend pending key SYMBOL:STRATEGY:TF (not SYMBOL:STRATEGY).
    const queueKey = retrainQueueKey(symbol, strat, trainingTimeframe);
    if (fromQueue) setRunNowKey(queueKey);
    setMlValidation(null);
    if (strat !== strategy) setStrategy(strat);
    const trainTimeoutMs = mlJobTimeoutMs(strat, 'train', { months: trainingWindow });
    const token = startJobProgress('train', strat, symbol, trainingWindow);
    const knobs = strat === strategy ? advanced : defaultAdvancedKnobs(strat, 'train');
    const trainDefaults = defaultAdvancedKnobs(strat, 'train');
    const hp = hyperparams && typeof hyperparams === 'object' ? hyperparams : {};
    const isChampion = Boolean(championTrain || Object.keys(hp).length);
    localJobWaiterRef.current = true;
    let trainUiCompleted = false;
    let trainOk = false;
    try {
      if (DEEP_ML_STRATEGIES.has(strat) || strat === 'RL_PPO_AGENT' || strat === 'ML_SIGNAL_BOOST') {
        toast.message(
          isChampion
            ? `Champion retrain ${strat} with tuned hyperparams… up to ${formatMlJobBudgetLabel(trainTimeoutMs)}`
            : `Training ${strat}… up to ${formatMlJobBudgetLabel(trainTimeoutMs)} (CUDA if the backend torch build supports it)`,
        );
      }
      const body = await submitMlTrainJob({
        symbol,
        strategy: strat,
        async: true,
        config: {
          timeframe: trainingTimeframe,
          training_window_months: Number(trainingWindow),
          // Force a real Lab champion write — never inherit Optuna trial
          // skip_persist / _wf_mode that produce empty metrics + no artifact.
          champion_train: isChampion,
          skip_persist: false,
          skip_snapshot: false,
          ...(strat === 'RL_PPO_AGENT'
            ? {
                total_timesteps: parsePositiveInt(
                  knobs.totalTimesteps, trainDefaults.totalTimesteps, { min: 256, max: 500_000 },
                ),
                hidden_dim: parsePositiveInt(
                  knobs.hiddenDim, trainDefaults.hiddenDim, { min: 32, max: 1024 },
                ),
              }
            : {}),
          ...(DEEP_ML_STRATEGIES.has(strat)
            ? {
                epochs: parsePositiveInt(knobs.epochs, trainDefaults.epochs, { min: 1, max: 500 }),
                early_stop_patience: parsePositiveInt(
                  knobs.earlyStopPatience, trainDefaults.earlyStopPatience, { min: 1, max: 100 },
                ),
                hidden_dim: parsePositiveInt(
                  knobs.hiddenDim, trainDefaults.hiddenDim, { min: 32, max: 1024 },
                ),
                ...(strat === 'TRANSFORMER_SIGNAL'
                  ? { d_model: parsePositiveInt(knobs.hiddenDim, 128, { min: 32, max: 512 }) }
                  : {}),
                ...(strat === 'TCN_MULTI_HORIZON' ? { num_blocks: 6 } : {}),
              }
            : {}),
          ...(strat === 'ML_SIGNAL_BOOST'
            ? {
                gbm_max_iter: parsePositiveInt(knobs.gbmMaxIter, 300, { min: 40, max: 1000 }),
                gbm_max_depth: parsePositiveInt(knobs.gbmMaxDepth, 6, { min: 3, max: 12 }),
              }
            : {}),
          ...hp,
          // Re-assert after hp spread — Optuna trial flags must never win.
          skip_persist: false,
          skip_snapshot: false,
          ...(isChampion
            ? { champion_train: true, use_optimized_hyperparams: true }
            : (
              typeof sessionStorage !== 'undefined'
              && sessionStorage.getItem(`ml-lab-use-opt-hp:${String(symbol).toUpperCase()}:${String(strat).toUpperCase()}`) === '1'
                ? { use_optimized_hyperparams: true }
                : {}
            )),
        },
      });
      if (!body?.ok) {
        toast.error(body?.error || 'Training failed to start');
        failPipelineAtStage('TRAINING', strat, symbol, body?.error || 'Training failed to start');
        return;
      }
      const jobId = body.job_id;
      if (!jobId) {
        toast.error('Server did not return a job_id');
        failPipelineAtStage('TRAINING', strat, symbol, 'Server did not return a job_id');
        return;
      }
      setMlJobId(jobId);
      const job = await pollMlJobUntilDone(jobId, {
        strategy: strat,
        kind: 'train',
        months: trainingWindow,
      });
      trainUiCompleted = true;
      const result = (job.result && typeof job.result === 'object') ? job.result : {};
      if (job.status === 'cancelled' || result.cancelled) {
        toast.message('Training cancelled');
      } else if (job.status === 'done' && result.ok !== false) {
        trainOk = true;
        const tw = result.training_window;
        const twNote = tw?.bars != null
          ? ` · ${Number(tw.bars).toLocaleString()} bars`
            + (tw.span_days != null ? ` (~${tw.span_days}d)` : '')
            + (tw.training_window_months != null ? ` / ${tw.training_window_months}mo` : '')
          : '';
        const m = result.metrics && typeof result.metrics === 'object' ? result.metrics : {};
        const early = result.early_stopped || m.early_stopped;
        const epNote = early
          ? ` · early stop ${m.epochs_trained ?? result.epochs_trained ?? '?'}`
            + `/${m.epochs_budget ?? advanced?.epochs ?? '?'}`
            + ' (val plateau)'
          : (m.epochs_trained != null
            ? ` · ${m.epochs_trained} epochs`
            : '');
        const metricBits = [];
        const va = m.val_accuracy ?? m.accuracy ?? result.mean_accuracy;
        if (va != null && Number.isFinite(Number(va))) {
          metricBits.push(`val ${(Number(va) * 100).toFixed(1)}%`);
        }
        if (m.fit_samples != null) metricBits.push(`${Number(m.fit_samples).toLocaleString()} fits`);
        if (m.train_samples != null && m.val_samples != null) {
          metricBits.push(`${m.train_samples}/${m.val_samples} tv`);
        }
        const metricNote = metricBits.length ? ` · ${metricBits.join(' · ')}` : '';
        const metricsMissing = !result.metrics
          || (typeof result.metrics === 'object' && !Object.keys(result.metrics).length);
        if (metricsMissing && !metricBits.length) {
          toast.message(
            `Training finished for ${strat} / ${symbol}${twNote} — metrics missing from job result; check Recent runs / model status.`,
          );
        } else {
          toast.success(`Training complete for ${strat} / ${symbol}${twNote}${epNote}${metricNote}`);
        }
        if (isChampion) {
          try {
            sessionStorage.setItem(
              `ml-lab-use-opt-hp:${String(symbol).toUpperCase()}:${String(strat).toUpperCase()}`,
              '1',
            );
          } catch { /* ignore */ }
        }
        invalidateMatchingMlBacktests(strat, symbol);
        // Refetch badges but keep race guard so late untrained cannot undo Fresh.
        markModelFreshAfterTrain(symbol, strat, trainingTimeframe);
        // Drop from retrain audit immediately (backend also clears via record_retrain).
        setRetrainPending((prev) => prev.filter((p) => (
          !matchesRetrainTarget(p, symbol, strat, trainingTimeframe)
        )));
        setRetrainActions((prev) => prev.filter((a) => (
          !matchesRetrainTarget(a, symbol, strat, trainingTimeframe)
        )));
        const pipe = getMlPipeline();
        if (
          pipe.pipelineId
          && pipe.stage === 'TRAINING'
          && pipe.autoAdvance
          && String(pipe.strategy || '').toUpperCase() === String(strat).toUpperCase()
          && String(pipe.symbol || '').toUpperCase() === String(symbol).toUpperCase()
        ) {
          advancePipeline(pipe.pipelineId, { result });
          // Chain validate after refresh in finally.
          pipelineValidateRef.current = true;
        }
      } else {
        toast.error(job.error || result.error || 'Training failed');
        const pipe = getMlPipeline();
        if (pipe.pipelineId && pipe.stage === 'TRAINING') {
          failPipeline(pipe.pipelineId, {
            stage: 'TRAINING',
            error: job.error || result.error || 'Training failed',
          });
        }
      }
    } catch (err) {
      if (!isAbortError(err)) {
        const jid = getMlTrainingSession().jobId;
        if (jid) {
          toast.error(
            `${err.message || 'Training UI interrupted'} — server job ${String(jid).slice(0, 8)} may still be running. Keep this panel open or re-check model status.`,
          );
        } else {
          toast.error(err.message || 'Training request failed');
        }
        const pipe = getMlPipeline();
        if (pipe.pipelineId && pipe.stage === 'TRAINING') {
          failPipeline(pipe.pipelineId, {
            stage: 'TRAINING',
            error: err.message || 'Training request failed',
          });
        }
      }
    } finally {
      localJobWaiterRef.current = false;
      const sess = getMlTrainingSession();
      if (trainUiCompleted || !sess.jobId || sess.jobToken !== token) {
        finishJobProgress(token);
      } else {
        // Poll/submit interrupted after job_id was assigned — keep session so
        // the background job poller can finish instead of looking "stopped".
        appendMlPollLog({
          status: 'running',
          phase: 'waiting',
          detail: 'UI wait interrupted — server job still tracked',
          note: 'ui_interrupt',
        });
      }
      if (fromQueue) setRunNowKey(null);
      // Always refresh enriched status — never cache thin train payloads as status.
      await refreshAll();
      if (pipelineValidateRef.current) {
        pipelineValidateRef.current = false;
        const chainedPipeId = getMlPipeline().pipelineId;
        queueMicrotask(() => {
          void pipelineValidateRef.currentHandleValidate?.().then((ok) => {
            // Guard-blocked validate (busy Lab) would otherwise park at VALIDATING.
            if (ok === false) {
              const p = getMlPipeline();
              if (p.pipelineId && p.pipelineId === chainedPipeId && p.stage === 'VALIDATING') {
                failPipeline(p.pipelineId, {
                  stage: 'VALIDATING',
                  error: 'Validate could not start — ML Lab busy',
                });
              }
            }
          });
        });
      }
    }
    return trainOk;
  };

  const handleTrain = async () => {
    await runTrainJob(strategy, activeSymbol);
  };

  const handleRunNow = async (strat, symbol) => {
    if (!strat || !symbol) return;
    if (!isMlStrategy(strat)) {
      toast.message(
        `Lab training not supported for ${strat} — technical/agent bots use meta-label retrain, not Lab warming.`,
      );
      return;
    }
    if (symbol !== activeSymbol) {
      toast.message(`Training ${strat} for ${symbol} (chart symbol is ${activeSymbol || '—'})`);
    }
    await runTrainJob(strat, symbol, { fromQueue: true });
  };

  const handleValidate = async (opts = {}) => {
    const strat = opts.strategy || strategy;
    if (validating || training || busyElsewhere || !activeSymbol) return false;
    if (strat !== strategy) setStrategy(strat);
    setMlValidation(null);
    const isRl = strat === 'RL_PPO_AGENT';
    const isDeep = DEEP_ML_STRATEGIES.has(strat);
    const defaults = defaultAdvancedKnobs(strat, 'train');
    const knobs = strat === strategy ? advanced : defaults;
    const nFolds = parsePositiveInt(knobs.nFolds, defaults.nFolds, { min: 2, max: 8 });
    const validateMaxBars = parsePositiveInt(
      knobs.validateMaxBars,
      defaults.validateMaxBars,
      { min: 200, max: 100_000 },
    );
    const pboSegments = parsePositiveInt(knobs.pboSegments, defaults.pboSegments, { min: 2, max: 8 });
    const pboMaxCombos = parsePositiveInt(knobs.pboMaxCombos, defaults.pboMaxCombos, { min: 1, max: 16 });
    championOosRef.current = status?.walk_forward?.mean_oos_accuracy ?? null;
    setChallengerDismissed(false);
    const validateTimeoutMs = mlJobTimeoutMs(strat, 'validate', { months: trainingWindow });
    const token = startJobProgress('validate', strat, activeSymbol, trainingWindow);
    localJobWaiterRef.current = true;
    let validateOk = false;
    try {
      toast.message(
        isRl
          ? `Running RL walk-forward at train capacity (no PBO)… up to ${formatMlJobBudgetLabel(validateTimeoutMs)}`
          : isDeep
            ? `Running walk-forward at train capacity (no PBO)… up to ${formatMlJobBudgetLabel(validateTimeoutMs)}`
            : `Running walk-forward + PBO at train capacity… up to ${formatMlJobBudgetLabel(validateTimeoutMs)}`,
      );
      // Capacity parity: reuse Lab Advanced train knobs so OOS ≈ production Train.
      const wfEpochs = isDeep
        ? parsePositiveInt(knobs.epochs, defaults.epochs, { min: 1, max: 500 })
        : null;
      const earlyStop = parsePositiveInt(
        knobs.earlyStopPatience,
        defaults.earlyStopPatience,
        { min: 1, max: 100 },
      );
      const body = await submitMlValidateJob({
        symbol: activeSymbol,
        strategy: strat,
        async: true,
        n_folds: nFolds,
        mode: 'rolling',
        // Deep/RL fold PBO re-trains every combo — too heavy for Lab Validate.
        pbo: !isRl && !isDeep,
        pbo_segments: pboSegments,
        timeframe: trainingTimeframe,
        config: {
          timeframe: trainingTimeframe,
          training_window_months: Number(trainingWindow),
          symbol: activeSymbol,
          model_symbol: activeSymbol,
          _wf_mode: true,
          wf_use_gpu: true,
          wf_capacity_parity: true,
          validate_max_bars: validateMaxBars,
          pbo_max_combos: pboMaxCombos,
          early_stop_patience: earlyStop,
          ...(isRl
            ? {
                total_timesteps: parsePositiveInt(
                  knobs.totalTimesteps,
                  defaults.totalTimesteps,
                  { min: 512, max: 500_000 },
                ),
                n_steps: 2048,
                ppo_epochs: 10,
                hidden_dim: parsePositiveInt(knobs.hiddenDim, defaults.hiddenDim, {
                  min: 32, max: 512,
                }),
                skip_onnx_export: true,
              }
            : {}),
          ...(isDeep
            ? {
                hidden_dim: parsePositiveInt(knobs.hiddenDim, defaults.hiddenDim, {
                  min: 32, max: 512,
                }),
                ...(strat === 'TRANSFORMER_SIGNAL'
                  ? {
                      d_model: parsePositiveInt(knobs.hiddenDim, 128, {
                        min: 32, max: 512,
                      }),
                    }
                  : {}),
                ...(strat === 'TCN_MULTI_HORIZON' ? { num_blocks: 6 } : {}),
              }
            : {}),
          ...(strat === 'ML_SIGNAL_BOOST'
            ? {
                gbm_max_iter: parsePositiveInt(knobs.gbmMaxIter, defaults.gbmMaxIter, {
                  min: 40, max: 1000,
                }),
                gbm_max_depth: parsePositiveInt(knobs.gbmMaxDepth, defaults.gbmMaxDepth, {
                  min: 3, max: 12,
                }),
              }
            : {}),
          ...(wfEpochs != null ? { epochs: wfEpochs, wf_epochs: wfEpochs } : {}),
        },
      });
      if (!body?.ok) {
        const foldErr = Array.isArray(body?.folds)
          ? body.folds.find((f) => f?.error)?.error
          : null;
        toast.error(body?.error || foldErr || 'Validation failed to start');
        setMlValidation(body || { ok: false, error: 'Validation failed' });
        failPipelineAtStage(
          'VALIDATING', strat, activeSymbol, body?.error || foldErr || 'Validation failed to start',
        );
        return false;
      }
      const jobId = body.job_id;
      if (!jobId) {
        toast.error('Server did not return a job_id');
        failPipelineAtStage('VALIDATING', strat, activeSymbol, 'Server did not return a job_id');
        return false;
      }
      setMlJobId(jobId);
      const job = await pollMlJobUntilDone(jobId, {
        strategy: strat,
        kind: 'validate',
        months: trainingWindow,
      });
      const result = (job.result && typeof job.result === 'object')
        ? job.result
        : { ok: false, error: job.error || 'Validation failed' };
      setMlValidation(result);
      if (job.status === 'cancelled' || result.cancelled) {
        toast.message('Validation cancelled');
        const pipe = getMlPipeline();
        if (pipe.pipelineId && pipe.stage === 'VALIDATING') {
          failPipeline(pipe.pipelineId, { stage: 'VALIDATING', error: 'cancelled' });
        }
      } else if (job.status === 'done' && result.ok) {
        validateOk = true;
        const tw = result.training_window;
        const twNote = tw?.bars != null
          ? ` · ${Number(tw.bars).toLocaleString()} bars`
            + (tw.span_days != null ? ` (~${tw.span_days}d)` : '')
          : '';
        const persisted = result.validation_persisted;
        if (persisted && persisted.ok === false) {
          toast.error(
            persisted.error
              || 'Walk-forward finished but deploy stamp was not saved — retry Validate',
          );
        } else {
          toast.success(`Walk-forward validation finished${twNote}`);
        }
        const pipe = getMlPipeline();
        if (pipe.pipelineId && pipe.stage === 'VALIDATING' && pipe.autoAdvance) {
          advancePipeline(pipe.pipelineId, { result });
          // AlgoPanel listens for BACKTESTING and kicks off holdout BT.
        }
      } else {
        const foldErr = Array.isArray(result?.folds)
          ? result.folds.find((f) => f?.error)?.error
          : null;
        toast.error(job.error || result.error || foldErr || 'Validation failed');
        const pipe = getMlPipeline();
        if (pipe.pipelineId && pipe.stage === 'VALIDATING') {
          failPipeline(pipe.pipelineId, {
            stage: 'VALIDATING',
            error: job.error || result.error || foldErr || 'Validation failed',
          });
        }
      }
    } catch (err) {
      const msg = err?.message || String(err) || 'Validation request failed';
      const badJson = /invalid json|internal server error/i.test(msg);
      const friendly = badJson
        ? 'Validation hit a server error (non-JSON response). Recycle the backend and retry — RL walk-forward needs the latest ONNX export fix.'
        : msg;
      setMlValidation({ ok: false, error: friendly });
      if (!isAbortError(err)) {
        toast.error(badJson ? 'Validation failed — recycle backend and retry' : msg);
      }
      const pipe = getMlPipeline();
      if (pipe.pipelineId && pipe.stage === 'VALIDATING') {
        failPipeline(pipe.pipelineId, { stage: 'VALIDATING', error: friendly });
      }
    } finally {
      localJobWaiterRef.current = false;
      finishJobProgress(token);
      await refreshAll();
    }
    return validateOk;
  };

  // Keep a stable handle for pipeline auto-advance → validate.
  pipelineValidateRef.currentHandleValidate = handleValidate;

  const handleFullPipeline = () => {
    if (!activeSymbol || !strategy) {
      toast.error('Select a symbol and ML strategy first');
      return;
    }
    if (training || validating || busyElsewhere) {
      toast.message('Wait for the current ML job to finish');
      return;
    }
    startPipeline({
      strategy,
      symbol: activeSymbol,
      timeframe: trainingTimeframe,
      trainingWindow,
      autoAdvance: autoAdvanceOn,
      autoDeployMode: getAutoDeployMode(),
    });
    toast.message('Full pipeline started — Train → Validate → Backtest → Gate');
    void runTrainJob(strategy, activeSymbol);
  };

  useEffect(() => {
    const onRunPipeline = (e) => {
      clearMlLabRequest('ml-lab-run-pipeline');
      const d = e?.detail || {};
      const strat = d.strategy || strategy;
      const sym = d.symbol || activeSymbol;
      const tf = d.timeframe || trainingTimeframe;
      const mode = d.mode || 'full';
      if (!sym || !strat) return;
      // Live session check (closure state may be stale in this long-lived effect).
      const sess = getMlTrainingSession();
      if (sess.training || sess.validating || sess.tuning) {
        toast.message(
          `ML job already running for ${sess.strategy} / ${sess.symbol} — wait for it before starting a pipeline`,
        );
        const existing = getMlPipeline();
        if (d.pipelineId && existing.pipelineId === d.pipelineId) {
          failPipeline(d.pipelineId, { stage: 'TRAINING', error: 'ML Lab busy — pipeline not started' });
        }
        return;
      }
      if (strat !== strategy) setStrategy(strat);
      if (tf && tf !== trainingTimeframe) setTrainingTimeframe(tf);
      // Presets may already have started the pipeline — reuse that id.
      const existing = getMlPipeline();
      if (!(d.pipelineId && existing.pipelineId === d.pipelineId)) {
        startPipeline({
          strategy: strat,
          symbol: sym,
          timeframe: tf,
          trainingWindow,
          autoAdvance: true,
          autoDeployMode: mode === 'retrain_validate' ? 'approval' : getAutoDeployMode(),
          stopAfterValidate: mode === 'retrain_validate',
          presetId: mode === 'retrain_validate' ? 'ml_retrain_validate' : 'ml_full_pipeline',
        });
      }
      setTimeout(() => {
        void runTrainJob(strat, sym);
      }, 50);
    };
    const onOpenBatch = (e) => {
      clearMlLabRequest('ml-lab-open-batch');
      setBatchScope(e?.detail?.scope || 'untrained');
      setBatchOpen(true);
    };
    const onRetrain = (e) => {
      clearMlLabRequest('ml-lab-retrain');
      const d = e?.detail || {};
      if (d.strategy && d.strategy !== strategy) setStrategy(d.strategy);
      if (d.timeframe && d.timeframe !== trainingTimeframe) setTrainingTimeframe(d.timeframe);
      if (d.strategy && (d.symbol || activeSymbol) && d.autoStart !== false) {
        const sess = getMlTrainingSession();
        if (sess.training || sess.validating || sess.tuning) {
          toast.message(
            `Retrain queued behind active ML job (${sess.strategy} / ${sess.symbol}) — start it when the job finishes`,
          );
          return;
        }
        setTimeout(() => {
          void runTrainJob(d.strategy, d.symbol || activeSymbol);
        }, 50);
      }
    };
    window.addEventListener('ml-lab-run-pipeline', onRunPipeline);
    window.addEventListener('ml-lab-open-batch', onOpenBatch);
    window.addEventListener('ml-lab-retrain', onRetrain);
    // Detached Lab lives in another window — requests arrive via BroadcastChannel.
    // Gate on standalone location so a second full-terminal tab never double-runs.
    const isStandaloneLab = isMlLabStandaloneLocation(window.location.search);
    const unsubscribeStandalone = isStandaloneLab
      ? subscribeMlLabEvents((msg) => {
        if (msg?.type !== 'ml-lab-request' || !msg.requestType) return;
        const evt = { detail: msg.detail || {} };
        if (msg.requestType === 'ml-lab-run-pipeline') onRunPipeline(evt);
        else if (msg.requestType === 'ml-lab-open-batch') onOpenBatch(evt);
        else if (msg.requestType === 'ml-lab-retrain') onRetrain(evt);
      })
      : null;
    // Drain a request posted while the Lab was unmounted (dock keep-alive expiry).
    const pendingReq = takeMlLabRequest([
      'ml-lab-run-pipeline',
      'ml-lab-open-batch',
      'ml-lab-retrain',
    ]);
    if (pendingReq) {
      const evt = { detail: pendingReq.detail };
      if (pendingReq.type === 'ml-lab-run-pipeline') onRunPipeline(evt);
      else if (pendingReq.type === 'ml-lab-open-batch') onOpenBatch(evt);
      else onRetrain(evt);
    }
    return () => {
      window.removeEventListener('ml-lab-run-pipeline', onRunPipeline);
      window.removeEventListener('ml-lab-open-batch', onOpenBatch);
      window.removeEventListener('ml-lab-retrain', onRetrain);
      unsubscribeStandalone?.();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- handlers use latest closures via runTrainJob
  }, [activeSymbol, strategy, trainingTimeframe, trainingWindow]);

  const statusLabel = training
    ? 'Training'
    : validating
      ? 'Validating'
      : status?.trained
        ? (status?.stale ? 'Ready (cached)' : 'Ready')
        : status?.error
          ? 'Failed'
          : 'Idle';

  const trainedCount = inventory.filter((r) => r.trained).length;
  const queueBadge = (queueTelemetry.active > 0 || queueTelemetry.queued > 0)
    ? `${queueTelemetry.active} running · ${queueTelemetry.queued} queued`
    : null;

  const displayValidation = validation || (
    status?.walk_forward || status?.pbo
      ? {
        ok: Boolean(status.walk_forward?.ok),
        mean_accuracy: status.walk_forward?.mean_oos_accuracy,
        n_folds: status.walk_forward?.n_folds,
        successful_folds: status.walk_forward?.successful_folds,
        recommendation: status.walk_forward?.recommendation,
        pbo: status.pbo,
        _persisted: true,
      }
      : null
  );

  const challengerHint = (
    !challengerDismissed
    && !displayValidation?._persisted
    && displayValidation?.ok
  )
    ? buildChallengerHint({
      validation: displayValidation,
      championOos: championOosRef.current,
      versions: status?.versions,
    })
    : null;

  const dismissChallengerHint = () => {
    setChallengerDismissed(true);
    championOosRef.current = null;
  };

  return (
    <div
      ref={panelScrollRef}
      className="dock-panel dock-panel--ml-training overflow-y-auto h-full"
    >
      <header
        title="Train and validate ML models per symbol. Optimizer Lab handles hyperparameter sweeps only."
      >
        <h3 className="ml-training__title">
          <BrainCircuit size={16} aria-hidden />
          Model Training
        </h3>
        <div className="ml-training__header-right">
          {queueBadge && (
            <span className="ml-training__queue-badge num-mono" title="ML train/validate worker queue">
              {queueBadge}
            </span>
          )}
          {status?.trained_at && (
            <span className="ml-training__header-meta num-mono">
              {trainingTimeframe} · {new Date(status.trained_at).toLocaleString()}
            </span>
          )}
          {status && !status.trained && (
            <span className="ml-training__header-meta text-muted-foreground">
              no {trainingTimeframe} model
            </span>
          )}
          <label className="ml-training__header-meta flex items-center gap-1 cursor-pointer" title="Auto-advance pipeline stages">
            <input
              type="checkbox"
              checked={autoAdvanceOn}
              onChange={(e) => {
                const on = Boolean(e.target.checked);
                setAutoAdvanceOn(on);
                setAutoAdvance(on);
              }}
            />
            Auto-advance
          </label>
          <PipelineAutoDeploySettings compact />
          {(onDetach || onAttach) && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs gap-1 shrink-0"
              title={
                detached
                  ? 'Dock ML Lab back into the trading layout'
                  : 'Open ML Lab in a separate window (keeps one Lab instance)'
              }
              onClick={() => (detached ? onAttach?.() : onDetach?.())}
            >
              {detached ? (
                <>
                  <PanelLeft size={14} aria-hidden />
                  Reattach
                </>
              ) : (
                <>
                  <ExternalLink size={14} aria-hidden />
                  Detach
                </>
              )}
            </Button>
          )}
        </div>
      </header>

      <PipelineStatusBar className="mx-3 mt-2" />

      <section className="ml-training__controls">
        <div className="ml-training__controls-grid">
          <div className="ml-training__field">
            <Label className="text-xs">Symbol</Label>
            <Select
              value={activeSymbol || undefined}
              onValueChange={setActiveSymbol}
            >
              <SelectTrigger size="sm" className="h-8 num-mono" aria-label="Symbol">
                <SelectValue placeholder="Select symbol" />
              </SelectTrigger>
              <SelectContent position="popper" className="max-h-56">
                {symbolOptions.map((sym) => (
                  <SelectItem key={sym} value={sym} className="text-xs num-mono">
                    {sym}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="ml-training__field">
            <Label className="text-xs">Strategy</Label>
            <Select value={strategy} onValueChange={setStrategy}>
              <SelectTrigger size="sm" className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ML_STRATEGIES.map((id) => (
                  <SelectItem key={id} value={id} className="text-xs">
                    {getStrategyMeta(id).label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="ml-training__field">
            <Label className="text-xs">Bar timeframe</Label>
            <Select value={trainingTimeframe} onValueChange={setTrainingTimeframe}>
              <SelectTrigger size="sm" className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TRAINING_TIMEFRAMES.map((t) => (
                  <SelectItem key={t.value} value={t.value} className="text-xs">
                    {t.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[10px] text-muted-foreground mt-0.5 leading-snug">
              Must match the bot execution TF. HTF models store separately (e.g. ETHUSDT__15M).
            </p>
          </div>
          <div className="ml-training__field">
            <Label className="text-xs">Training window</Label>
            <Select
              value={trainingWindow}
              onValueChange={(v) => setTrainingWindow(v)}
            >
              <SelectTrigger size="sm" className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TRAINING_WINDOWS.map((w) => (
                  <SelectItem key={w.value} value={w.value} className="text-xs">
                    {w.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[10px] text-muted-foreground mt-0.5 leading-snug">
              Train ~{estimateTrainingBars(trainingWindow, trainingTimeframe).toLocaleString()}{' '}
              {trainingTimeframe} bars · Validate ~{estimateValidateBars(trainingWindow, trainingTimeframe, strategy).toLocaleString()}{' '}
              bars · {suggestedNFolds(trainingWindow, strategy)} folds
              · budget ~{formatMlJobBudgetLabel(mlJobTimeoutMs(strategy, 'train', { months: trainingWindow }))}{' '}
              train / ~{formatMlJobBudgetLabel(mlJobTimeoutMs(strategy, 'validate', { months: trainingWindow }))}{' '}
              validate (Advanced knobs update with this pick).
            </p>
            <DataCalendarStrip
              calendar={status?.data_calendar}
              trainingWindow={trainingWindow}
            />
          </div>
          <div className="ml-training__field">
            <Label className="text-xs">Status</Label>
            <p className="text-sm flex items-center gap-2">
              {(training || validating || loading) && (
                <Loader2 size={14} className="animate-spin" aria-hidden />
              )}
              {statusLabel}
              <span className="text-xs text-muted-foreground">({meta.shortLabel})</span>
            </p>
          </div>
        </div>

        <MlAdvancedKnobs
          advanced={advanced}
          setAdvanced={setAdvanced}
          strategy={strategy}
        />

        <JobProgressBar
          job={jobProgress}
          serverProgress={serverProgress}
          onCancel={activeJobId ? handleCancelJob : undefined}
          cancelling={cancellingJob}
        />
        <JobPollLog
          entries={pollLog}
          enabled={showPollLog}
          onEnabledChange={(on) => {
            setShowPollLog(on);
            try {
              window.localStorage.setItem(POLL_LOG_PREF_KEY, on ? '1' : '0');
            } catch {
              /* ignore */
            }
          }}
          onClear={() => clearMlPollLog()}
        />

        {busyElsewhere && (
          <p className="text-xs text-amber-400/90">
            Job running for {mlSession.strategy} / {mlSession.symbol}
            {mlSession.tuning ? ' (auto-tune)' : mlSession.validating ? ' (validate)' : ' (train)'}
            {queueBadge ? ` · ${queueBadge}` : ''}
            {' '}— switch back to that pair to watch progress.
          </p>
        )}
        {!busyElsewhere && queueBadge && !(training || validating || sessionTuningHint) && (
          <p className="text-xs text-muted-foreground">
            Worker queue: {queueBadge}
          </p>
        )}
        <MetricChips metrics={status?.metrics} />
        <DeployReadinessStrip status={status} strategy={strategy} />
        <LossHistoryChart
          history={status?.loss_history}
          trainHistory={status?.train_history}
          metrics={status?.metrics}
        />
        {status?.fetch_error && status?.trained && (
          <p className="text-xs text-muted-foreground">
            Showing last known status ({status.fetch_error}).
          </p>
        )}
        {status?.error && !status?.trained && (
          <p className="text-xs text-destructive">{status.error}</p>
        )}

        <div className="ml-training__actions">
          {(status?.artifact || status?.version_id) && (
            <span className="ml-training__artifact num-mono">
              {status.artifact || 'artifact'}
              {status.version_id ? ` · ${status.version_id}` : status.trained_at ? ` · ${status.trained_at}` : ''}
            </span>
          )}
          <Button
            type="button"
            size="sm"
            className="h-8 text-xs gap-1"
            disabled={training || validating || busyElsewhere || !activeSymbol}
            onClick={handleTrain}
          >
            {training ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            Trigger retrain
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 text-xs gap-1"
            disabled={training || validating || busyElsewhere || !activeSymbol}
            onClick={handleValidate}
          >
            {validating ? <Loader2 size={14} className="animate-spin" /> : <FlaskConical size={14} />}
            {strategy === 'RL_PPO_AGENT' || DEEP_ML_STRATEGIES.has(strategy)
              ? 'Walk-forward'
              : 'Walk-forward + PBO'}
          </Button>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="h-8 text-xs gap-1"
            disabled={training || validating || busyElsewhere || !activeSymbol}
            onClick={handleFullPipeline}
            title="Train → Validate → Backtest → Gate (auto-advance)"
          >
            <Workflow size={14} />
            Run Full Pipeline
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 text-xs gap-1"
            disabled={training || validating || busyElsewhere || !activeSymbol}
            onClick={() => setBatchOpen(true)}
            title="Train multiple strategies for this symbol"
          >
            <Layers size={14} />
            Batch Train
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 text-xs gap-1"
            disabled={refreshing}
            aria-busy={refreshing}
            onClick={handleManualRefresh}
            title="Reload model status without leaving this panel"
          >
            {refreshing
              ? <Loader2 size={14} className="animate-spin" />
              : <RefreshCw size={14} />}
            Refresh
          </Button>
        </div>
        <p className="text-[10px] text-muted-foreground -mt-1">
          Trigger retrain and Walk-forward Validate both use the Training window above
          ({Number(advanced.validateMaxBars).toLocaleString()} bars at capacity parity)
          so OOS metrics match production Train depth.
        </p>
      </section>

      <DatasetBrowser
        dataset={status?.dataset}
        versions={status?.versions}
        activatingVersionId={activatingVersionId}
        deletingVersionId={deletingVersionId}
        onActivateVersion={handleActivateVersion}
        onDeleteVersion={handleDeleteVersion}
        onCopyPin={handleCopyPin}
      />

      {displayValidation && (
        <section className="ml-training__card">
          <div className="ml-training__card-head">
            <h4 className="ml-training__section-title">Validation result</h4>
            {displayValidation._persisted && (
              <span className="ml-training__header-meta">from model status</span>
            )}
          </div>
          {displayValidation.ok === false && (
            <p className="text-xs text-destructive">
              {displayValidation.error
                || (Array.isArray(displayValidation.folds) && displayValidation.folds.find((f) => f?.error)?.error)
                || 'Validation failed'}
            </p>
          )}
          {displayValidation.ok && (
            <div className="grid gap-2 sm:grid-cols-3 text-xs">
              {displayValidation.aggregate?.mean_oos_return_pct != null && (
                <div>
                  <span className="text-muted-foreground">Mean OOS return</span>
                  <p className="num-mono font-medium">
                    {Number(displayValidation.aggregate.mean_oos_return_pct) >= 0 ? '+' : ''}
                    {Number(displayValidation.aggregate.mean_oos_return_pct).toFixed(2)}%
                  </p>
                </div>
              )}
              {(displayValidation.mean_accuracy ?? displayValidation.aggregate?.mean_oos_accuracy) != null
                && displayValidation.aggregate?.mean_oos_return_pct == null && (
                <div>
                  <span className="text-muted-foreground">Mean OOS accuracy</span>
                  <p className="num-mono font-medium">
                    {fmtMetric(displayValidation.mean_accuracy ?? displayValidation.aggregate?.mean_oos_accuracy)}
                  </p>
                </div>
              )}
              {displayValidation.n_folds != null && (
                <div>
                  <span className="text-muted-foreground">Folds</span>
                  <p className="num-mono font-medium">
                    {displayValidation.successful_folds ?? displayValidation.n_folds}/{displayValidation.n_folds}
                  </p>
                </div>
              )}
              {displayValidation.pbo?.skipped && (
                <div>
                  <span className="text-muted-foreground">PBO</span>
                  <p className="text-xs text-muted-foreground">Skipped (interactive)</p>
                </div>
              )}
              {displayValidation.pbo?.pbo != null && (
                <div>
                  <span className="text-muted-foreground">PBO</span>
                  <p className={cn(
                    'num-mono font-medium',
                    Number(displayValidation.pbo.pbo) >= 0.5 && 'text-destructive',
                  )}
                  >
                    {fmtMetric(displayValidation.pbo.pbo)}
                  </p>
                </div>
              )}
              {displayValidation.capacity_gap_warning && (
                <div className="sm:col-span-3">
                  <span className="text-muted-foreground">Capacity note</span>
                  <p className="text-xs text-amber-600 dark:text-amber-400">
                    {displayValidation.capacity_gap_warning}
                  </p>
                </div>
              )}
              {displayValidation.recommendation && (
                <div className="sm:col-span-3">
                  <span className="text-muted-foreground">Recommendation</span>
                  <p className="text-xs">{displayValidation.recommendation}</p>
                </div>
              )}
            </div>
          )}
          {Array.isArray(displayValidation.folds) && displayValidation.folds.length > 0 && (
            <ul className="ml-training__fold-list text-[0.65rem] text-muted-foreground">
              {displayValidation.folds.slice(0, 8).map((f, i) => (
                <li key={i} className={cn('num-mono', f.ok === false && 'text-destructive')}>
                  fold {f.fold ?? i + 1}
                  {f.ok === false
                    ? `: FAIL ${f.error || '—'}`
                    : `: acc ${fmtMetric(f.accuracy ?? f.oos_metrics?.accuracy ?? f.val_accuracy) ?? '—'}`}
                  {(f.n_samples ?? f.oos_metrics?.n_signals ?? f.test_bars) != null
                    ? ` · n=${f.n_samples ?? f.oos_metrics?.n_signals ?? f.test_bars}`
                    : ''}
                </li>
              ))}
            </ul>
          )}
          {challengerHint && (
            <div className="ml-training__challenger">
              <div className="ml-training__challenger-text">
                <p className="ml-training__challenger-title">Challenger beats champion</p>
                <p className="text-[0.65rem] text-muted-foreground num-mono">
                  OOS {fmtMetric(challengerHint.championOos)} → {fmtMetric(challengerHint.challengerOos)}
                  {challengerHint.version?.version_id
                    ? ` · ${challengerHint.version.version_id}`
                    : ''}
                  {challengerHint.alreadyLive ? ' · already live' : ''}
                </p>
              </div>
              <div className="ml-training__challenger-actions">
                {challengerHint.canActivate && challengerHint.version && (
                  <Button
                    type="button"
                    size="sm"
                    className="h-7 text-[0.65rem] gap-1 shrink-0"
                    disabled={Boolean(activatingVersionId)}
                    onClick={() => handleActivateVersion(challengerHint.version)}
                  >
                    {activatingVersionId ? <Loader2 size={12} className="animate-spin" /> : null}
                    Activate
                  </Button>
                )}
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-7 text-[0.65rem] shrink-0"
                  onClick={dismissChallengerHint}
                >
                  Dismiss
                </Button>
              </div>
            </div>
          )}
        </section>
      )}

      <MlAutoTunePanel
        symbol={activeSymbol}
        strategy={strategy}
        timeframe={trainingTimeframe}
        trainingWindow={trainingWindow}
        disabled={training || validating || busyElsewhere}
        onApplyAndRetrain={(hp) => {
          if (!activeSymbol) {
            toast.error('No active symbol');
            return;
          }
          // Mirror tuned knobs into the Advanced UI so a later "Trigger retrain"
          // keeps the same architecture (epochs / hidden_dim / GBM depth…).
          // Non-UI knobs (learning_rate, lookback, …) are filled server-side
          // from the persisted Optuna best_config.
          if (hp && typeof hp === 'object') {
            setAdvanced((prev) => {
              const next = { ...prev };
              if (hp.epochs != null) next.epochs = String(hp.epochs);
              if (hp.hidden_dim != null) next.hiddenDim = String(hp.hidden_dim);
              if (hp.early_stop_patience != null) {
                next.earlyStopPatience = String(hp.early_stop_patience);
              }
              if (hp.total_timesteps != null) {
                next.totalTimesteps = String(hp.total_timesteps);
              }
              if (hp.gbm_max_iter != null) next.gbmMaxIter = String(hp.gbm_max_iter);
              if (hp.gbm_max_depth != null) next.gbmMaxDepth = String(hp.gbm_max_depth);
              // Transformer Optuna tunes d_model separately; keep UI knob aligned.
              if (hp.d_model != null && hp.hidden_dim == null) {
                next.hiddenDim = String(hp.d_model);
              }
              return next;
            });
          }
          toast.message('Applying best hyperparams — starting full champion retrain…');
          void runTrainJob(strategy, activeSymbol, {
            hyperparams: hp,
            championTrain: true,
          });
        }}
      />

      <section className="ml-training__card">
        <div className="ml-training__card-head">
          <h4 className="ml-training__section-title">Model inventory</h4>
          <span className="ml-training__header-meta">
            {trainedCount}/{ML_STRATEGIES.length} trained · {activeSymbol || '—'}
          </span>
        </div>
        <ul className="ml-training__inventory">
          {inventory.map((row) => {
            const rowMeta = getStrategyMeta(row.strategy);
            const selected = row.strategy === strategy;
            return (
              <li key={row.strategy}>
                <button
                  type="button"
                  className={cn(
                    'ml-training__inventory-row',
                    selected && 'ml-training__inventory-row--active',
                  )}
                  onClick={() => setStrategy(row.strategy)}
                >
                  <span className="ml-training__inventory-icon" aria-hidden>
                    {row.trained
                      ? <CheckCircle2 size={14} className="text-emerald-400" />
                      : <XCircle size={14} className="text-muted-foreground/60" />}
                  </span>
                  <span className="ml-training__inventory-name">
                    {rowMeta.shortLabel || rowMeta.label}
                    <span className="text-muted-foreground font-normal"> · {row.strategy}</span>
                  </span>
                  <span
                    className="ml-training__inventory-meta num-mono"
                    title={row.error && !row.trained ? row.error : undefined}
                  >
                    {row.trained_at
                      ? new Date(row.trained_at).toLocaleDateString()
                      : (row.error ? 'status unavailable' : 'not trained')}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      <MlTrainRunsTable trainRuns={trainRuns} activeSymbol={activeSymbol} />

      <MlRetrainQueue
        retrainActions={retrainActions}
        retrainPending={retrainPending}
        retrainHistory={retrainHistory}
        runNowKey={runNowKey}
        training={training}
        validating={validating}
        busyElsewhere={busyElsewhere}
        onRunNow={handleRunNow}
      />

      <BatchTrainDialog
        open={batchOpen}
        onOpenChange={setBatchOpen}
        symbol={activeSymbol}
        timeframe={trainingTimeframe}
        trainingWindow={trainingWindow}
        inventory={inventory}
        busy={training || validating || busyElsewhere}
        initialScope={batchScope}
        onTrainStrategy={async (stratId) => {
          const ok = await runTrainJob(stratId, activeSymbol);
          if (!ok) throw new Error(`Training failed for ${stratId}`);
        }}
        onValidateStrategy={async (stratId) => {
          const ok = await handleValidate({ strategy: stratId });
          if (!ok) throw new Error(`Validation failed for ${stratId}`);
        }}
      />
    </div>
  );
}
