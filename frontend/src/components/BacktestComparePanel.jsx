/**
 * Backtest run comparison — current run vs a saved run (P2).
 */
import React, { useMemo, useState } from 'react';
import { cn } from '@/lib/utils';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

function fmtDelta(current, baseline, { prefix = '', suffix = '', higherIsBetter = true } = {}) {
  if (current == null || baseline == null) return '—';
  const delta = Number(current) - Number(baseline);
  if (!Number.isFinite(delta) || Math.abs(delta) < 1e-9) return '±0';
  const sign = delta > 0 ? '+' : '';
  const tone = higherIsBetter
    ? (delta > 0 ? 'text-trading-up' : 'text-trading-down')
    : (delta < 0 ? 'text-trading-up' : 'text-trading-down');
  return (
    <span className={cn('num-mono', tone)}>
      {sign}{prefix}{delta.toFixed(2)}{suffix}
    </span>
  );
}

const METRICS = [
  { key: 'total_pnl', label: 'PnL', prefix: '$', higherIsBetter: true },
  { key: 'return_pct', label: 'Return', suffix: '%', higherIsBetter: true },
  { key: 'win_rate', label: 'Win rate', suffix: '%', higherIsBetter: true },
  { key: 'max_drawdown', label: 'Max DD', suffix: '%', higherIsBetter: false },
  { key: 'profit_factor', label: 'Profit factor', higherIsBetter: true },
  { key: 'sharpe_ratio', label: 'Sharpe', higherIsBetter: true },
];

function metricValue(summary, key) {
  const v = summary?.[key];
  return v == null ? null : Number(v);
}

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

  if (!currentRun || candidates.length === 0) return null;

  const currentSummary = currentRun.summary ?? {};

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
              <th className="text-right">Δ</th>
            </tr>
          </thead>
          <tbody>
            {METRICS.map(({ key, label, prefix = '', suffix = '', higherIsBetter }) => {
              const cur = metricValue(currentSummary, key);
              const base = metricValue(baseline.summary, key);
              return (
                <tr key={key}>
                  <td>{label}</td>
                  <td className="num-mono text-right">
                    {cur != null ? `${prefix}${cur.toFixed(2)}${suffix}` : '—'}
                  </td>
                  <td className="num-mono text-right text-muted-foreground">
                    {base != null ? `${prefix}${base.toFixed(2)}${suffix}` : '—'}
                  </td>
                  <td className="text-right">
                    {fmtDelta(cur, base, { prefix, suffix, higherIsBetter })}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
