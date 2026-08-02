import { useVirtualRows, VirtualTablePadding } from '@/components/VirtualTableBody';
import { cn } from '@/lib/utils';
import { fmtMetric, formatDurationMs } from '@/components/ml-lab/MlLabConstants';

export function MlTrainRunsTable({ trainRuns, activeSymbol }) {
  const { onScroll: onRunsScroll, window: runsWindow } = useVirtualRows(trainRuns, {
    rowHeight: 32,
    overscan: 6,
  });

  return (
    <section className="ml-training__card">
      <div className="ml-training__card-head">
        <h4 className="ml-training__section-title">Recent runs</h4>
        <span className="ml-training__header-meta">
          {trainRuns.length} · {activeSymbol || '—'}
        </span>
      </div>
      {trainRuns.length === 0 ? (
        <p className="text-[0.65rem] text-muted-foreground">
          No train/validate history yet for this symbol/strategy.
        </p>
      ) : (
        <div className="ml-training__runs-scroll" onScroll={onRunsScroll}>
          <table className="ml-training__runs-table">
            <thead>
              <tr>
                <th>When</th>
                <th>Kind</th>
                <th>TF</th>
                <th>Result</th>
                <th className="text-right">Duration</th>
              </tr>
            </thead>
            <tbody>
              <VirtualTablePadding height={runsWindow.topPad} colSpan={5} />
              {runsWindow.slice.map((run) => {
                const metricHint = run.metrics?.mean_oos_accuracy
                  ?? run.metrics?.mean_accuracy
                  ?? run.metrics?.val_accuracy
                  ?? run.metrics?.pbo;
                return (
                  <tr key={run.id} title={run.error || run.version_id || ''}>
                    <td className="num-mono">
                      {run.finished_at
                        ? new Date(run.finished_at).toLocaleString(undefined, {
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })
                        : '—'}
                    </td>
                    <td>{run.kind || '—'}</td>
                    <td className="num-mono text-muted-foreground">{run.timeframe || '—'}</td>
                    <td className={cn(
                      'num-mono',
                      run.ok ? 'text-emerald-400' : 'text-destructive',
                    )}
                    >
                      {run.ok ? 'ok' : (run.error === 'cancelled' ? 'cancelled' : 'fail')}
                      {metricHint != null
                        ? ` · ${fmtMetric(metricHint, 3, 'mean_oos_accuracy') ?? metricHint}`
                        : ''}
                    </td>
                    <td className="num-mono text-right">
                      {formatDurationMs(run.duration_ms)}
                    </td>
                  </tr>
                );
              })}
              <VirtualTablePadding height={runsWindow.bottomPad} colSpan={5} />
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
