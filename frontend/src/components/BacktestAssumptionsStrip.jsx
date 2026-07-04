/**
 * Properties / assumptions strip — costs, fill model, parity gates.
 */
import React, { useMemo } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { buildBacktestAssumptions } from '@/lib/backtestAssumptions';

export default function BacktestAssumptionsStrip({ results, className }) {
  const chips = useMemo(() => buildBacktestAssumptions(results), [results]);
  if (!chips.length) return null;

  return (
    <section className={cn('algo-backtest-assumptions algo-backtest-lab__section', className)}>
      <p className="algo-backtest-assumptions__title algo-backtest-section__title">Assumptions & properties</p>
      <div className="algo-backtest-assumptions__chips">
        {chips.map((chip) => (
          <Badge
            key={chip.key}
            variant={chip.warn ? 'destructive' : 'outline'}
            className="algo-backtest-assumptions__chip"
          >
            {chip.label}
          </Badge>
        ))}
      </div>
    </section>
  );
}
