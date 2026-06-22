/**
 * Walk-forward in-sample vs out-of-sample summary (single or rolling folds).
 */
import React from 'react';
import { cn } from '@/lib/utils';

function Metric({ label, value, tone }) {
  return (
    <div className="algo-backtest-wf__metric">
      <span className="text-muted-foreground">{label}</span>
      <strong className={cn('num-mono', tone)}>{value}</strong>
    </div>
  );
}

function FoldSummary({ fold }) {
  const is = fold.in_sample ?? {};
  const oos = fold.out_of_sample ?? {};
  const isSummary = is.summary ?? {};
  const oosSummary = oos.summary ?? {};

  return (
    <tr>
      <td className="num-mono text-center">{fold.fold ?? '—'}</td>
      <td className={cn(
        'num-mono text-right whitespace-nowrap',
        (is.total_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down',
      )}>
        {is.total_pnl != null ? `$${Number(is.total_pnl).toFixed(2)}` : '—'}
      </td>
      <td className={cn(
        'num-mono text-right whitespace-nowrap',
        (oos.total_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down',
      )}>
        {oos.total_pnl != null ? `$${Number(oos.total_pnl).toFixed(2)}` : '—'}
      </td>
      <td className="num-mono text-right">
        {oosSummary.sharpe_ratio != null ? Number(oosSummary.sharpe_ratio).toFixed(2) : '—'}
      </td>
      <td className="num-mono text-right">{oos.trade_count ?? oosSummary.total_trades ?? '—'}</td>
    </tr>
  );
}

export default function BacktestWalkForwardPanel({ walkForward }) {
  if (!walkForward) return null;

  const folds = walkForward.folds ?? [];
  const rolling = (walkForward.rolling_folds ?? 1) > 1;
  const aggregate = walkForward.aggregate ?? {};
  const is = walkForward.in_sample ?? {};
  const oos = walkForward.out_of_sample ?? {};
  const isSummary = is.summary ?? {};
  const oosSummary = oos.summary ?? {};

  return (
    <section className="algo-backtest-wf">
      <p className="algo-backtest-wf__title">
        {rolling
          ? `Rolling walk-forward (${walkForward.rolling_folds} folds, ${walkForward.train_pct ?? 70}% train per fold)`
          : `Walk-forward (${walkForward.train_pct ?? 70}% train → OOS test)`}
      </p>

      {rolling && folds.length > 0 && (
        <div className="algo-backtest-table-scroll overflow-x-auto mb-2">
          <table className="terminal-table algo-backtest-table m-0 text-xs">
            <thead>
              <tr>
                <th className="text-center">Fold</th>
                <th className="text-right">IS PnL</th>
                <th className="text-right">OOS PnL</th>
                <th className="text-right">OOS Sharpe</th>
                <th className="text-right">OOS Trades</th>
              </tr>
            </thead>
            <tbody>
              {folds.map((fold) => (
                <FoldSummary key={fold.fold} fold={fold} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {aggregate.fold_count > 0 && (
        <div className="algo-backtest-wf__aggregate mb-2 rounded border border-border/50 p-2 text-xs">
          <p className="font-semibold mb-1">Aggregate OOS ({aggregate.fold_count} folds)</p>
          <div className="flex flex-wrap gap-x-4 gap-y-1 num-mono">
            <span>
              Mean PnL:{' '}
              <strong className={(aggregate.mean_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down'}>
                {aggregate.mean_pnl != null ? `$${Number(aggregate.mean_pnl).toFixed(2)}` : '—'}
              </strong>
            </span>
            <span>
              Mean Sharpe:{' '}
              <strong>{aggregate.mean_sharpe != null ? Number(aggregate.mean_sharpe).toFixed(2) : '—'}</strong>
            </span>
            <span>
              Stability:{' '}
              <strong>
                {aggregate.stability_score != null
                  ? `${Math.round(Number(aggregate.stability_score) * 100)}% positive folds`
                  : '—'}
              </strong>
            </span>
          </div>
        </div>
      )}

      {!rolling && (
        <div className="algo-backtest-wf__grid">
          <div className="algo-backtest-wf__col">
            <p className="algo-backtest-wf__col-title">In-sample (optimized)</p>
            <Metric
              label="PnL"
              value={is.total_pnl != null ? `$${Number(is.total_pnl).toFixed(2)}` : '—'}
              tone={(is.total_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down'}
            />
            <Metric
              label="Win rate"
              value={isSummary.win_rate != null ? `${Number(isSummary.win_rate).toFixed(1)}%` : '—'}
            />
            <Metric
              label="Trades"
              value={String(is.trade_count ?? isSummary.total_trades ?? '—')}
            />
          </div>
          <div className="algo-backtest-wf__col">
            <p className="algo-backtest-wf__col-title">Out-of-sample (validated)</p>
            <Metric
              label="PnL"
              value={oos.total_pnl != null ? `$${Number(oos.total_pnl).toFixed(2)}` : '—'}
              tone={(oos.total_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down'}
            />
            <Metric
              label="Win rate"
              value={oosSummary.win_rate != null ? `${Number(oosSummary.win_rate).toFixed(1)}%` : '—'}
            />
            <Metric
              label="Trades"
              value={String(oos.trade_count ?? oosSummary.total_trades ?? '—')}
            />
          </div>
        </div>
      )}
    </section>
  );
}
