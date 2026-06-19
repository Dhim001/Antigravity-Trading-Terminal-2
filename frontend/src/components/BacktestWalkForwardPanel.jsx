/**
 * Walk-forward in-sample vs out-of-sample summary (P4).
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

export default function BacktestWalkForwardPanel({ walkForward }) {
  if (!walkForward) return null;
  const is = walkForward.in_sample ?? {};
  const oos = walkForward.out_of_sample ?? {};
  const isSummary = is.summary ?? {};
  const oosSummary = oos.summary ?? {};

  return (
    <section className="algo-backtest-wf">
      <p className="algo-backtest-wf__title">
        Walk-forward ({walkForward.train_pct ?? 70}% train → OOS test)
      </p>
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
    </section>
  );
}
