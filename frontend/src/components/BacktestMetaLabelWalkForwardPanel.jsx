import React from 'react';
import { cn } from '@/lib/utils';

function Metric({ label, value, tone }) {
  return (
    <div className="rounded-md border border-border/50 px-2 py-1.5">
      <span className="block text-[0.62rem] text-muted-foreground">{label}</span>
      <strong className={cn(
        'text-xs num-mono',
        tone === 'up' && 'text-trading-up',
        tone === 'down' && 'text-trading-down',
      )}>
        {value}
      </strong>
    </div>
  );
}

export default function BacktestMetaLabelWalkForwardPanel({ walkForward, className }) {
  if (!walkForward) return null;

  if (!walkForward.ok) {
    return (
      <section className={cn('algo-backtest-wf', className)}>
        <header className="text-xs font-medium mb-1">Meta-label walk-forward</header>
        <p className="text-[0.65rem] text-muted-foreground m-0">
          {walkForward.error || 'Evaluation did not complete'}
        </p>
      </section>
    );
  }

  const agg = walkForward.aggregate || {};
  const base = agg.baseline_oos_avg || {};
  const gbm = agg.gbm_oos_avg || {};
  const delta = agg.gbm_vs_baseline_avg || {};

  return (
    <section className={cn('algo-backtest-wf flex flex-col gap-2', className)}>
      <header className="algo-backtest-wf__title-wrap">
        <span className="algo-backtest-wf__title">Meta-label walk-forward (OOS)</span>
        <p className="text-[0.62rem] text-muted-foreground m-0 mt-0.5">
          GBM trained on in-sample trades, gate applied out-of-sample vs no gate.
        </p>
      </header>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Metric label="Baseline PnL" value={`$${Number(base.total_pnl ?? 0).toFixed(2)}`} />
        <Metric label="GBM PnL" value={`$${Number(gbm.total_pnl ?? 0).toFixed(2)}`} tone={(delta.total_pnl ?? 0) >= 0 ? 'up' : 'down'} />
        <Metric label="Baseline trades" value={String(Math.round(base.total_trades ?? 0))} />
        <Metric label="GBM trades" value={String(Math.round(gbm.total_trades ?? 0))} />
      </div>

      {walkForward.recommendation && (
        <p className="text-[0.65rem] leading-snug m-0">{walkForward.recommendation}</p>
      )}

      {(walkForward.folds || []).length >= 1 && (
        <div className="overflow-x-auto">
          <table className="terminal-table m-0 w-full text-[0.62rem]">
            <thead>
              <tr>
                <th>Fold</th>
                <th className="text-right">Train N</th>
                <th className="text-right">Val AUC</th>
                <th className="text-right">Base PnL</th>
                <th className="text-right">GBM PnL</th>
              </tr>
            </thead>
            <tbody>
              {walkForward.folds.map((f) => (
                <tr key={f.fold}>
                  <td>{f.fold}</td>
                  <td className="text-right num-mono">{f.train_trades ?? '—'}</td>
                  <td className="text-right num-mono">
                    {f.train_val_auc != null ? `${(f.train_val_auc * 100).toFixed(1)}%` : '—'}
                  </td>
                  <td className="text-right num-mono">
                    ${Number(f.baseline_oos?.total_pnl ?? 0).toFixed(2)}
                  </td>
                  <td className="text-right num-mono">
                    {f.gbm_oos ? `$${Number(f.gbm_oos.total_pnl ?? 0).toFixed(2)}` : f.gbm_error || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
