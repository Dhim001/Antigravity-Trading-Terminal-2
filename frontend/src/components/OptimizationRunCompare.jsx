/**
 * Side-by-side comparison of two saved optimization runs (Tier 4).
 */
import React, { useMemo } from 'react';
import { cn } from '@/lib/utils';
import { compareOptimizationRuns, formatCompareMetric } from '@/lib/optimizationCompare';
import { formatConfigValue } from '@/lib/backtestConfigDiff';

export default function OptimizationRunCompare({ left, right, onClose }) {
  const cmp = useMemo(
    () => (left && right ? compareOptimizationRuns(left, right) : null),
    [left, right],
  );

  if (!cmp) return null;

  return (
    <section className="algo-opt-compare mt-3 rounded border border-border/60 p-2 text-xs">
      <div className="flex items-center justify-between gap-2 mb-2">
        <p className="font-semibold m-0">Optimization compare</p>
        {onClose && (
          <button type="button" className="text-muted-foreground hover:text-foreground" onClick={onClose}>
            Close
          </button>
        )}
      </div>
      {!cmp.comparable && (
        <p className="text-trading-warn mb-2">
          Symbol/strategy differ — config diff still shown; metrics may not be apples-to-apples.
        </p>
      )}
      <div className="algo-backtest-table-scroll overflow-x-auto mb-2">
        <table className="terminal-table algo-backtest-table m-0 text-xs">
          <thead>
            <tr>
              <th>Metric</th>
              <th className="text-right">Run A</th>
              <th className="text-right">Run B</th>
            </tr>
          </thead>
          <tbody>
            {cmp.metrics.map((row) => (
              <tr key={row.id}>
                <td>{row.label}</td>
                <td className="num-mono text-right">{formatCompareMetric(row.left, row.format)}</td>
                <td className="num-mono text-right">{formatCompareMetric(row.right, row.format)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {cmp.configDiff.length > 0 && (
        <>
          <p className="text-muted-foreground mb-1">Best config diff</p>
          <div className="algo-backtest-table-scroll overflow-x-auto">
            <table className="terminal-table algo-backtest-table m-0 text-xs">
              <thead>
                <tr>
                  <th>Param</th>
                  <th>Run A</th>
                  <th>Run B</th>
                </tr>
              </thead>
              <tbody>
                {cmp.configDiff.map((row) => (
                  <tr key={row.key}>
                    <td>{row.label}</td>
                    <td className="num-mono">{formatConfigValue(row.left)}</td>
                    <td className="num-mono">{formatConfigValue(row.right)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {(cmp.leftRegime?.per_regime || cmp.rightRegime?.per_regime) && (
        <p className={cn('mt-2 text-muted-foreground')}>
          Regime A: {cmp.leftRegime?.regimes_seen?.join(', ') || '—'}
          {' · '}
          Regime B: {cmp.rightRegime?.regimes_seen?.join(', ') || '—'}
        </p>
      )}
    </section>
  );
}
