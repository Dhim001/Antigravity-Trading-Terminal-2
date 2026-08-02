/**
 * Pure helpers for BatchTrainDialog scope selection.
 */
import { ML_STRATEGIES } from '@/components/ml-lab/MlLabConstants';
import { isModelStale } from '@/lib/modelHealth';

export const BATCH_SCOPES = ['untrained', 'stale', 'all', 'custom'];

/**
 * Select strategy ids for a scope given inventory rows.
 * @param {Array<{ strategy: string, trained?: boolean, trained_at?: string }>} inventory
 * @param {'untrained'|'stale'|'all'|'custom'} scope
 * @param {string[]} [customSelected]
 * @param {string[]} [strategyIds]
 */
export function selectStrategiesForScope(
  inventory,
  scope,
  customSelected = [],
  strategyIds = ML_STRATEGIES,
) {
  const byId = new Map((inventory || []).map((r) => [r.strategy, r]));
  const ids = strategyIds || ML_STRATEGIES;

  if (scope === 'custom') {
    return ids.filter((id) => customSelected.includes(id));
  }
  if (scope === 'all') {
    return [...ids];
  }
  if (scope === 'untrained') {
    return ids.filter((id) => {
      const row = byId.get(id);
      return !row?.trained;
    });
  }
  if (scope === 'stale') {
    return ids.filter((id) => {
      const row = byId.get(id);
      return row?.trained && isModelStale(row, 48);
    });
  }
  return [];
}

export function countStrategiesForScope(inventory, scope, strategyIds = ML_STRATEGIES) {
  return selectStrategiesForScope(inventory, scope, [], strategyIds).length;
}
