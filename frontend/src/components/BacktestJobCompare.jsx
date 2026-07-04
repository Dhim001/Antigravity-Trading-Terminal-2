/**
 * BacktestJobCompare — lightweight A/B compare from job library (no full result load).
 */
import React, { useMemo } from 'react';
import { cn } from '@/lib/utils';
import {
  BACKTEST_COMPARE_METRICS,
  formatMetricDelta,
  formatSignedValue,
  metricValue,
  resolveBacktestSummary,
  TONE_CLASS,
} from '@/lib/metricComparison';
import { diffBacktestConfigs, formatConfigValue } from '@/lib/backtestConfigDiff';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

function extractMetrics(job) {
  const res = job?.results || {};
  const summary = res.summary || {};
  return {
    summary: resolveBacktestSummary(res),
    config: job?.request?.config || res?.meta?.config || {},
    symbol: job?.request?.symbol || res?.meta?.symbol || '—',
    strategy: job?.request?.strategy || res?.meta?.strategy || '—',
    createdAt: job?.created_at,
  };
}

export default function BacktestJobCompare({ jobs = [], open, onOpenChange }) {
  const [left, right] = jobs;

  const leftData = useMemo(() => (left ? extractMetrics(left) : null), [left]);
  const rightData = useMemo(() => (right ? extractMetrics(right) : null), [right]);

  const configDiff = useMemo(() => {
    if (!leftData || !rightData) return [];
    return diffBacktestConfigs(leftData.config, rightData.config);
  }, [leftData, rightData]);

  if (!leftData || !rightData) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="algo-dialog sm:max-w-3xl" overlayClassName="admin-panel-overlay">
        <DialogHeader>
          <DialogTitle>Compare backtest runs</DialogTitle>
          <DialogDescription className="text-xs">
            {leftData.symbol} · {leftData.strategy} — metric delta and config changes
          </DialogDescription>
        </DialogHeader>

        <div className={cn(
          'algo-backtest-compare__body',
          configDiff.length > 0 && 'algo-backtest-compare__body--split',
        )}
        >
          <section className="algo-backtest-compare__metrics">
            <table className="terminal-table algo-backtest-table m-0 text-[0.58rem]">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th className="text-right">A</th>
                  <th className="text-right">B</th>
                  <th className="text-right">Δ</th>
                </tr>
              </thead>
              <tbody>
                {BACKTEST_COMPARE_METRICS.map(({ key, label, prefix = '', suffix = '', higherIsBetter }) => {
                  const a = metricValue(leftData.summary, key);
                  const b = metricValue(rightData.summary, key);
                  const { text, tone } = formatMetricDelta(a, b, { prefix, suffix, higherIsBetter });
                  return (
                    <tr key={key}>
                      <td>{label}</td>
                      <td className="num-mono text-right">{formatSignedValue(a, { prefix, suffix })}</td>
                      <td className="num-mono text-right">{formatSignedValue(b, { prefix, suffix })}</td>
                      <td className={cn('num-mono text-right', TONE_CLASS[tone])}>{text}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>

          {configDiff.length > 0 && (
            <section className="algo-backtest-compare__config">
              <p className="algo-backtest-table-scroll__caption mb-1">Config diff ({configDiff.length})</p>
              <div className="algo-backtest-table-scroll algo-backtest-table-scroll--compare-config">
                <table className="terminal-table algo-backtest-table m-0 text-[0.55rem]">
                  <thead>
                    <tr>
                      <th>Param</th>
                      <th className="text-right">A</th>
                      <th className="text-right">B</th>
                    </tr>
                  </thead>
                  <tbody>
                    {configDiff.map((row) => (
                      <tr key={row.key}>
                        <td>{row.label}</td>
                        <td className="num-mono text-right text-muted-foreground">
                          {formatConfigValue(row.left)}
                        </td>
                        <td className="num-mono text-right">
                          {formatConfigValue(row.right)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
