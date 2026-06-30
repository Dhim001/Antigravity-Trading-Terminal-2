/**
 * Backtest run comparison — current run vs a saved run (P2).
 */
import React, { useMemo, useState } from 'react';
import { cn } from '@/lib/utils';
import {
  BACKTEST_COMPARE_METRICS,
  formatMetricDelta,
  formatSignedValue,
  metricValue,
  resolveBacktestSummary,
  TONE_CLASS,
} from '@/lib/metricComparison';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

export default function BacktestComparePanel({ currentRun, recentRuns = [] }) {
  const candidates = useMemo(
    () => recentRuns.filter((r) => r.id !== currentRun?.run_id),
    [recentRuns, currentRun?.run_id],
  );

  const [compareId, setCompareId] = useState(candidates[0]?.id ?? '');

  const baseline = useMemo(
    () => candidates.find((r) => r.id === compareId) ?? null,
    [candidates, compareId],
  );

  const currentSummary = useMemo(
    () => resolveBacktestSummary(currentRun?.results ?? currentRun),
    [currentRun],
  );

  const baselineSummary = useMemo(
    () => resolveBacktestSummary(baseline),
    [baseline],
  );

  if (!currentRun || candidates.length === 0) return null;

  return (
    <div className="algo-backtest-compare">
      <div className="algo-backtest-compare__header">
        <span className="algo-backtest-table-scroll__caption m-0">Run comparison</span>
        <Select value={compareId || undefined} onValueChange={setCompareId}>
          <SelectTrigger className="h-7 w-[11rem] text-[0.62rem]" size="sm">
            <SelectValue placeholder="Compare to…" />
          </SelectTrigger>
          <SelectContent>
            {candidates.map((run) => (
              <SelectItem key={run.id} value={run.id} className="text-xs">
                {run.created_at?.slice(0, 16) ?? run.id.slice(0, 8)} · {run.strategy}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {baseline && (
        <table className="terminal-table algo-backtest-table m-0 text-[0.58rem]">
          <thead>
            <tr>
              <th>Metric</th>
              <th className="text-right">Current</th>
              <th className="text-right">Baseline</th>
              <th className="text-right">Δ vs baseline</th>
            </tr>
          </thead>
          <tbody>
            {BACKTEST_COMPARE_METRICS.map(({ key, label, prefix = '', suffix = '', higherIsBetter }) => {
              const cur = metricValue(currentSummary, key);
              const base = metricValue(baselineSummary, key);
              const { text, tone } = formatMetricDelta(cur, base, {
                prefix,
                suffix,
                higherIsBetter,
              });
              const curTone = (key === 'total_pnl' || key === 'return_pct')
                ? ((cur ?? 0) >= 0 ? 'up' : 'down')
                : 'neutral';
              return (
                <tr key={key}>
                  <td>{label}</td>
                  <td className={cn('num-mono text-right', TONE_CLASS[curTone] ?? TONE_CLASS.neutral)}>
                    {formatSignedValue(cur, { prefix, suffix })}
                  </td>
                  <td className="num-mono text-right text-muted-foreground">
                    {formatSignedValue(base, { prefix, suffix })}
                  </td>
                  <td className={cn('num-mono text-right', TONE_CLASS[tone])}>
                    {text}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <p className="algo-backtest-compare__hint text-[0.58rem] text-muted-foreground">
        Δ is current minus baseline — green means better on that metric (e.g. −$200 vs −$800 is +$600).
      </p>
    </div>
  );
}
