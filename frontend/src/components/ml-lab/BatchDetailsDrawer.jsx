/**
 * Batch Details drawer — live per-item table for the Batch Train dialog.
 *
 * Renders the last polled server batch (or a synthesized local summary) as a
 * collapsible table: strategy, status, duration, error (truncated, tooltip)
 * and a per-item "Run" action that filters the main runs table by batch.
 */
import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, ListFilter } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { getStrategyMeta } from '@/config/strategies';
import { formatDurationMs } from '@/components/ml-lab/MlLabConstants';
import { buildBatchDrawerRows } from '@/components/ml-lab/batchItemStatus';
import { cn } from '@/lib/utils';

const STATUS_TONE = {
  pending: 'text-muted-foreground',
  running: 'text-sky-400',
  done: 'text-emerald-400',
  error: 'text-destructive',
  cancelled: 'text-amber-400',
  skipped: 'text-muted-foreground',
};

function shortBatchId(batchId) {
  const id = String(batchId || '');
  return id.length > 8 ? `${id.slice(0, 8)}…` : id;
}

export function BatchDetailsDrawer({
  batch = null,
  running = false,
  defaultOpen = false,
  onViewRuns,
}) {
  const [open, setOpen] = useState(defaultOpen);
  const rows = useMemo(() => buildBatchDrawerRows(batch), [batch]);
  if (!batch || rows.length === 0) return null;

  const failedCount = rows.filter((r) => r.status === 'error').length;
  const batchId = batch.batch_id || null;
  const canViewRuns = Boolean(batchId) && typeof onViewRuns === 'function';

  return (
    <div className="batch-train-dialog__details rounded-md border border-border/60">
      <div className="flex items-center gap-1 px-2 py-1">
        <button
          type="button"
          className="flex flex-1 items-center gap-1 text-left text-[0.65rem] text-muted-foreground hover:text-foreground"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {open ? <ChevronDown size={12} aria-hidden /> : <ChevronRight size={12} aria-hidden />}
          <span className="font-medium">Details</span>
          <span className="num-mono">
            {rows.length} items
            {failedCount ? ` · ${failedCount} failed` : ''}
            {batchId ? ` · batch ${shortBatchId(batchId)}` : ''}
            {batch.local ? ' · local queue' : ''}
          </span>
        </button>
        {canViewRuns && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-6 px-1.5 text-[0.65rem] gap-1 shrink-0"
            title="Show this batch's runs in the Recent runs table"
            onClick={() => onViewRuns(batchId)}
          >
            <ListFilter size={11} aria-hidden />
            View runs
          </Button>
        )}
      </div>
      {open && (
        <div className="max-h-44 overflow-y-auto border-t border-border/60">
          <table className="w-full text-[0.65rem]">
            <thead>
              <tr className="text-muted-foreground">
                <th className="px-2 py-1 text-left font-medium">Strategy</th>
                <th className="px-2 py-1 text-left font-medium">Status</th>
                <th className="px-2 py-1 text-right font-medium">Duration</th>
                <th className="px-2 py-1 text-left font-medium">Error</th>
                <th className="px-2 py-1 text-right font-medium">Run</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key} className="border-t border-border/30">
                  <td className="px-2 py-1">
                    <span className="font-medium">
                      {getStrategyMeta(row.strategy)?.shortLabel || row.strategy || '—'}
                    </span>
                  </td>
                  <td className={cn('px-2 py-1 num-mono', STATUS_TONE[row.status])}>
                    {row.status}
                    {running && row.status === 'running' ? '…' : ''}
                  </td>
                  <td className="px-2 py-1 text-right num-mono text-muted-foreground">
                    {row.durationMs != null ? formatDurationMs(row.durationMs) : '—'}
                  </td>
                  <td className="px-2 py-1 max-w-40 truncate text-muted-foreground" title={row.errorFull || undefined}>
                    {row.status === 'error'
                      ? (row.errorHint || row.error || 'training failed')
                      : (row.error || '—')}
                  </td>
                  <td className="px-2 py-1 text-right">
                    {canViewRuns && row.jobId ? (
                      <button
                        type="button"
                        className="text-primary hover:underline"
                        title="View this item's run in Recent runs"
                        onClick={() => onViewRuns(batchId)}
                      >
                        run
                      </button>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default BatchDetailsDrawer;
