/** Open the Backtest Lab right sheet on a specific tab. */

import { useResearchStore } from '../store/useResearchStore';
import { fetchBacktestRun } from '../api/endpoints';
import { isStandaloneLocation, openStandaloneWindow } from './standalonePanels';
import { toast } from 'sonner';

function setLabTabOnly(tab) {
  useResearchStore.getState().setBacktestLabTab(tab);
}

/** Open Lab as a standalone window; pass runId so the new store can hydrate results. */
export function openBacktestLabStandalone(tab = 'results') {
  const state = useResearchStore.getState();
  const runId = state.backtestResults?.run_id;
  // Empty Results is useless in a fresh window — land on Jobs instead.
  const effectiveTab = (!runId && (tab === 'results' || !tab) && !state.backtestResults)
    ? 'jobs'
    : (tab || 'results');
  state.setBacktestLabTab(effectiveTab);
  return openStandaloneWindow('backtest-lab', {
    labTab: effectiveTab,
    ...(runId ? { runId: String(runId) } : {}),
  });
}

export function openBacktestLabResults() {
  if (isStandaloneLocation()) {
    setLabTabOnly('results');
    return;
  }
  useResearchStore.getState().openBacktestLab('results');
}

export function openBacktestLabOptimizer() {
  if (isStandaloneLocation()) {
    setLabTabOnly('optimizer');
    return;
  }
  useResearchStore.getState().openBacktestLab('optimizer');
}

export function openBacktestLabJobs() {
  if (isStandaloneLocation()) {
    setLabTabOnly('jobs');
    return;
  }
  useResearchStore.getState().openBacktestLab('jobs');
}

/** Load a saved run into the store and open the Lab results tab. */
export async function openBacktestLabWithRun(runId, tab = 'results') {
  if (!runId) {
    if (isStandaloneLocation()) setLabTabOnly(tab);
    else useResearchStore.getState().openBacktestLab(tab);
    return;
  }
  const { setBacktestResults } = useResearchStore.getState();
  try {
    await fetchBacktestRun(runId, { setBacktestResults });
    if (isStandaloneLocation()) setLabTabOnly(tab);
    else useResearchStore.getState().openBacktestLab(tab);
  } catch (err) {
    toast.error(err?.message || 'Could not load backtest run');
    throw err;
  }
}
