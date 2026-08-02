import { cn } from '@/lib/utils';
import {
  fmtMetric,
  metricLabel,
  pickMetricEntries,
} from '@/components/ml-lab/MlLabConstants';

export function MetricChips({ metrics }) {
  const entries = pickMetricEntries(metrics);
  if (!entries.length) return null;

  const trainAcc = metrics?.train_accuracy != null ? Number(metrics.train_accuracy) : null;
  const valAcc = metrics?.val_accuracy != null ? Number(metrics.val_accuracy) : (metrics?.accuracy != null ? Number(metrics.accuracy) : null);
  const gap = metrics?.overfitting_gap != null
    ? Number(metrics.overfitting_gap)
    : (trainAcc != null && valAcc != null ? Math.max(0, trainAcc - valAcc) : null);
  const risk = gap == null ? null : gap > 0.20 ? 'HIGH' : gap > 0.10 ? 'MEDIUM' : 'LOW';

  const hasClassAcc = metrics?.val_acc_buy != null || metrics?.val_acc_sell != null || metrics?.val_acc_none != null;

  return (
    <div className="ml-training__metrics-block">
      <div className="ml-training__metrics-head flex items-center justify-between">
        <h4 className="ml-training__section-title">Latest model metrics</h4>
        <div className="flex items-center gap-2">
          {metrics?.early_stopped && (
            <span
              className="px-2 py-0.5 rounded text-[10px] font-semibold num-mono bg-amber-500/15 text-amber-600 border border-amber-500/30"
              title={metrics.early_stop_reason || 'Validation loss stopped improving'}
            >
              Early stop
              {metrics.epochs_trained != null && metrics.epochs_budget != null
                ? ` ${metrics.epochs_trained}/${metrics.epochs_budget}`
                : ''}
            </span>
          )}
          {risk && (
          <span
            className={cn(
              'px-2 py-0.5 rounded text-[10px] font-semibold num-mono flex items-center gap-1',
              risk === 'HIGH' && 'bg-destructive/20 text-destructive border border-destructive/30',
              risk === 'MEDIUM' && 'bg-amber-500/20 text-amber-500 border border-amber-500/30',
              risk === 'LOW' && 'bg-emerald-500/20 text-emerald-500 border border-emerald-500/30',
            )}
            title="Overfitting risk calculated from train vs val accuracy gap"
          >
            Overfit risk: {risk} {gap != null ? `(${(gap * 100).toFixed(1)}% gap)` : ''}
          </span>
          )}
        </div>
      </div>

      {trainAcc != null && valAcc != null && (
        <div className="my-2 p-2 bg-muted/30 rounded border text-xs space-y-1">
          <div className="flex justify-between items-center text-[11px]">
            <span className="text-muted-foreground">Train vs Val Accuracy</span>
            <span className="num-mono font-medium">
              Train: {(trainAcc * 100).toFixed(1)}% | Val: {(valAcc * 100).toFixed(1)}%
            </span>
          </div>
          <div className="w-full bg-muted rounded-full h-1.5 overflow-hidden flex">
            <div
              className="bg-primary h-full transition-all"
              style={{ width: `${Math.min(100, trainAcc * 100)}%` }}
              title={`Train accuracy: ${(trainAcc * 100).toFixed(1)}%`}
            />
            <div
              className="bg-emerald-500 h-full transition-all -ml-full opacity-80"
              style={{ width: `${Math.min(100, valAcc * 100)}%` }}
              title={`Val accuracy: ${(valAcc * 100).toFixed(1)}%`}
            />
          </div>
        </div>
      )}

      {hasClassAcc && (
        <div className="my-2 grid grid-cols-3 gap-1.5 text-center text-[10px] num-mono">
          <div className="p-1 rounded bg-muted/40 border">
            <div className="text-muted-foreground">BUY Acc</div>
            <div className="font-semibold text-emerald-500">
              {metrics?.val_acc_buy != null ? `${(metrics.val_acc_buy * 100).toFixed(1)}%` : '—'}
            </div>
          </div>
          <div className="p-1 rounded bg-muted/40 border">
            <div className="text-muted-foreground">NONE Acc</div>
            <div className="font-semibold text-slate-400">
              {metrics?.val_acc_none != null ? `${(metrics.val_acc_none * 100).toFixed(1)}%` : '—'}
            </div>
          </div>
          <div className="p-1 rounded bg-muted/40 border">
            <div className="text-muted-foreground">SELL Acc</div>
            <div className="font-semibold text-rose-500">
              {metrics?.val_acc_sell != null ? `${(metrics.val_acc_sell * 100).toFixed(1)}%` : '—'}
            </div>
          </div>
        </div>
      )}

      <div className="ml-training__metrics">
        {entries.map(([k, v], i) => (
          <span
            key={k}
            className={cn('ml-training__metric-chip', i === 0 && 'ml-training__metric-chip--primary')}
            title={k}
          >
            <span className="ml-training__metric-key">{metricLabel(k)}</span>
            <strong className="num-mono">{fmtMetric(v, 3, k) ?? String(v)}</strong>
          </span>
        ))}
      </div>
    </div>
  );
}

export default MetricChips;
