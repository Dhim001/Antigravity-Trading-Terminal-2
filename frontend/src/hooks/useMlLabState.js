import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { toast } from 'sonner';
import { useStore } from '@/store/useStore';
import { isAbortError } from '@/api/client';
import { getStrategyMeta, isMlStrategy } from '@/config/strategies';
import {
  beginMlJob,
  clearMlJobProgress,
  finishMlJob,
  getCachedModelStatus,
  getMlTrainingSession,
  invalidateMatchingMlBacktests,
  markModelFreshAfterTrain,
  resolveModelStatusFetch,
  setMlServerProgress,
  setMlValidation,
  subscribeMlTrainingSession,
  appendMlPollLog,
} from '@/lib/mlTrainingSession';
import {
  formatMlJobBudgetLabel,
  isTransientMlPollError,
  mlJobPollDeadlineMs,
  mlJobPollIntervalMs,
  mlJobTimeoutMs,
} from '@/lib/mlJobTimeouts';
import {
  defaultAdvancedKnobs,
  ML_LAB_TF_KEY,
  ML_LAB_WINDOW_KEY,
  ML_STRATEGIES,
  preferredTrainingTimeframe,
  readStoredTrainingTimeframe,
  readStoredTrainingWindow,
  syncAdvancedForWindow,
  trainJobPhases,
  validateJobPhases,
} from '@/components/ml-lab/MlLabConstants';
import { POLL_LOG_PREF_KEY } from '@/components/ml-lab/MlJobProgress';
import {
  cancelMlJob,
  fetchMlInventory,
  fetchMlModelStatus,
  fetchMlQueueTelemetry,
  fetchMlRetrainQueue,
  fetchMlTrainRuns,
  pollMlJob,
} from '@/lib/mlLabApi';
import {
  deriveMlLabJobFlags,
  normalizeRetrainActions,
  normalizeRetrainPending,
  resolveMlLabSymbolOptions,
} from '@/hooks/mlLabStateHelpers';

export {
  deriveMlLabJobFlags,
  matchesRetrainTarget,
  normalizeRetrainActions,
  normalizeRetrainPending,
  resolveMlLabSymbolOptions,
  retrainQueueKey,
  sessionMatchesLab,
} from '@/hooks/mlLabStateHelpers';

export default function useMlLabState() {
  const activeSymbol = useStore((s) => s.activeSymbol);
  const symbolsList = useStore((s) => s.symbolsList);
  const setActiveSymbol = useStore((s) => s.setActiveSymbol);
  const botStrategy = useStore((s) => s.botStrategy);
  const botTimeframe = useStore((s) => s.botTimeframe);
  const mlSession = useSyncExternalStore(
    subscribeMlTrainingSession,
    getMlTrainingSession,
    getMlTrainingSession,
  );

  const [strategy, setStrategy] = useState(
    () => (isMlStrategy(botStrategy) ? botStrategy : 'ML_SIGNAL_BOOST'),
  );
  const [trainingWindow, setTrainingWindow] = useState(readStoredTrainingWindow);
  const [trainingTimeframe, setTrainingTimeframe] = useState(() => {
    const strat = isMlStrategy(botStrategy) ? botStrategy : 'ML_SIGNAL_BOOST';
    const tf = String(botTimeframe || '1m').toLowerCase();
    const botTf = tf === 'tick' ? '1m' : (tf || '1m');
    return preferredTrainingTimeframe(strat, botTf);
  });
  const [advanced, setAdvanced] = useState(() => {
    const strat = isMlStrategy(botStrategy) ? botStrategy : 'ML_SIGNAL_BOOST';
    const win = readStoredTrainingWindow();
    const tf = String(botTimeframe || '1m').toLowerCase();
    const botTf = tf === 'tick' ? '1m' : (tf || '1m');
    const timeframe = preferredTrainingTimeframe(strat, botTf);
    return syncAdvancedForWindow(
      defaultAdvancedKnobs(strat, 'train'),
      strat,
      win,
      timeframe,
    );
  });
  const [status, setStatus] = useState(null);
  const championOosRef = useRef(null);
  const [inventory, setInventory] = useState([]);
  const [retrainActions, setRetrainActions] = useState([]);
  const [retrainPending, setRetrainPending] = useState([]);
  const [retrainHistory, setRetrainHistory] = useState([]);
  const [runNowKey, setRunNowKey] = useState(null);
  const [cancellingJob, setCancellingJob] = useState(false);
  const [queueTelemetry, setQueueTelemetry] = useState({ active: 0, queued: 0 });
  const [trainRuns, setTrainRuns] = useState([]);
  // When set ({ batchId }), Recent runs filters to that batch's job ids.
  const [runsBatchFilter, setRunsBatchFilter] = useState(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const panelScrollRef = useRef(null);
  const [activatingVersionId, setActivatingVersionId] = useState(null);
  const [deletingVersionId, setDeletingVersionId] = useState(null);
  const [updatingVersionId, setUpdatingVersionId] = useState(null);
  const [challengerDismissed, setChallengerDismissed] = useState(false);
  const [showPollLog, setShowPollLog] = useState(() => {
    try {
      return window.localStorage.getItem(POLL_LOG_PREF_KEY) === '1';
    } catch {
      return false;
    }
  });
  const statusRef = useRef(status);
  statusRef.current = status;

  const {
    jobMatches,
    training,
    validating,
    jobProgress,
    serverProgress,
    pollLog,
    activeJobId,
    validation,
    busyElsewhere,
    sessionTuningHint,
  } = deriveMlLabJobFlags(mlSession, activeSymbol, strategy);

  const meta = getStrategyMeta(strategy);

  const startJobProgress = useCallback((kind, strat, symbol, months) => {
    const timeoutMs = mlJobTimeoutMs(strat, kind === 'train' ? 'train' : 'validate', {
      months,
    });
    const progress = {
      active: true,
      kind,
      startedAt: Date.now(),
      timeoutMs,
      label: kind === 'train'
        ? `Retraining ${getStrategyMeta(strat).shortLabel || strat}`
        : `Walk-forward${strat === 'RL_PPO_AGENT' ? '' : ' + PBO'} · ${getStrategyMeta(strat).shortLabel || strat}`,
      phases: kind === 'train' ? trainJobPhases(strat) : validateJobPhases(strat),
    };
    const next = beginMlJob({ kind, strategy: strat, symbol, jobProgress: progress });
    return next.jobToken;
  }, []);

  const finishTimersRef = useRef(new Set());

  useEffect(() => () => {
    for (const t of finishTimersRef.current) clearTimeout(t);
    finishTimersRef.current.clear();
  }, []);

  const finishJobProgress = useCallback((token, extras = {}) => {
    finishMlJob(token, extras);
    const t = window.setTimeout(() => {
      finishTimersRef.current.delete(t);
      clearMlJobProgress(token);
    }, 600);
    finishTimersRef.current.add(t);
  }, []);

  const fetchInventory = useCallback(async () => {
    if (!activeSymbol) {
      setInventory([]);
      return;
    }
    const rows = await fetchMlInventory(activeSymbol, ML_STRATEGIES, trainingTimeframe);
    setInventory(rows);
  }, [activeSymbol, trainingTimeframe]);

  const fetchRetrainQueue = useCallback(async () => {
    try {
      const body = await fetchMlRetrainQueue();
      setRetrainActions(normalizeRetrainActions(body?.retrain_actions));
      setRetrainPending(normalizeRetrainPending(body?.pending));
      setRetrainHistory(Array.isArray(body?.history) ? body.history : []);
    } catch (err) {
      if (!isAbortError(err)) {
        setRetrainActions([]);
        setRetrainPending([]);
        setRetrainHistory([]);
      }
    }
  }, []);

  const fetchQueueTelemetry = useCallback(async () => {
    try {
      const telemetry = await fetchMlQueueTelemetry();
      setQueueTelemetry(telemetry);
    } catch (err) {
      if (!isAbortError(err)) {
        /* keep last known */
      }
    }
  }, []);

  const fetchTrainRuns = useCallback(async () => {
    if (!activeSymbol) {
      setTrainRuns([]);
      return;
    }
    try {
      // Batch filter spans strategies — bypass strategy/timeframe narrowing.
      const runs = runsBatchFilter?.batchId
        ? await fetchMlTrainRuns(activeSymbol, null, null, { batchId: runsBatchFilter.batchId })
        : await fetchMlTrainRuns(activeSymbol, strategy, trainingTimeframe);
      setTrainRuns(runs);
    } catch (err) {
      if (!isAbortError(err)) setTrainRuns([]);
    }
  }, [activeSymbol, strategy, trainingTimeframe, runsBatchFilter]);

  // Batch filters are symbol-scoped — switching symbol would only show an
  // empty table, so the Lab symbol setter drops the filter alongside.
  const setLabActiveSymbol = useCallback((sym) => {
    setRunsBatchFilter(null);
    setActiveSymbol(sym);
  }, [setActiveSymbol]);

  const fetchStatus = useCallback(async ({ quiet = false } = {}) => {
    if (!activeSymbol || !strategy) return;
    if (!quiet) setLoading(true);
    try {
      const body = await fetchMlModelStatus(activeSymbol, strategy, trainingTimeframe);
      const next = resolveModelStatusFetch(activeSymbol, strategy, {
        body,
        previous: statusRef.current,
        timeframe: trainingTimeframe,
      });
      setStatus(next);
    } catch (err) {
      const next = resolveModelStatusFetch(activeSymbol, strategy, {
        error: err,
        previous: statusRef.current,
        timeframe: trainingTimeframe,
      });
      setStatus(next);
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [activeSymbol, strategy, trainingTimeframe]);

  const refreshAll = useCallback(async ({
    clearSessionValidation = false,
    quiet = false,
    preserveScroll = false,
  } = {}) => {
    const scroller = panelScrollRef.current;
    const scrollTop = preserveScroll && scroller ? scroller.scrollTop : null;
    if (clearSessionValidation) {
      setMlValidation(null);
      setChallengerDismissed(true);
      championOosRef.current = null;
    }
    await Promise.all([
      fetchStatus({ quiet }),
      fetchInventory(),
      fetchRetrainQueue(),
      fetchQueueTelemetry(),
      fetchTrainRuns(),
    ]);
    if (scrollTop != null && scroller) {
      requestAnimationFrame(() => {
        scroller.scrollTop = scrollTop;
      });
    }
  }, [fetchStatus, fetchInventory, fetchRetrainQueue, fetchQueueTelemetry, fetchTrainRuns]);

  const handleManualRefresh = useCallback(async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      await refreshAll({
        clearSessionValidation: false,
        quiet: true,
        preserveScroll: true,
      });
    } finally {
      setRefreshing(false);
    }
  }, [refreshAll, refreshing]);

  useEffect(() => {
    if (isMlStrategy(botStrategy) && botStrategy !== strategy) {
      setStrategy(botStrategy);
    }
  }, [botStrategy]); // eslint-disable-line react-hooks/exhaustive-deps -- sync bot picker → dashboard

  const lastBotTfRef = useRef(null);
  useEffect(() => {
    const tf = String(botTimeframe || '1m').toLowerCase();
    if (!tf || tf === 'tick') return;
    if (lastBotTfRef.current === null) {
      lastBotTfRef.current = tf;
      return;
    }
    if (lastBotTfRef.current === tf) return;
    lastBotTfRef.current = tf;
    setTrainingTimeframe(tf);
  }, [botTimeframe]);

  useEffect(() => {
    setAdvanced((prev) => syncAdvancedForWindow(
      defaultAdvancedKnobs(strategy, 'train'),
      strategy,
      trainingWindow,
      trainingTimeframe,
    ));
    // trainingWindow/TF intentionally omitted — window effect owns those syncs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategy]);

  useEffect(() => {
    setAdvanced((prev) => syncAdvancedForWindow(
      prev,
      strategy,
      trainingWindow,
      trainingTimeframe,
    ));
    try {
      window.localStorage.setItem(ML_LAB_WINDOW_KEY, String(trainingWindow));
      window.localStorage.setItem(ML_LAB_TF_KEY, String(trainingTimeframe));
    } catch {
      /* ignore */
    }
  }, [trainingWindow, trainingTimeframe, strategy]);

  useEffect(() => {
    const cached = getCachedModelStatus(activeSymbol, strategy, trainingTimeframe);
    setStatus(cached);
    refreshAll({ quiet: true, preserveScroll: true });
  }, [refreshAll, trainingTimeframe, activeSymbol, strategy]);

  useEffect(() => {
    const id = window.setInterval(() => {
      fetchQueueTelemetry();
    }, 5_000);
    return () => window.clearInterval(id);
  }, [fetchQueueTelemetry]);

  const localJobWaiterRef = useRef(false);
  useEffect(() => {
    if (!jobMatches) return undefined;
    if (!mlSession.training && !mlSession.validating) return undefined;
    const jobId = mlSession.jobId;
    if (!jobId) {
      const id = window.setInterval(() => {
        fetchStatus();
      }, 15_000);
      return () => window.clearInterval(id);
    }
    if (localJobWaiterRef.current) return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const body = await pollMlJob(jobId);
        const job = body?.job;
        if (cancelled || !job) return;
        if (job.progress) setMlServerProgress({ ...job.progress, status: job.status });
        if (job.status === 'done' || job.status === 'error' || job.status === 'cancelled') {
          if (job.kind === 'validate' && job.result) setMlValidation(job.result);
          if (job.status === 'done' && job.kind === 'train') {
            const doneStrat = job.strategy || mlSession.strategy;
            const doneSym = job.symbol || mlSession.symbol;
            invalidateMatchingMlBacktests(doneStrat, doneSym);
            markModelFreshAfterTrain(
              doneSym,
              doneStrat,
              job.result?.timeframe || trainingTimeframe,
            );
          }
          finishMlJob(mlSession.jobToken, {
            validation: job.kind === 'validate' ? job.result : undefined,
            error: job.status === 'error' ? (job.error || 'failed') : null,
          });
          fetchStatus();
        }
      } catch {
        appendMlPollLog({
          status: 'running',
          phase: 'waiting',
          detail: 'server busy — still polling…',
          note: 'poll_err',
        });
      }
    };
    tick();
    const id = window.setInterval(tick, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [
    jobMatches,
    mlSession.training,
    mlSession.validating,
    mlSession.jobId,
    mlSession.jobToken,
    mlSession.strategy,
    mlSession.symbol,
    trainingTimeframe,
    fetchStatus,
  ]);

  const pollMlJobUntilDone = useCallback(async (jobId, { strategy: strat, kind = 'train', months } = {}) => {
    const terminal = new Set(['done', 'error', 'cancelled']);
    const winMonths = months ?? trainingWindow;
    const budgetMs = mlJobPollDeadlineMs(strat || strategy, kind, { months: winMonths });
    const started = Date.now();
    const deadline = started + budgetMs;
    let transientStreak = 0;
    let warnedTransient = false;
    let warnedPastBudget = false;
    for (;;) {
      const pastBudget = Date.now() >= deadline;
      if (pastBudget && !warnedPastBudget) {
        warnedPastBudget = true;
        toast.message(
          `Still ${kind === 'train' ? 'training' : 'validating'} past ${formatMlJobBudgetLabel(budgetMs)} — progress stays open`,
        );
      }
      try {
        const body = await pollMlJob(jobId);
        transientStreak = 0;
        const job = body?.job;
        if (!job) throw new Error('ML job not found');
        if (job.progress) setMlServerProgress({ ...job.progress, status: job.status });
        if (terminal.has(job.status)) return job;
      } catch (err) {
        if (!isTransientMlPollError(err)) throw err;
        transientStreak += 1;
        const prev = getMlTrainingSession().serverProgress || {};
        setMlServerProgress({
          pct: Number(prev.pct) || 0,
          phase: prev.phase || 'waiting',
          detail: 'server busy — still polling…',
          status: prev.status || 'running',
          note: 'poll_err',
        });
        if (!warnedTransient) {
          warnedTransient = true;
          toast.message('Job status briefly unreachable — keeping progress open and retrying…');
        }
        const backoff = Math.min(15_000, 2_000 * transientStreak);
        await new Promise((r) => setTimeout(r, backoff));
        continue;
      }
      const elapsed = Date.now() - started;
      const interval = pastBudget
        ? Math.max(8_000, mlJobPollIntervalMs(elapsed, budgetMs))
        : mlJobPollIntervalMs(elapsed, budgetMs);
      await new Promise((r) => setTimeout(r, interval));
    }
  }, [strategy, trainingWindow]);

  const handleCancelJob = useCallback(async () => {
    const jobId = getMlTrainingSession().jobId;
    if (cancellingJob) return;
    if (!jobId) {
      toast.message('Job is still starting — tap Cancel again in a moment');
      return;
    }
    setCancellingJob(true);
    try {
      const body = await cancelMlJob(jobId);
      if (body?.ok) {
        toast.message(body.immediate ? 'Job cancelled' : 'Cancel requested — finishing current step…');
      } else {
        toast.error(body?.error || 'Cancel failed');
      }
    } catch (err) {
      if (!isAbortError(err)) toast.error(err.message || 'Cancel failed');
    } finally {
      setCancellingJob(false);
    }
  }, [cancellingJob]);

  const symbolOptions = resolveMlLabSymbolOptions(symbolsList, activeSymbol);

  return {
    activeSymbol,
    symbolsList,
    symbolOptions,
    setActiveSymbol: setLabActiveSymbol,
    botStrategy,
    botTimeframe,
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
    runsBatchFilter,
    setRunsBatchFilter,
    queueTelemetry,
    loading,
    refreshing,
    activatingVersionId,
    setActivatingVersionId,
    deletingVersionId,
    setDeletingVersionId,
    updatingVersionId,
    setUpdatingVersionId,
    showPollLog,
    setShowPollLog,
    challengerDismissed,
    setChallengerDismissed,
    runNowKey,
    setRunNowKey,
    cancellingJob,
    setCancellingJob,
    championOosRef,
    panelScrollRef,
    localJobWaiterRef,
    statusRef,
    finishTimersRef,
    jobMatches,
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
    fetchInventory,
    fetchRetrainQueue,
    fetchQueueTelemetry,
    fetchTrainRuns,
    fetchStatus,
    refreshAll,
    handleManualRefresh,
    pollMlJobUntilDone,
    handleCancelJob,
  };
}

export { useMlLabState };
