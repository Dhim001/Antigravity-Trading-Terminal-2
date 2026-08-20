/**
 * Batch Train dialog — queue multiple ML strategies sequentially.
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { toast } from 'sonner';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { getStrategyMeta } from '@/config/strategies';
import { ML_STRATEGIES, TRAINING_WINDOWS } from '@/components/ml-lab/MlLabConstants';
import { modelAgeHours } from '@/lib/modelHealth';
import {
  getMlTrainingSession,
  subscribeMlTrainingSession,
} from '@/lib/mlTrainingSession';
import {
  cancelMlBatch,
  cancelMlJob,
  retryMlBatch,
  submitMlBatchTrain,
} from '@/lib/mlLabApi';
import {
  BATCH_SCOPES,
  countStrategiesForScope,
  selectStrategiesForScope,
} from '@/components/ml-lab/batchTrainScope';
import {
  clearSavedBatchQueue,
  formatBatchTrainSummary,
  isMlJobCancelledError,
  readSavedBatchQueue,
  remainingAfterSummary,
  requestBatchCancel,
  runBatchTrainQueue,
  writeBatchQueueState,
} from '@/components/ml-lab/batchTrainRunner';
import {
  buildBatchItems,
  deriveServerProgress,
  isBatchApiUnavailableError,
  makeBatchIdempotencyKey,
  retryServerBatch,
  trySubmitServerBatch,
} from '@/components/ml-lab/batchTrainServerRunner';
import {
  getMlBatchTracker,
  startMlBatchTracking,
  subscribeMlBatchTracker,
} from '@/lib/mlBatchTracker';
import { BatchDetailsDrawer } from '@/components/ml-lab/BatchDetailsDrawer';
import {
  describeBatchItemError,
  diffNewBatchItemFailures,
  synthesizeLocalBatchSummary,
  truncateBatchError,
} from '@/components/ml-lab/batchItemStatus';
import { cn } from '@/lib/utils';

export { BATCH_SCOPES, selectStrategiesForScope };
export { formatBatchTrainSummary, runBatchTrainQueue };

function formatTrainedHint(row, { trainingPct = null } = {}) {
  if (trainingPct != null) {
    return `training ${Math.round(trainingPct)}%`;
  }
  if (!row?.trained) return 'not trained';
  const age = modelAgeHours(row);
  if (age == null) return 'trained';
  if (age < 1) return `trained ${Math.max(1, Math.round(age * 60))}m ago`;
  if (age < 48) return `trained ${Math.round(age)}h ago`;
  return `trained ${Math.round(age / 24)}d ago`;
}

function windowLabel(trainingWindow) {
  const hit = TRAINING_WINDOWS.find((w) => w.value === String(trainingWindow));
  return hit?.label || `${trainingWindow} months`;
}

function getBatchQueueStorage() {
  try {
    return typeof sessionStorage !== 'undefined' ? sessionStorage : null;
  } catch {
    return null;
  }
}

export default function BatchTrainDialog({
  open,
  onOpenChange,
  symbol,
  timeframe = '1m',
  trainingWindow = '3',
  inventory = [],
  busy = false,
  initialScope = 'untrained',
  onTrainStrategy,
  onValidateStrategy,
  configOverrides = null,
  onViewRuns,
}) {
  const [scope, setScope] = useState(initialScope);
  const [selected, setSelected] = useState(() => (
    selectStrategiesForScope(inventory, initialScope)
  ));
  const [autoValidate, setAutoValidate] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState({ index: 0, total: 0, strategy: null });
  const [failedIds, setFailedIds] = useState([]);
  const [resumeOffer, setResumeOffer] = useState(null);
  // Last polled server batch payload (drives the server status line + drawer).
  const [serverBatch, setServerBatch] = useState(null);
  // Synthesized batch-shaped summary for the legacy local-queue fallback, so
  // the Details drawer can show the last batch even without the server API.
  const [localBatchSummary, setLocalBatchSummary] = useState(null);
  const cancelRef = useRef(false);
  // Per-strategy knobs frozen at batch start (retry reuses the same snapshot).
  const configOverridesRef = useRef(null);
  // Active/finished server batch: { batchId, queue, configSnapshot, startedAt }.
  const serverBatchRef = useRef(null);
  // Previous polled batch payload — diffs surface per-item failure toasts once.
  const seenBatchRef = useRef(null);
  // strategy → raw error message for the local-queue path (drawer synthesis).
  const localErrorsRef = useRef({});
  // Per-strategy live progress from the training session (plan: "[▓▓▓░░] 45%").
  const mlSession = useSyncExternalStore(
    subscribeMlTrainingSession,
    getMlTrainingSession,
    getMlTrainingSession,
  );
  // Store-level server-batch tracking — survives dialog remounts/reloads.
  const tracker = useSyncExternalStore(
    subscribeMlBatchTracker,
    getMlBatchTracker,
    getMlBatchTracker,
  );
  const activePct = (() => {
    if (!running || !progress.strategy) return null;
    // Server batch: live per-item progress comes from the tracker's job poll.
    const followed = serverBatchRef.current?.batchId;
    if (followed && tracker.batchId === followed) {
      const n = Number(tracker.activeJobProgress?.pct);
      return Number.isFinite(n) ? n : null;
    }
    if (mlSession.strategy !== progress.strategy) return null;
    const raw = mlSession.serverProgress?.pct;
    if (raw == null || raw === '') return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  })();

  const runningRef = useRef(false);
  runningRef.current = running;

  // Init when dialog opens or Automation posts a new initialScope.
  // Never reset while a batch is in flight (inventory refreshAll must not
  // wipe running/progress/custom selection — that looked like a "stale" stuck UI).
  // serverBatch/localBatchSummary intentionally persist: the Details drawer
  // shows the last batch's items when idle.
  useEffect(() => {
    if (!open) return;
    if (runningRef.current) return;
    setScope(initialScope);
    setSelected(selectStrategiesForScope(inventory, initialScope));
    setProgress({ index: 0, total: 0, strategy: null });
    setFailedIds([]);
    serverBatchRef.current = null;
    cancelRef.current = false;
    // inventory captured at open / scope change only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialScope]);

  // Offer to resume a persisted in-progress queue (survives reload / detach).
  useEffect(() => {
    if (!open || runningRef.current) return;
    const saved = readSavedBatchQueue(getBatchQueueStorage());
    if (saved && (!saved.symbol || !symbol || saved.symbol === symbol)) {
      setResumeOffer(saved);
    } else {
      setResumeOffer(null);
    }
  }, [open, symbol]);

  // Idle inventory load/refresh: keep non-custom scopes in sync (e.g. open before rows arrive).
  useEffect(() => {
    if (!open || runningRef.current) return;
    if (scope === 'custom') return;
    setSelected(selectStrategiesForScope(inventory, scope));
  }, [open, inventory, scope]);

  // Re-attach to an in-flight server batch when the dialog (re)opens — the
  // tracker keeps polling across remounts, so the run is never lost.
  useEffect(() => {
    if (!open || runningRef.current) return;
    if (!tracker.active || !tracker.batchId) return;
    if (tracker.symbol && symbol && tracker.symbol !== String(symbol).toUpperCase()) return;
    serverBatchRef.current = {
      batchId: tracker.batchId,
      queue: tracker.meta?.queue || [],
      configSnapshot: tracker.meta?.configSnapshot || null,
      startedAt: tracker.meta?.startedAt || tracker.trackingSince || Date.now(),
    };
    // Suppress toasts for failures that happened before the attach.
    seenBatchRef.current = tracker.batch || null;
    setRunning(true);
    setFailedIds([]);
    if (tracker.batch) {
      setServerBatch(tracker.batch);
      setProgress(deriveServerProgress(tracker.batch));
    }
  }, [open, tracker.active, tracker.batchId, tracker.symbol, tracker.batch, tracker.meta, tracker.trackingSince, symbol]);

  // Server batch progress + finalize flow through the tracker (not a dialog-
  // owned poll) so a remount mid-run never abandons tracking.
  const followedBatchId = serverBatchRef.current?.batchId || null;
  useEffect(() => {
    if (!followedBatchId || tracker.batchId !== followedBatchId) return;
    const batch = tracker.batch;
    if (batch) {
      // Per-item failure toasts — diffed by item_id so each failure toasts
      // once, while a server retry (error → pending → error) re-toasts.
      const fresh = diffNewBatchItemFailures(seenBatchRef.current, batch);
      seenBatchRef.current = batch;
      for (const item of fresh) {
        const reason = describeBatchItemError(item?.error);
        const snippet = truncateBatchError(item?.error);
        toast.error(`${item?.strategy || 'Strategy'}: ${reason}`, {
          description: snippet && snippet !== reason ? snippet : undefined,
        });
      }
      setServerBatch(batch);
      setProgress(deriveServerProgress(batch));
    }
    if (tracker.terminal && runningRef.current) {
      if (tracker.terminal.status === 'lost') {
        setRunning(false);
        setProgress({ index: 0, total: 0, strategy: null });
        toast.error(`Server batch tracking lost: ${tracker.terminal.error || 'batch unavailable'}`);
        return;
      }
      const meta = tracker.meta || {};
      const followed = serverBatchRef.current || {};
      finalizeBatch(tracker.terminal, {
        queue: meta.queue || followed.queue || [],
        configSnapshot: meta.configSnapshot ?? followed.configSnapshot ?? null,
        startedAt: meta.startedAt || followed.startedAt || Date.now(),
      });
    }
    // finalizeBatch is stable enough for this flow (reads latest state via refs/setters)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tracker, followedBatchId]);

  const applyScope = useCallback((nextScope) => {
    setScope(nextScope);
    if (nextScope !== 'custom') {
      setSelected(selectStrategiesForScope(inventory, nextScope));
    }
  }, [inventory]);

  const toggleStrategy = (id, checked) => {
    setScope('custom');
    setSelected((prev) => {
      if (checked) return prev.includes(id) ? prev : [...prev, id];
      return prev.filter((x) => x !== id);
    });
  };

  const inventoryById = useMemo(() => {
    const m = new Map();
    for (const row of inventory || []) m.set(row.strategy, row);
    return m;
  }, [inventory]);

  const handleCancel = async () => {
    if (running) {
      // Soft-stop first so the queue never starts another strategy, then ask
      // the server to abort the in-flight job (true cancel).
      cancelRef.current = true;
      const serverBatchId = serverBatchRef.current?.batchId;
      if (serverBatchId) {
        // Server-side batch: one call cancels the active job + skips pending items.
        const { requested } = await requestBatchCancel({ jobId: serverBatchId, cancelJob: cancelMlBatch });
        toast.message(requested
          ? 'Cancel sent — stopping server batch…'
          : 'Cancel request failed — finishing current strategy…');
        return;
      }
      const jobId = getMlTrainingSession()?.jobId;
      if (jobId) {
        const { requested } = await requestBatchCancel({ jobId, cancelJob: cancelMlJob });
        toast.message(requested
          ? 'Cancel sent — stopping batch…'
          : 'Cancel request failed — finishing current strategy…');
      } else {
        toast.message('Cancel requested — finishing current strategy…');
      }
      return;
    }
    onOpenChange?.(false);
  };

  // Shared end-of-batch bookkeeping for the server and local-queue paths
  // (identical summary shapes — see summarizeServerBatch / runBatchTrainQueue).
  const finalizeBatch = (summary, { queue, configSnapshot, startedAt }) => {
    setRunning(false);
    setProgress({ index: 0, total: 0, strategy: null });
    setFailedIds(summary.failedIds);
    // Keep the drawer populated after the run: the server path leaves the
    // terminal payload in serverBatch; the local path synthesizes one.
    if (summary.server) {
      setLocalBatchSummary(null);
    } else {
      setLocalBatchSummary(synthesizeLocalBatchSummary({
        queue,
        summary,
        errors: localErrorsRef.current,
        symbol,
      }));
    }
    toast.message(formatBatchTrainSummary(summary));
    if (summary.stoppedEarly) {
      // Keep the remaining queue so a later dialog open offers Resume.
      writeBatchQueueState(getBatchQueueStorage(), {
        symbol: symbol || null,
        timeframe,
        trainingWindow,
        scope,
        queue,
        remaining: remainingAfterSummary(queue, summary),
        completed: summary.completed,
        failedIds: summary.failedIds,
        autoValidate,
        configOverrides: configSnapshot,
        startedAt,
      });
    } else {
      clearSavedBatchQueue(getBatchQueueStorage());
    }
    if (!summary.stoppedEarly && summary.failed === 0) onOpenChange?.(false);
  };

  const executeBatch = async (queue, configSnapshot) => {
    // Freeze per-strategy knobs for the whole batch so auto-validate uses the
    // same config as train, even after runTrainJob switches the Lab strategy.
    configOverridesRef.current = configSnapshot;
    cancelRef.current = false;
    setRunning(true);
    setFailedIds([]);
    setResumeOffer(null);
    setServerBatch(null);
    setLocalBatchSummary(null);
    seenBatchRef.current = null;
    localErrorsRef.current = {};

    const startedAt = Date.now();
    const finalize = (summary) => finalizeBatch(summary, { queue, configSnapshot, startedAt });

    // Preferred path: durable server-side batch (survives refresh). Falls back
    // to the local queue when the backend predates the batch API.
    try {
      const submission = await trySubmitServerBatch({
        symbol,
        items: buildBatchItems(queue, {
          configOverrides: configSnapshot,
          timeframe,
          trainingWindow,
          autoValidate,
        }),
        submit: submitMlBatchTrain,
        idempotencyKey: makeBatchIdempotencyKey(),
      });
      if (submission?.batchId) {
        serverBatchRef.current = { batchId: submission.batchId, queue, configSnapshot, startedAt };
        // The server owns durability now — no local resume entry needed.
        clearSavedBatchQueue(getBatchQueueStorage());
        // Store-level tracking: polling + finalize continue across dialog
        // remounts/reloads via the tracker effects above.
        startMlBatchTracking({
          batchId: submission.batchId,
          symbol,
          meta: { queue, configSnapshot, startedAt, autoValidate },
        });
        return;
      }
    } catch (err) {
      setRunning(false);
      toast.error(err?.message || 'Batch submit failed');
      return;
    }

    // Legacy fallback: frontend-driven queue (older backend).
    serverBatchRef.current = null;
    const done = { completed: [], failedIds: [] };
    const persist = (remaining) => {
      writeBatchQueueState(getBatchQueueStorage(), {
        symbol: symbol || null,
        timeframe,
        trainingWindow,
        scope,
        queue,
        remaining,
        completed: done.completed,
        failedIds: done.failedIds,
        autoValidate,
        configOverrides: configSnapshot,
        startedAt,
      });
    };
    persist(queue);

    const trackTrain = async (strategyId, config) => {
      try {
        await onTrainStrategy(strategyId, config);
        done.completed.push(strategyId);
      } catch (err) {
        // Cancelled items stay in `remaining` so a later resume re-runs them.
        if (!isMlJobCancelledError(err)) done.failedIds.push(strategyId);
        throw err;
      } finally {
        const attempted = new Set([...done.completed, ...done.failedIds]);
        persist(queue.filter((id) => !attempted.has(id)));
      }
    };

    const summary = await runBatchTrainQueue({
      queue,
      onTrainStrategy: trackTrain,
      onValidateStrategy,
      autoValidate,
      shouldCancel: () => cancelRef.current,
      configOverrides: configSnapshot,
      onProgress: setProgress,
      onStrategyError: (strategyId, err) => {
        const raw = err?.message || 'Train failed';
        localErrorsRef.current[strategyId] = raw;
        const reason = describeBatchItemError(raw);
        const snippet = truncateBatchError(raw);
        toast.error(`${strategyId}: ${reason}`, {
          description: snippet && snippet !== reason ? snippet : undefined,
        });
      },
      onStrategyCancelled: (strategyId) => {
        toast.message(`${strategyId}: cancelled`);
      },
    });

    finalize(summary);
  };

  const startBatch = async (queueIds, overrides = null) => {
    if (!onTrainStrategy || busy || runningRef.current) return;
    const queue = (Array.isArray(queueIds) ? queueIds : []).filter((id) => typeof id === 'string');
    if (!queue.length) {
      toast.error('No strategies selected');
      return;
    }
    await executeBatch(queue, overrides || configOverrides || null);
  };

  const handleTrainSelected = async () => {
    const queue = selectStrategiesForScope(inventory, scope === 'custom' ? 'custom' : scope, selected);
    await startBatch(queue);
  };

  const handleRetryFailed = async () => {
    if (!failedIds.length) return;
    const server = serverBatchRef.current;
    if (server?.batchId) {
      // Server batch: re-queue error/cancelled items in place, resume polling.
      cancelRef.current = false;
      setRunning(true);
      let retried;
      try {
        retried = await retryServerBatch({ batchId: server.batchId, retry: retryMlBatch });
      } catch (err) {
        const gone = isBatchApiUnavailableError(err)
          || /batch not found/i.test(String(err?.message || ''));
        if (!gone) {
          setRunning(false);
          toast.error(err?.message || 'Batch retry failed');
          return;
        }
        // Backend lost the batch (or was downgraded) — retry via local queue.
        serverBatchRef.current = null;
        toast.message('Server batch unavailable — retrying with local queue');
        await executeBatch(failedIds, configOverridesRef.current || configOverrides);
        return;
      }
      setFailedIds([]);
      setServerBatch(null);
      toast.message(`Retrying ${retried.requeued || failedIds.length} failed on server…`);
      startMlBatchTracking({
        batchId: server.batchId,
        symbol,
        meta: {
          queue: server.queue,
          configSnapshot: server.configSnapshot,
          startedAt: server.startedAt,
          autoValidate,
        },
      });
      return;
    }
    await startBatch(failedIds, configOverridesRef.current || configOverrides);
  };

  // Drawer "View runs": filter the dashboard's Recent runs table by batch.
  // The dialog closes when idle so the runs table is actually visible.
  const handleViewRuns = (batchId) => {
    if (!batchId) return;
    onViewRuns?.(batchId);
    if (!runningRef.current) onOpenChange?.(false);
  };

  const handleResumeBatch = async () => {
    const saved = resumeOffer;
    if (!saved) return;
    setResumeOffer(null);
    setAutoValidate(saved.autoValidate);
    toast.message(`Resuming batch — ${saved.remaining.length} strategies remaining`);
    await startBatch(saved.remaining, saved.configOverrides || configOverrides);
  };

  const handleDiscardResume = () => {
    clearSavedBatchQueue(getBatchQueueStorage());
    setResumeOffer(null);
  };

  const scopes = [
    { id: 'untrained', label: 'Untrained only', count: countStrategiesForScope(inventory, 'untrained') },
    { id: 'stale', label: 'Stale models (> 48h)', count: countStrategiesForScope(inventory, 'stale') },
    { id: 'all', label: 'All ML strategies', count: countStrategiesForScope(inventory, 'all') },
    { id: 'custom', label: 'Custom selection', count: selected.length },
  ];

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!running) onOpenChange?.(v); }}>
      <DialogContent className="batch-train-dialog sm:max-w-md" showCloseButton={!running}>
        <DialogHeader>
          <DialogTitle>
            Batch Train — {symbol || '—'} ({timeframe}, {windowLabel(trainingWindow)})
          </DialogTitle>
          <DialogDescription>
            Queue multiple ML strategies. Failures skip to the next strategy.
          </DialogDescription>
        </DialogHeader>

        {resumeOffer && !running && (
          <div className="batch-train-dialog__resume flex items-center justify-between gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-xs">
            <span>
              Resume previous batch?
              <span className="text-muted-foreground ml-1">
                {resumeOffer.remaining.length} remaining
                {resumeOffer.symbol ? ` · ${resumeOffer.symbol}` : ''}
              </span>
            </span>
            <span className="flex gap-1 shrink-0">
              <Button
                type="button"
                size="sm"
                className="h-6 text-[0.65rem]"
                onClick={handleResumeBatch}
              >
                Resume
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-6 text-[0.65rem]"
                onClick={handleDiscardResume}
              >
                Discard
              </Button>
            </span>
          </div>
        )}

        <fieldset className="batch-train-dialog__scopes space-y-1.5" disabled={running}>
          <legend className="text-[0.65rem] text-muted-foreground mb-1">Scope</legend>
          {scopes.map((s) => (
            <label key={s.id} className="flex items-center gap-2 text-xs cursor-pointer">
              <input
                type="radio"
                name="batch-scope"
                checked={scope === s.id}
                onChange={() => applyScope(s.id)}
                className="accent-primary"
              />
              <span>
                {s.label}
                <span className="text-muted-foreground ml-1">({s.count} strategies)</span>
              </span>
            </label>
          ))}
        </fieldset>

        <ul className="batch-train-dialog__list max-h-48 overflow-y-auto space-y-1 border rounded-md p-2">
          {ML_STRATEGIES.map((id) => {
            const row = inventoryById.get(id);
            const meta = getStrategyMeta(id);
            const checked = selected.includes(id);
            const isActiveTrain = running && progress.strategy === id;
            const hintPct = isActiveTrain ? activePct : null;
            return (
              <li key={id} className="flex items-center gap-2 text-xs">
                <Checkbox
                  checked={checked}
                  disabled={running}
                  onCheckedChange={(v) => toggleStrategy(id, Boolean(v))}
                  id={`batch-strat-${id}`}
                />
                <Label htmlFor={`batch-strat-${id}`} className="flex-1 cursor-pointer font-normal">
                  <span className="font-medium">{meta?.shortLabel || id}</span>
                  <span className="text-muted-foreground ml-2">
                    {isActiveTrain && hintPct == null
                      ? 'starting…'
                      : formatTrainedHint(row, { trainingPct: hintPct })}
                  </span>
                </Label>
              </li>
            );
          })}
        </ul>

        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <Checkbox
            checked={autoValidate}
            disabled={running || typeof onValidateStrategy !== 'function'}
            onCheckedChange={(v) => setAutoValidate(Boolean(v))}
          />
          Auto-validate after each train
        </label>

        {running && (
          <p className="text-[0.65rem] text-muted-foreground num-mono">
            Progress: Training {progress.index}/{progress.total}
            {progress.strategy
              ? `: ${getStrategyMeta(progress.strategy)?.shortLabel || progress.strategy}`
              : ''}
            {activePct != null ? ` ${Math.round(activePct)}%` : ''}
          </p>
        )}

        {running && serverBatch && (
          <p className="text-[0.65rem] text-muted-foreground num-mono">
            Server batch: {Number(serverBatch.completed) || 0}/{Number(serverBatch.total) || progress.total} done
            {` · ${Number(serverBatch.failed) || 0} failed · ${Number(serverBatch.cancelled) || 0} cancelled`}
            {serverBatch.stalled ? ' · stalled — restarting runner…' : ''}
            {tracker.pollErrors > 2 ? ' · reconnecting…' : ''}
          </p>
        )}

        {(serverBatch || localBatchSummary) && (
          <BatchDetailsDrawer
            batch={serverBatch || localBatchSummary}
            running={running}
            onViewRuns={handleViewRuns}
          />
        )}

        <DialogFooter>
          <Button type="button" variant="outline" size="sm" onClick={handleCancel}>
            Cancel
          </Button>
          {!running && failedIds.length > 0 && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={handleRetryFailed}
              title={`Re-run: ${failedIds.join(', ')}`}
            >
              Retry failed ({failedIds.length})
            </Button>
          )}
          <Button
            type="button"
            size="sm"
            disabled={busy || running || selected.length === 0}
            className={cn(running && 'opacity-80')}
            onClick={handleTrainSelected}
          >
            {running ? `Training ${progress.index}/${progress.total}` : 'Train Selected'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
