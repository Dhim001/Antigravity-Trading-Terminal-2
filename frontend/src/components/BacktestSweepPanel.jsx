/**
 * Category-aware optimizer dispatcher for Backtest Lab.
 */
import { Suspense } from 'react';
import { getStrategyCategory } from '../config/strategies';
import { lazyImport } from '../lib/lazyImport';

const TaOptimizerPanel = lazyImport(() => import('./TaOptimizerPanel'), 'ta-optimizer');
const MlOptimizerPanel = lazyImport(() => import('./MlOptimizerPanel'), 'ml-optimizer');
const AgentOptimizerPanel = lazyImport(() => import('./AgentOptimizerPanel'), 'agent-optimizer');

function OptimizerFallback() {
  return <p className="backtest-lab__loading px-3 pt-2">Loading optimizer…</p>;
}

export default function BacktestSweepPanel({ strategyCategory, ...props }) {
  const category = strategyCategory ?? getStrategyCategory(props.strategy);

  const Panel = category === 'ml'
    ? MlOptimizerPanel
    : category === 'agent'
      ? AgentOptimizerPanel
      : TaOptimizerPanel;

  return (
    <Suspense fallback={<OptimizerFallback />}>
      <Panel {...props} />
    </Suspense>
  );
}
