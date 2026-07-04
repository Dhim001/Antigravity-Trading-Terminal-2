/** Open the Backtest Lab right sheet on a specific tab. */

import { useStore } from '../store/useStore';
import { fetchBacktestRun } from '../api/endpoints';

export function openBacktestLabResults() {
  useStore.getState().openBacktestLab('results');
}

export function openBacktestLabOptimizer() {
  useStore.getState().openBacktestLab('optimizer');
}

export function openBacktestLabJobs() {
  useStore.getState().openBacktestLab('jobs');
}

/** Load a saved run into the store and open the Lab results tab. */
export async function openBacktestLabWithRun(runId, tab = 'results') {
  if (!runId) {
    useStore.getState().openBacktestLab(tab);
    return;
  }
  const { setBacktestResults } = useStore.getState();
  try {
    await fetchBacktestRun(runId, { setBacktestResults });
    useStore.getState().openBacktestLab(tab);
  } catch (err) {
    useStore.getState().openBacktestLab(tab);
    throw err;
  }
}
