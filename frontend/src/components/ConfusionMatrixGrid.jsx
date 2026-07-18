/**
 * 3×3 classification confusion matrix with per-class metrics.
 *
 * @param {{
 *   matrix?: number[][];
 *   labels?: string[];
 *   className?: string;
 * }} props
 */
import { cn } from '@/lib/utils';

const DEFAULT_LABELS = ['BUY', 'SELL', 'NONE'];

function classMetrics(matrix, idx) {
  const n = matrix.length;
  let tp = Number(matrix[idx]?.[idx] ?? 0);
  let fp = 0;
  let fn = 0;
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const v = Number(matrix[i]?.[j] ?? 0);
      if (i === idx && j !== idx) fn += v;
      if (i !== idx && j === idx) fp += v;
    }
  }
  const precision = tp + fp > 0 ? tp / (tp + fp) : 0;
  const recall = tp + fn > 0 ? tp / (tp + fn) : 0;
  const f1 = precision + recall > 0 ? (2 * precision * recall) / (precision + recall) : 0;
  return { precision, recall, f1, tp };
}

export function computeConfusionStats(matrix, labels = DEFAULT_LABELS) {
  const m = matrix || [];
  let total = 0;
  let correct = 0;
  for (let i = 0; i < m.length; i++) {
    for (let j = 0; j < (m[i]?.length ?? 0); j++) {
      const v = Number(m[i][j] ?? 0);
      total += v;
      if (i === j) correct += v;
    }
  }
  const accuracy = total > 0 ? correct / total : 0;
  const perClass = labels.map((label, idx) => ({
    label,
    ...classMetrics(m, idx),
  }));
  return { accuracy, total, perClass };
}

export default function ConfusionMatrixGrid({
  matrix = [],
  labels = DEFAULT_LABELS,
  className,
}) {
  const stats = computeConfusionStats(matrix, labels);
  const flat = (matrix || []).flat().map(Number);
  const maxCell = Math.max(...flat, 1);

  if (!matrix?.length) {
    return (
      <p className={cn('confusion-matrix confusion-matrix--empty text-xs text-muted-foreground', className)}>
        No confusion matrix data.
      </p>
    );
  }

  return (
    <div className={cn('confusion-matrix', className)}>
      <div className="confusion-matrix__badge">
        <span className="text-[0.55rem] uppercase text-muted-foreground">Accuracy</span>
        <span className="num-mono text-sm font-semibold">
          {(stats.accuracy * 100).toFixed(1)}%
        </span>
      </div>
      <div
        className="confusion-matrix__grid"
        style={{ gridTemplateColumns: `auto repeat(${labels.length}, minmax(2.5rem, 1fr))` }}
        role="table"
        aria-label="Confusion matrix"
      >
        <div className="confusion-matrix__corner" aria-hidden />
        {labels.map((lab) => (
          <div key={`pred-${lab}`} className="confusion-matrix__col-label">
            {lab}
          </div>
        ))}
        {labels.map((rowLab, i) => (
          <div key={`row-${rowLab}`} className="confusion-matrix__row-group contents">
            <div className="confusion-matrix__row-label">{rowLab}</div>
            {labels.map((colLab, j) => {
              const v = Number(matrix[i]?.[j] ?? 0);
              const intensity = v / maxCell;
              const diagonal = i === j;
              return (
                <div
                  key={`${rowLab}-${colLab}`}
                  className={cn(
                    'confusion-matrix__cell num-mono',
                    diagonal ? 'confusion-matrix__cell--ok' : 'confusion-matrix__cell--err',
                  )}
                  style={{ '--cell-intensity': intensity }}
                  title={`Actual ${rowLab} → Predicted ${colLab}: ${v}`}
                >
                  {v}
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <p className="confusion-matrix__axis-hint text-[0.5rem] text-muted-foreground mt-1">
        Rows = actual · Columns = predicted
      </p>
      <div className="confusion-matrix__metrics">
        {stats.perClass.map((c) => (
          <div key={c.label} className="confusion-matrix__metric">
            <span className="confusion-matrix__metric-label">{c.label}</span>
            <span className="num-mono text-[0.6rem] text-muted-foreground">
              P {(c.precision * 100).toFixed(0)}%
              {' · '}
              R {(c.recall * 100).toFixed(0)}%
              {' · '}
              F1 {(c.f1 * 100).toFixed(0)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
