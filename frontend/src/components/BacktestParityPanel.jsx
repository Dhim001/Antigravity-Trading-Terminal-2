/**
 * Live bot vs backtest parity — compares deploy-time run to live PnL.
 */
import React, { useMemo } from 'react';
import { cn } from '@/lib/utils';
import { formatMetricDelta, formatSignedValue, resolveBacktestSummary, TONE_CLASS } from '@/lib/metricComparison';
import { blockedEventRate } from '@/lib/backtestBlockedEvents';
import { useStore } from '../store/useStore';

function divergenceHints({
  pnlDrift,
  tradeDrift,
  ddDrift,
  backtestBlocked,
  blockedRate,
}) {
  const hints = [];
  if (pnlDrift.tone === 'down' && Math.abs(pnlDrift.delta ?? 0) > 50) {
    hints.push('Live PnL trails backtest — check fill model, slippage, or gate differences.');
  }
  if (tradeDrift.tone === 'down' && (tradeDrift.delta ?? 0) < -2) {
    hints.push('Fewer live trades than backtest — parity or risk gates may be blocking more often live.');
  }
  if (ddDrift.tone === 'down' && (ddDrift.delta ?? 0) > 2) {
    hints.push('Live drawdown exceeds backtest — sizing or stop behavior may differ.');
  }
  if (backtestBlocked > 5 && blockedRate != null && blockedRate > 10) {
    hints.push(`Backtest blocked ${blockedRate}% of entry attempts — live may diverge if gates differ.`);
  }
  return hints;
}

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
  const backtestSummary = resolveBacktestSummary(results);
  const backtestPnl = backtestSummary.total_pnl;
  const livePnl = liveStats?.total_pnl;
  const backtestTrades = backtestSummary.total_trades ?? results?.trade_count ?? 0;
  const liveTrades = liveStats?.total_trades ?? liveStats?.trade_count;
  const backtestBlocked = results?.summary?.blocked_entries
    ?? results?.summary?.parity_gate_blocks
    ?? 0;
  const backtestDd = backtestSummary.max_drawdown ?? results?.max_drawdown;
  const liveDd = liveStats?.max_drawdown;
  const blockedRate = blockedEventRate(results);
  const runId = results?.run_id ?? bot?.config?.backtest_run_id;

  if (backtestPnl == null && livePnl == null) return null;

  const pnlDrift = formatMetricDelta(livePnl, backtestPnl, { prefix: '$', higherIsBetter: true });
  const tradeDrift = formatMetricDelta(liveTrades, backtestTrades, { suffix: '', higherIsBetter: null });
  const ddDrift = formatMetricDelta(liveDd, backtestDd, { suffix: '%', higherIsBetter: false });
  const hints = divergenceHints({
    pnlDrift,
    tradeDrift,
    ddDrift,
    backtestBlocked,
    blockedRate,
  });

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
          <span className="text-muted-foreground">PnL drift</span>
          <strong className={cn('num-mono', TONE_CLASS[pnlDrift.tone])}>
            {pnlDrift.text}
          </strong>
        </div>
        <div className="algo-backtest-parity__cell">
          <span className="text-muted-foreground">Max DD</span>
          <strong className="num-mono">
            {backtestDd != null ? `${Number(backtestDd).toFixed(1)}%` : '—'}
            {liveDd != null ? ` → ${Number(liveDd).toFixed(1)}%` : ''}
          </strong>
        </div>
        <div className="algo-backtest-parity__cell">
          <span className="text-muted-foreground">DD drift</span>
          <strong className={cn('num-mono', TONE_CLASS[ddDrift.tone])}>
            {liveDd != null ? ddDrift.text : '—'}
          </strong>
        </div>
        <div className="algo-backtest-parity__cell">
          <span className="text-muted-foreground">Trades</span>
          <strong className="num-mono">
            {backtestTrades}
            {liveTrades != null ? ` → ${liveTrades}` : ''}
          </strong>
        </div>
        <div className="algo-backtest-parity__cell">
          <span className="text-muted-foreground">Trade drift</span>
          <strong className={cn('num-mono', TONE_CLASS[tradeDrift.tone])}>
            {liveTrades != null ? tradeDrift.text : '—'}
          </strong>
        </div>
        <div className="algo-backtest-parity__cell">
          <span className="text-muted-foreground">Blocked (BT)</span>
          <strong className="num-mono">{backtestBlocked}</strong>
          <span className="text-[0.52rem] text-muted-foreground block">
            {blockedRate != null ? `${blockedRate}% reject rate` : 'Risk + parity gates'}
          </span>
        </div>
      </div>
      {hints.length > 0 && (
        <ul className="algo-backtest-parity__hints">
          {hints.map((h) => (
            <li key={h}>{h}</li>
          ))}
        </ul>
      )}
      {runId && (
        <p className="algo-backtest-parity__meta text-muted-foreground">
          Run {String(runId).slice(0, 8)}…
          {bot?.config?.backtest_run_id ? ' · linked at deploy' : ''}
        </p>
      )}
    </section>
  );
}
