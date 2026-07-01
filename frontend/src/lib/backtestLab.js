/** Open the Backtest Lab right sheet on a specific tab. */

import { useStore } from '../store/useStore';

export function openBacktestLabResults() {
  useStore.getState().openBacktestLab('results');
}

export function openBacktestLabOptimizer() {
  useStore.getState().openBacktestLab('optimizer');
}

export function openBacktestLabJobs() {
  useStore.getState().openBacktestLab('jobs');
}
