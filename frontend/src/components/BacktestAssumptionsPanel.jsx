/**
 * Full assumptions card — fill model, gates, data window, reproducibility.
 */
import React, { useMemo } from 'react';
import { cn } from '@/lib/utils';
import { buildBacktestAssumptionDetails } from '@/lib/backtestAssumptions';

export default function BacktestAssumptionsPanel({ results, className }) {
  const { sections } = useMemo(
    () => buildBacktestAssumptionDetails(results),
    [results],
  );

  if (!sections.length) return null;

  return (
    <section className={cn('algo-backtest-assumptions-panel algo-backtest-lab__section', className)}>
      <p className="algo-backtest-assumptions-panel__title algo-backtest-section__title">Assumptions & trust</p>
      <div className="algo-backtest-assumptions-panel__grid">
        {sections.map((section) => (
          <div key={section.id} className="algo-backtest-assumptions-panel__card">
            <p className="algo-backtest-assumptions-panel__card-title">{section.title}</p>
            <dl className="algo-backtest-assumptions-panel__rows">
              {section.rows.map((row) => (
                <div key={`${section.id}-${row.label}`} className="algo-backtest-assumptions-panel__row">
                  <dt className="text-muted-foreground">{row.label}</dt>
                  <dd className={cn(row.mono && 'num-mono', row.warn && 'text-trading-warn')}>
                    {row.value}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>
    </section>
  );
}
