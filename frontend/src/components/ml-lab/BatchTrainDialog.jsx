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
  BATCH_SCOPES,
  countStrategiesForScope,
  selectStrategiesForScope,
} from '@/components/ml-lab/batchTrainScope';
import {
  formatBatchTrainSummary,
  runBatchTrainQueue,
} from '@/components/ml-lab/batchTrainRunner';
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
}) {
  const [scope, setScope] = useState(initialScope);
  const [selected, setSelected] = useState(() => (
    selectStrategiesForScope(inventory, initialScope)
  ));
  const [autoValidate, setAutoValidate] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState({ index: 0, total: 0, strategy: null });
  const cancelRef = useRef(false);
  // Per-strategy live progress from the training session (plan: "[▓▓▓░░] 45%").
  const mlSession = useSyncExternalStore(
    subscribeMlTrainingSession,
    getMlTrainingSession,
    getMlTrainingSession,
  );
  const activePct = (() => {
    if (!running || !progress.strategy) return null;
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
  useEffect(() => {
    if (!open) return;
    if (runningRef.current) return;
    setScope(initialScope);
    setSelected(selectStrategiesForScope(inventory, initialScope));
    setProgress({ index: 0, total: 0, strategy: null });
    cancelRef.current = false;
    // inventory captured at open / scope change only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialScope]);

  // Idle inventory load/refresh: keep non-custom scopes in sync (e.g. open before rows arrive).
  useEffect(() => {
    if (!open || runningRef.current) return;
    if (scope === 'custom') return;
    setSelected(selectStrategiesForScope(inventory, scope));
  }, [open, inventory, scope]);

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

  const handleCancel = () => {
    if (running) {
      cancelRef.current = true;
      toast.message('Cancel requested — finishing current strategy…');
      return;
    }
    onOpenChange?.(false);
  };

  const handleTrainSelected = async () => {
    if (!onTrainStrategy || busy || running) return;
    const queue = selectStrategiesForScope(inventory, scope === 'custom' ? 'custom' : scope, selected);
    if (!queue.length) {
      toast.error('No strategies selected');
      return;
    }

    cancelRef.current = false;
    setRunning(true);
    const summary = await runBatchTrainQueue({
      queue,
      onTrainStrategy,
      onValidateStrategy,
      autoValidate,
      shouldCancel: () => cancelRef.current,
      onProgress: setProgress,
      onStrategyError: (strategyId, err) => {
        toast.error(`${strategyId}: ${err?.message || 'Train failed'}`);
      },
    });

    setRunning(false);
    setProgress({ index: 0, total: 0, strategy: null });
    toast.message(formatBatchTrainSummary(summary));
    if (!summary.cancelled && summary.failed === 0) onOpenChange?.(false);
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
            const hintPct = isActiveTrain ? (activePct ?? 0) : null;
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
                    {formatTrainedHint(row, { trainingPct: hintPct })}
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

        <DialogFooter>
          <Button type="button" variant="outline" size="sm" onClick={handleCancel}>
            Cancel
          </Button>
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
