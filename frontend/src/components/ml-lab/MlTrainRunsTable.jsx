import { useVirtualRows, VirtualTablePadding } from '@/components/VirtualTableBody';
import { cn } from '@/lib/utils';
import { formatDurationMs } from '@/components/ml-lab/MlLabConstants';
import {
  runModelLabel,
  runResultLabel,
  runTitle,
  runVersionParts,
} from '@/lib/mlTrainRunsDisplay';

export function MlTrainRunsTable({ trainRuns, activeSymbol, versions = [] }) {
  const { onScroll: onRunsScroll, window: runsWindow } = useVirtualRows(trainRuns, {
    rowHeight: 46,
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
                <th>Model</th>
                <th>Version</th>
                <th>Kind</th>
                <th>TF</th>
                <th>Result</th>
                <th className="text-right">Duration</th>
              </tr>
            </thead>
            <tbody>
              <VirtualTablePadding height={runsWindow.topPad} colSpan={7} />
              {runsWindow.slice.map((run) => {
                const model = runModelLabel(run);
                const version = runVersionParts(run, versions);
                return (
                  <tr key={run.id} title={runTitle(run)}>
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
                    <td>
                      <div className="ml-training__runs-model">
                        <span className="ml-training__runs-model-name">{model}</span>
                        <span className="ml-training__runs-model-meta num-mono text-muted-foreground">
                          {[run.symbol || activeSymbol, run.artifact].filter(Boolean).join(' · ') || '—'}
                        </span>
                      </div>
                    </td>
                    <td title={run.version_id || version.name || ''}>
                      <div className="ml-training__runs-version">
                        {version.name ? (
                          <span className="ml-training__runs-version-name">{version.name}</span>
                        ) : null}
                        {version.id ? (
                          <span className="ml-training__runs-version-id num-mono text-muted-foreground">
                            {version.id}
                          </span>
                        ) : (
                          <span className="ml-training__runs-version-id num-mono text-muted-foreground">
                            {version.emptyLabel}
                          </span>
                        )}
                      </div>
                    </td>
                    <td>{run.kind || '—'}</td>
                    <td className="num-mono text-muted-foreground">{run.timeframe || '—'}</td>
                    <td className={cn(
                      'num-mono',
                      run.ok ? 'text-emerald-400' : 'text-destructive',
                    )}
                    >
                      {runResultLabel(run)}
                    </td>
                    <td className="num-mono text-right">
                      {formatDurationMs(run.duration_ms)}
                    </td>
                  </tr>
                );
              })}
              <VirtualTablePadding height={runsWindow.bottomPad} colSpan={7} />
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
