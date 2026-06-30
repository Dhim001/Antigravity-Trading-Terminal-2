/**
 * Live bot vs backtest parity — compares deploy-time run to live PnL.
 */
import React, { useMemo } from 'react';
import { cn } from '@/lib/utils';
import { formatMetricDelta, formatSignedValue, resolveBacktestSummary, TONE_CLASS } from '@/lib/metricComparison';
import { useStore } from '../store/useStore';

export default function BacktestParityPanel({ results, symbol, strategy }) {
  const activeBots = useStore((s) => s.activeBots) ?? [];
  const botDetail = useStore((s) => s.botDetail);

  const bot = useMemo(() => {
    const sym = (symbol ?? results?.meta?.symbol ?? '').toUpperCase();
    const strat = strategy ?? results?.meta?.strategy;
    return activeBots.find(
      (b) => b.symbol?.toUpperCase() === sym && (!strat || b.strategy === strat),
    ) ?? null;
  }, [activeBots, symbol, strategy, results]);

  const liveStats = botDetail?.bot?.id === bot?.id ? botDetail?.stats : null;
  const backtestPnl = resolveBacktestSummary(results).total_pnl;
  const livePnl = liveStats?.total_pnl;
  const runId = results?.run_id ?? bot?.config?.backtest_run_id;

  if (backtestPnl == null && livePnl == null) return null;

  const drift = formatMetricDelta(livePnl, backtestPnl, { prefix: '$', higherIsBetter: true });

  return (
    <section className="algo-backtest-parity">
      <p className="algo-backtest-parity__title">Live vs backtest</p>
      <div className="algo-backtest-parity__grid">
        <div className="algo-backtest-parity__cell">
          <span className="text-muted-foreground">Backtest PnL</span>
          <strong className={cn(
            'num-mono',
            (backtestPnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down',
          )}>
            {formatSignedValue(backtestPnl, { prefix: '$' })}
          </strong>
        </div>
        <div className="algo-backtest-parity__cell">
          <span className="text-muted-foreground">Live PnL</span>
          <strong className={cn(
            'num-mono',
            livePnl == null ? '' : (livePnl >= 0 ? 'text-trading-up' : 'text-trading-down'),
          )}>
            {livePnl != null ? formatSignedValue(livePnl, { prefix: '$' }) : (bot ? 'No fills yet' : 'No live bot')}
          </strong>
        </div>
        <div className="algo-backtest-parity__cell">
          <span className="text-muted-foreground">Drift</span>
          <strong className={cn('num-mono', TONE_CLASS[drift.tone])}>
            {drift.text}
          </strong>
        </div>
      </div>
      {runId && (
        <p className="algo-backtest-parity__meta text-muted-foreground">
          Run {String(runId).slice(0, 8)}…
          {bot?.config?.backtest_run_id ? ' · linked at deploy' : ''}
        </p>
      )}
    </section>
  );
}
