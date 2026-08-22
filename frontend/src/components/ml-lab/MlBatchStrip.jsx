/**
 * Persistent ML batch progress strip — rendered in the Model Training panel
 * whenever a server batch is active or recently finished. The Batch Train
 * dialog owns the detailed view; this strip is the minimized form of that
 * panel so the rest of the app stays usable during a run.
 */
import { useSyncExternalStore } from 'react';
import { Layers, Maximize2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { getStrategyMeta } from '@/config/strategies';
import { deriveServerProgress } from '@/components/ml-lab/batchTrainServerRunner';
import { formatBatchTrainSummary } from '@/components/ml-lab/batchTrainRunner';
import {
  dismissMlBatchTerminal,
  getMlBatchTracker,
  subscribeMlBatchTracker,
} from '@/lib/mlBatchTracker';
import { cn } from '@/lib/utils';

export default function MlBatchStrip({ onView }) {
  const tracker = useSyncExternalStore(
    subscribeMlBatchTracker,
    getMlBatchTracker,
    getMlBatchTracker,
  );

  if (!tracker.active && !tracker.terminal) return null;

  const batch = tracker.batch;
  const prog = deriveServerProgress(batch);
  const failed = Number(batch?.failed) || 0;
  const activeLabel = prog.strategy
    ? (getStrategyMeta(prog.strategy)?.shortLabel || prog.strategy)
    : null;
  const pct = Number(tracker.activeJobProgress?.pct);
  const pctText = tracker.active && Number.isFinite(pct) ? ` ${Math.round(pct)}%` : '';
  const stalled = Boolean(tracker.active && batch?.stalled);
  const reconnecting = tracker.active && tracker.pollErrors > 2;
  const barPct = prog.total > 0 ? Math.round((prog.index / prog.total) * 100) : 0;
  const canMaximize = typeof onView === 'function';

  if (tracker.terminal) {
    const lost = tracker.terminal.status === 'lost';
    return (
      <div className="ml-batch-strip ml-batch-strip--terminal" data-testid="ml-batch-strip">
        <Layers size={13} aria-hidden className="shrink-0" />
        <span className="ml-batch-strip__text" title={lost ? tracker.terminal.error : undefined}>
          {lost
            ? `Batch tracking lost — ${tracker.terminal.error || 'batch unavailable'}`
            : formatBatchTrainSummary(tracker.terminal)}
        </span>
        {!lost && tracker.terminal.batchId && canMaximize && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-[0.65rem]"
            onClick={() => onView(tracker.terminal.batchId)}
          >
            View
          </Button>
        )}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-6 px-1.5 text-[0.65rem] ml-batch-strip__dismiss"
          title="Dismiss batch summary"
          onClick={dismissMlBatchTerminal}
        >
          <X size={12} aria-hidden />
        </Button>
      </div>
    );
  }

  return (
    <div
      className={cn('ml-batch-strip', canMaximize && 'ml-batch-strip--interactive')}
      data-testid="ml-batch-strip"
      title={canMaximize ? 'Maximize batch train panel' : undefined}
      onClick={canMaximize ? () => onView(tracker.batchId) : undefined}
    >
      <Layers size={13} aria-hidden className="shrink-0" />
      <span className="ml-batch-strip__text num-mono">
        Batch {tracker.symbol || ''} — {prog.index}/{prog.total}
        {activeLabel ? ` · ${activeLabel}${pctText}` : ''}
        {failed ? ` · ${failed} failed` : ''}
        {stalled ? ' · stalled — restarting runner…' : ''}
        {reconnecting ? ' · reconnecting…' : ''}
      </span>
      <span
        className="ml-batch-strip__bar"
        role="progressbar"
        aria-valuenow={barPct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <span className="ml-batch-strip__bar-fill" style={{ width: `${barPct}%` }} />
      </span>
      {canMaximize && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-6 gap-1 px-2 text-[0.65rem]"
          title="Maximize batch train panel"
          onClick={(e) => {
            e.stopPropagation();
            onView(tracker.batchId);
          }}
        >
          <Maximize2 size={12} aria-hidden />
          Maximize
        </Button>
      )}
    </div>
  );
}
