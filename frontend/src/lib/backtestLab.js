/** Open the Backtest Lab right sheet on a specific tab. */

import { useResearchStore } from '../store/useResearchStore';
import { fetchBacktestRun } from '../api/endpoints';

export function openBacktestLabResults() {
  useResearchStore.getState().openBacktestLab('results');
}

export function openBacktestLabOptimizer() {
  useResearchStore.getState().openBacktestLab('optimizer');
}

export function openBacktestLabJobs() {
  useResearchStore.getState().openBacktestLab('jobs');
}

/** Load a saved run into the store and open the Lab results tab. */
export async function openBacktestLabWithRun(runId, tab = 'results') {
  if (!runId) {
    useResearchStore.getState().openBacktestLab(tab);
    return;
  }
  const { setBacktestResults } = useResearchStore.getState();
  try {
    await fetchBacktestRun(runId, { setBacktestResults });
    useResearchStore.getState().openBacktestLab(tab);
  } catch (err) {
    useResearchStore.getState().openBacktestLab(tab);
    throw err;
  }
}
