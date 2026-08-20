/**
 * Apply & Retrain sanitize helpers — tuned hyperparameters from a hyperparam
 * sweep before they are overlaid onto a Lab champion train.
 *
 * Sweep trials run at reduced fidelity (screen trials use epochs/5,
 * timesteps/5 on a data slice), so a tuned *budget* value is only meaningful
 * relative to that cheap proxy. Applied verbatim it undertrains the champion
 * — Apply & Retrain finishes suspiciously fast with a weaker model than a
 * plain Trigger retrain. Budget knobs are therefore floored at the Lab
 * production defaults; tuned values above the floor (deliberate upward
 * tuning) pass through unchanged. Architecture / regularization knobs
 * (hidden_dim, learning_rate, num_layers, …) always apply verbatim.
 */

import { defaultAdvancedKnobs } from '@/components/ml-lab/MlLabConstants';

/**
 * Budget ceilings keyed by their train-config name, floored at the Lab
 * Advanced defaults for the strategy (the "Trigger retrain" budget).
 */
export function tunedBudgetFloors(strategy) {
  const knobs = defaultAdvancedKnobs(strategy, 'train');
  return {
    total_timesteps: knobs.totalTimesteps,
    epochs: knobs.epochs,
    early_stop_patience: knobs.earlyStopPatience,
    gbm_max_iter: knobs.gbmMaxIter,
  };
}

/**
 * Floor the budget knobs in an already-sanitized tuned-hyperparams map.
 * Returns a new object; non-budget keys pass through untouched.
 */
export function floorTunedBudgetKnobs(strategy, hp) {
  const out = { ...(hp || {}) };
  const floors = tunedBudgetFloors(strategy);
  for (const [key, floor] of Object.entries(floors)) {
    if (!(key in out) || floor == null) continue;
    const v = Number(out[key]);
    out[key] = Number.isFinite(v) ? Math.max(v, floor) : floor;
  }
  return out;
}
